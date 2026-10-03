"""An OpenAI-Realtime-compatible WebSocket server in front of Groq.

The iPhone app speaks OpenAI's Realtime protocol. When the saved key is a Groq key
the desktop points the phone here instead, and this translates the handful of events
the app uses into Groq chat and transcription calls. The phone still runs the tools
over the ordinary TCP link and sends the results back, so all this has to do is
carry audio in and text (plus tool calls) out.

Events the phone needs, and what we answer:

===========================  ====================================================
``input_audio_buffer.append``  buffer 24 kHz mono PCM16
``input_audio_buffer.commit``  → ``input_audio_buffer.committed`` + transcription
``response.create``            → ``response.created``, text deltas, ``response.done``
``conversation.item.create``   tool results and screenshots from the phone
===========================  ====================================================
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import secrets
import threading
import uuid
from typing import Any, Callable

from . import groq

try:  # websockets 13+
    from websockets.asyncio.server import serve
except ImportError:  # pragma: no cover - older websockets
    from websockets.server import serve  # type: ignore[no-redef]

log = logging.getLogger(__name__)

PATH = "/v1/realtime"
MAX_HISTORY = 24
CONTEXT_CHUNK_SECONDS = 20
MAX_BUFFER_SECONDS = 90
BYTES_PER_SECOND = 2 * groq.WIRE_SAMPLE_RATE  # PCM16 mono at 24 kHz
MAX_TOOL_OUTPUT = 20000
MAX_TOOL_CALLS = 4


def _id(prefix: str) -> str:
    return prefix + "_" + uuid.uuid4().hex[:16]


def _pieces(text: str, size: int = 3) -> list[str]:
    """Split a reply into bubble-sized pieces so the phone can stream it."""
    words = text.split(" ")
    return [" ".join(words[index:index + size]) + " " for index in range(0, len(words), size)]


class PhoneSession:
    """One phone's conversation: its audio buffer, its history, and the tools."""

    def __init__(self, *, api_key: str, tools: list[dict], instructions: str):
        self.api_key = api_key
        self.tools = tools
        self.system = instructions
        self.websocket: Any = None
        self.messages: list[dict] = []
        self.audio = bytearray()
        self.pending: asyncio.Task | None = None
        self.context: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self.notes_images = True

    # MARK: Talking to the phone

    async def send(self, event: dict) -> None:
        try:
            await self.websocket.send(json.dumps(event))
        except Exception:
            log.debug("Realtime proxy could not send %s", event.get("type"))

    # MARK: Audio

    def on_audio(self, audio: str) -> None:
        """Buffer a mic frame; transcribe and drop old speech so context stays fresh."""
        try:
            chunk = base64.b64decode(audio, validate=True)
        except Exception:
            return
        if not chunk:
            return
        self.audio.extend(chunk)
        ceiling = MAX_BUFFER_SECONDS * BYTES_PER_SECOND
        if len(self.audio) > ceiling:
            del self.audio[:len(self.audio) - ceiling]
        limit = CONTEXT_CHUNK_SECONDS * BYTES_PER_SECOND
        if len(self.audio) >= limit and (self.context is None or self.context.done()):
            oldest = bytes(self.audio[:limit])
            del self.audio[:limit]
            self.context = asyncio.ensure_future(self._remember(oldest))

    async def _remember(self, pcm: bytes) -> None:
        """Background speech becomes context, exactly like the Mac's server VAD did."""
        try:
            text = await asyncio.to_thread(groq.transcribe, self.api_key, pcm)
        except groq.APIError as error:
            log.debug("Background transcription failed: %s", error)
            return
        if text:
            self._remembered(text)

    def _remembered(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
        self._trim()

    def _trim(self) -> None:
        """Keep the history bounded; the newest turns matter most."""
        if len(self.messages) > MAX_HISTORY:
            del self.messages[:len(self.messages) - MAX_HISTORY]

    async def on_commit(self) -> None:
        """The user let go of the screen: this stretch of speech is the question."""
        item = _id("item")
        pcm = bytes(self.audio)
        self.audio.clear()
        await self.send({"type": "input_audio_buffer.committed", "item_id": item})
        self.pending = asyncio.ensure_future(self._transcribe(item, pcm))

    async def _transcribe(self, item: str, pcm: bytes) -> None:
        try:
            text = await asyncio.to_thread(groq.transcribe, self.api_key, pcm)
        except groq.APIError as error:
            log.debug("Transcription failed: %s", error)
            await self.send({"type": "error", "error": {"code": "transcription_failed", "message": str(error)}})
            text = ""
        if text:
            self._remembered(text)
        await self.send({"type": "conversation.item.input_audio_transcription.completed",
                         "item_id": item, "transcript": text})

    # MARK: Replying

    def on_item(self, item: Any) -> None:
        """Tool results and screenshots the phone sends back."""
        if not isinstance(item, dict):
            return
        if item.get("type") == "function_call_output":
            self.messages.append({"role": "tool", "tool_call_id": str(item.get("call_id", "")),
                                  "content": str(item.get("output", ""))[:MAX_TOOL_OUTPUT]})
            self._trim()
        elif item.get("type") == "message":
            self.on_media(item)

    def on_media(self, item: dict) -> None:
        content = item.get("content")
        if not isinstance(content, list):
            return
        images = [part["image_url"] for part in content
                  if isinstance(part, dict) and isinstance(part.get("image_url"), str)
                  and part["image_url"].startswith("data:image/") and len(part["image_url"]) < 6000000]
        if not images:
            return
        if not groq.supports_images(groq.CHAT_MODEL):
            if self.notes_images:
                log.info("Screenshots skipped: %s cannot see images. Set BLUEY_GROQ_MODEL to a vision model.",
                         groq.CHAT_MODEL)
                self.notes_images = False
            return
        self.messages.append({"role": "user", "content": [
            {"type": "text", "text": "Here is the screen."},
            *({"type": "image_url", "image_url": {"url": url}} for url in images)]})

    async def respond(self, event: dict) -> None:
        async with self.lock:
            await self.finish()
            response = event.get("response")
            extra = response.get("instructions") if isinstance(response, dict) else None
            messages: list[dict] = [{"role": "system", "content": self.system}]
            if isinstance(extra, str) and extra.strip():
                messages.append({"role": "system", "content": extra.strip()[:2000]})
            messages.extend(self.messages)
            try:
                reply = await asyncio.to_thread(groq.chat, self.api_key, messages, self.tools)
            except groq.APIError as error:
                await self.send({"type": "error", "error": {"code": "groq_error", "message": str(error)}})
                return
            await self.deliver(reply)

    async def finish(self) -> None:
        """A reply may only use speech that has finished being transcribed."""
        for task in (self.pending, self.context):
            if task is not None and not task.done():
                with contextlib.suppress(Exception):
                    await task

    async def deliver(self, reply: dict) -> None:
        response = _id("resp")
        await self.send({"type": "response.created",
                         "response": {"id": response, "status": "in_progress", "output": []}})
        output: list[dict] = []
        text = reply.get("text") or ""
        if text:
            item = _id("item")
            output.append({"id": item, "type": "message", "role": "assistant", "status": "completed",
                           "content": [{"type": "output_text", "text": text}]})
            await self.send({"type": "response.output_item.added", "output_index": 0,
                             "item": {"id": item, "type": "message", "role": "assistant", "content": []}})
            await self.send({"type": "response.content_part.added", "item_id": item, "output_index": 0,
                             "content_index": 0, "part": {"type": "output_text", "text": ""}})
            for piece in _pieces(text):
                await self.send({"type": "response.output_text.delta", "item_id": item, "output_index": 0,
                                 "content_index": 0, "delta": piece})
                await asyncio.sleep(0.03)
            await self.send({"type": "response.output_text.done", "item_id": item, "output_index": 0,
                             "content_index": 0, "text": text})
            await self.send({"type": "response.content_part.done", "item_id": item, "output_index": 0,
                             "content_index": 0, "part": {"type": "output_text", "text": text}})
            await self.send({"type": "response.output_item.done", "output_index": 0, "item": output[-1]})
        calls = (reply.get("tool_calls") or [])[:MAX_TOOL_CALLS]
        for call in calls:
            output.append({"type": "function_call", "name": call["name"], "call_id": call["id"],
                           "arguments": call["arguments"], "status": "completed"})
        await self.send({"type": "response.done",
                         "response": {"id": response, "status": "completed", "output": output}})
        self.record(text, calls)
        if not text and not calls:
            await self.send({"type": "error", "error": {"code": "empty_response",
                                                         "message": "Groq returned nothing. Try again."}})

    def record(self, text: str, calls: list[dict]) -> None:
        if calls:
            self.messages.append({"role": "assistant", "content": text or None, "tool_calls": [
                {"id": call["id"], "type": "function",
                 "function": {"name": call["name"], "arguments": call["arguments"]}} for call in calls]})
        elif text:
            self.messages.append({"role": "assistant", "content": text})
        self._trim()

    # MARK: Event loop

    @staticmethod
    def parse(raw: Any) -> dict | None:
        try:
            event = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        except Exception:
            return None
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            return None
        return event

    async def run(self, websocket: Any) -> None:
        self.websocket = websocket
        try:
            async for raw in websocket:
                event = self.parse(raw)
                if not event:
                    continue
                kind = event.get("type")
                if kind == "input_audio_buffer.append":
                    self.on_audio(str(event.get("audio", "")))
                elif kind == "input_audio_buffer.commit":
                    await self.on_commit()
                elif kind == "response.create":
                    await self.respond(event)
                elif kind == "conversation.item.create":
                    self.on_item(event.get("item"))
        except Exception as error:
            log.info("Realtime proxy session ended: %s", error)
        finally:
            # The phone may hang up the instant it lets go: finish hearing the words.
            await self.finish()
            for task in (self.pending, self.context):
                if task is not None:
                    task.cancel()


class RealtimeProxy:
    """Serves the phone on the LAN and mints the token it has to present."""

    def __init__(self, *, api_key: Callable[[], str], tools: Callable[[], list[dict]],
                 instructions: Callable[[], str], host: str = "0.0.0.0"):
        self._api_key = api_key
        self._tools = tools
        self._instructions = instructions
        self.host = host
        self.port: int | None = None
        self._token = secrets.token_urlsafe(18)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: Any = None
        self._thread: threading.Thread | None = None

    def token(self) -> str:
        return self._token

    def url_for(self, host: str) -> str:
        """The ws:// address the phone should use, for a phone seen at `host`."""
        if not self.port:
            raise RuntimeError("The realtime proxy is not running.")
        return f"ws://{host}:{self.port}{PATH}"

    def start(self, port: int = 0, timeout: float = 10.0) -> None:
        if self._thread is not None:
            return
        ready = threading.Event()

        async def main() -> None:
            self._server = await serve(self._dispatch, self.host, port)
            self.port = int(self._server.sockets[0].getsockname()[1])
            ready.set()
            await asyncio.Future()

        def run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                loop.run_until_complete(main())
            except (asyncio.CancelledError, RuntimeError):
                pass
            finally:
                with contextlib.suppress(Exception):
                    loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()

        self._thread = threading.Thread(target=run, name="bluey-realtime", daemon=True)
        self._thread.start()
        if not ready.wait(timeout):
            raise RuntimeError("The realtime proxy did not start.")

    async def _dispatch(self, websocket: Any, path: str | None = None) -> None:
        """New websockets passes one argument, older ones also pass the path."""
        request = getattr(websocket, "request", None)
        headers = getattr(request, "headers", None) or getattr(websocket, "request_headers", None) or {}
        try:
            auth = headers.get("Authorization", "")
        except Exception:
            auth = ""
        if not auth.startswith("Bearer ") or not secrets.compare_digest(auth[7:].strip(), self._token):
            with contextlib.suppress(Exception):
                await websocket.close(code=1008, reason="unknown token")
            return
        log.info("Realtime proxy: phone session started.")
        session = PhoneSession(api_key=self._api_key(), tools=self._tools(),
                               instructions=self._instructions())
        await session.run(websocket)

    def stop(self) -> None:
        loop, self._loop = self._loop, None
        self._thread, self.port, self._server = None, None, None
        if loop is None:
            return

        async def shutdown() -> None:
            if self._server is not None:
                with contextlib.suppress(Exception):
                    self._server.close()
            loop.stop()

        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(shutdown(), loop=loop))