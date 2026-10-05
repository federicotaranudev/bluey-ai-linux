from __future__ import annotations

"""Bluey in the browser: talk to him from any phone or computer on the network.

The desktop app is the full experience (he sees your screen, clicks things).
This is the travelling version: hold to talk, he listens and answers, nothing
touches your computer. The Groq key stays on this server and is never sent to
the browser, so the page can be opened on any device on your Wi-Fi.
"""

import base64
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import groq
from .settings import Credentials


def lan_address() -> str:
    """This machine's address on the home network, so a phone can find the page."""
    import socket
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))  # a public address that is never actually reached
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()

HERE = Path(__file__).resolve().parent
PAGE = HERE / "web" / "index.html"
PORT = int(os.environ.get("BLUEY_WEB_PORT", "8790"))
MAX_BODY = 32 * 1024 * 1024

SYSTEM_PROMPT = (
    "You are Bluey, a small friendly berry-shaped character living on someone's "
    "computer. You are warm, curious and brief. Answer in one to three short "
    "sentences unless asked for more. You cannot see the screen or control the "
    "computer in this version, so never claim that you did."
)


def groq_provider(key: str) -> str:
    return "groq" if key.strip().startswith("gsk_") else "openai"


class WebHandler(BaseHTTPRequestHandler):
    server_version = "BlueyWeb/0.1"
    credentials: Credentials
    history: list[dict] = []
    history_lock = threading.Lock()

    def log_message(self, format: str, *args) -> None:  # quieter than the default
        pass

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

    def _error(self, code: int, message: str) -> None:
        self._json(code, {"error": message})

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            try:
                page = PAGE.read_bytes()
            except OSError:
                self._error(500, "The web page is missing from this install.")
                return
            self._send(200, page, "text/html; charset=utf-8")
        elif self.path == "/api/status":
            key = self.credentials.get()
            self._json(200, {"ready": bool(key), "provider": groq_provider(key),
                             "model": groq.CHAT_MODEL, "stt": groq.STT_MODEL})
        else:
            self._error(404, "Not found.")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            self._error(413, "That recording is too large. Hold to talk for less time.")
            return
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeError):
            self._error(400, "Could not read the request.")
            return
        if not isinstance(payload, dict):
            self._error(400, "Could not read the request.")
            return

        key = self.credentials.get()
        if not key:
            self._error(401, "Add a Groq API key in the Bluey desktop app first.")
            return

        try:
            if self.path == "/api/transcribe":
                self._transcribe(key, payload)
            elif self.path == "/api/chat":
                self._chat(key, payload)
            else:
                self._error(404, "Not found.")
        except groq.APIError as error:
            self._error(502, str(error))
        except Exception:
            self._error(500, "Something went wrong on the server.")

    def _transcribe(self, key: str, payload: dict) -> None:
        raw = payload.get("pcm_base64")
        if not isinstance(raw, str) or not raw:
            self._error(400, "No audio was recorded.")
            return
        try:
            pcm = base64.b64decode(raw, validate=True)
        except Exception:
            self._error(400, "The audio could not be read.")
            return
        try:
            rate = int(payload.get("sample_rate") or groq.WIRE_SAMPLE_RATE)
        except (TypeError, ValueError):
            rate = groq.WIRE_SAMPLE_RATE
        rate = max(8000, min(48000, rate))
        text = groq.transcribe(key, pcm, sample_rate=rate)
        self._json(200, {"text": text})

    def _chat(self, key: str, payload: dict) -> None:
        said = payload.get("text")
        if not isinstance(said, str) or not said.strip():
            self._error(400, "There was nothing to answer.")
            return
        said = said.strip()[:4000]
        with self.history_lock:
            self.history.append({"role": "user", "content": said})
            self.history[:] = self.history[-12:]  # keep the context small
            messages = [{"role": "system", "content": SYSTEM_PROMPT}, *self.history]
        result = groq.chat(key, messages, temperature=0.7, max_tokens=320)
        reply = result["text"]
        with self.history_lock:
            self.history.append({"role": "assistant", "content": reply or "(no answer)"})
        self._json(200, {"text": reply})


def main() -> int:
    handler = type("BoundWebHandler", (WebHandler,), {"credentials": Credentials()})
    if not handler.credentials.get():
        print("No saved Groq key yet. Add one in the Bluey desktop app, then run this again.")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), handler)
    print(f"Bluey is awake at  http://{lan_address()}:{PORT}")
    print("Open that address in any browser on this Wi-Fi (phone, tablet, laptop).")
    print("Press Ctrl+C to put him back to sleep.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nGoing to sleep.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())