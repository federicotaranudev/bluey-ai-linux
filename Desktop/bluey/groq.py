"""Groq instead of OpenAI: free-tier transcription and chat with tools.

The phone app speaks OpenAI's Realtime protocol (see realtime_proxy), but the model
itself can be anything. Groq needs no payment details, so this is what the desktop
uses when the saved key is a Groq key.
"""

from __future__ import annotations

import json
import math
import os
import struct
import uuid
from array import array
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

GROQ_BASE = "https://api.groq.com/openai/v1"
# Cloudflare in front of Groq refuses the default Python user agent (error 1010),
# so every request identifies itself like an ordinary client.
USER_AGENT = "bluey-desktop/0.1"
# gpt-oss 120B calls tools reliably and is available on the free tier.
CHAT_MODEL = os.environ.get("BLUEY_GROQ_MODEL", "openai/gpt-oss-120b")
STT_MODEL = os.environ.get("BLUEY_GROQ_STT_MODEL", "whisper-large-v3-turbo")
# The phone converts its microphone to 24 kHz mono 16-bit PCM before it reaches us.
WIRE_SAMPLE_RATE = 24000
MAX_AUDIO_BYTES = 25 * 1024 * 1024


class APIError(RuntimeError):
    """A safe, user-facing API failure with no secret in the message."""


def _error_message(code: int) -> str:
    return {
        400: "Groq could not accept the request.",
        401: "Groq rejected the API key.",
        403: "This Groq key cannot use the requested model.",
        404: "That Groq model or endpoint is unavailable.",
        413: "The recording was too long. Hold to talk for less time.",
        429: "Groq's free rate limit was reached. Try again in a moment.",
    }.get(code, f"Groq could not complete the request (HTTP {code}).")


def _post(path: str, api_key: str, body: bytes, content_type: str, timeout: int,
          raw_response: bool = False) -> Any:
    key = api_key.strip()
    if not key:
        raise APIError("Add a Groq API key in Bluey's settings first.")
    if len(key) > 512 or any(ord(char) <= 32 or ord(char) > 126 for char in key):
        raise APIError("The Groq API key contains invalid characters.")
    request = Request(GROQ_BASE + path, data=body, method="POST",
                      headers={"Authorization": f"Bearer {key}", "User-Agent": USER_AGENT,
                               "Content-Type": content_type})
    try:
        with build_opener().open(request, timeout=timeout) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
    except HTTPError as error:
        code = error.code
        error.close()
        raise APIError(_error_message(code)) from None
    except (TimeoutError, OSError) as error:
        raise APIError("Could not reach Groq. Check your internet connection.") from error
    if raw_response:
        return raw
    try:
        result = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise APIError("Groq returned an unreadable response. Try again.") from None
    if not isinstance(result, dict) or result.get("error"):
        raise APIError("Groq could not complete the request. Check API access.")
    return result


def wav_bytes(pcm: bytes, sample_rate: int = WIRE_SAMPLE_RATE) -> bytes:
    """Wrap raw PCM16 mono samples in the WAV header the transcriptions API wants."""
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
    header += struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
    return header + b"data" + struct.pack("<I", len(pcm)) + pcm


def _multipart(fields: dict[str, str], filename: str, content: bytes, content_type: str) -> tuple[bytes, str]:
    boundary = "----bluey" + uuid.uuid4().hex
    chunks = []
    for name, value in fields.items():
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                  f"Content-Type: {content_type}\r\n\r\n".encode())
    chunks.append(content)
    chunks.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def is_silent(pcm: bytes, threshold: float = 500.0) -> bool:
    """Whisper invents sentences for silence, so quiet audio is never worth sending."""
    samples = array("h")
    usable = len(pcm) - (len(pcm) % 2)
    if usable <= 0:
        return True
    samples.frombytes(pcm[:usable])
    step = max(1, len(samples) // 4000)
    total = count = 0
    for index in range(0, len(samples), step):
        total += samples[index] * samples[index]
        count += 1
    return math.sqrt(total / max(1, count)) < threshold


def transcribe(api_key: str, pcm: bytes, *, model: str | None = None,
               sample_rate: int = WIRE_SAMPLE_RATE, timeout: int = 45) -> str:
    """Speech to text for one stretch of speech. Empty or silent audio returns nothing."""
    if not pcm or is_silent(pcm):
        return ""
    if len(pcm) > MAX_AUDIO_BYTES:
        raise APIError("The recording was too long. Hold to talk for less time.")
    body, content_type = _multipart(
        {"model": model or STT_MODEL, "response_format": "json", "temperature": "0"},
        "speech.wav", wav_bytes(pcm, sample_rate), "audio/wav")
    result = _post("/audio/transcriptions", api_key, body, content_type, timeout)
    text = result.get("text") if isinstance(result, dict) else None
    return " ".join(str(text).split()) if isinstance(text, str) else ""


def as_chat_tools(realtime_tools: list[dict]) -> list[dict]:
    """Convert the session's Realtime tool definitions to the chat completions shape."""
    tools = []
    for tool in realtime_tools:
        tools.append({"type": "function", "function": {
            "name": tool.get("name", ""), "description": tool.get("description", ""),
            "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
        }})
    return tools


def supports_images(model: str) -> bool:
    """Only Groq's vision models can look at a screenshot; the text ones cannot."""
    return "llama-4" in model or "vision" in model


def chat(api_key: str, messages: list[dict], tools: list[dict] | None = None, *,
         model: str | None = None, temperature: float = 0.6, max_tokens: int = 512,
         timeout: int = 90) -> dict:
    """One assistant turn. Returns the reply text and any tool calls it wants."""
    payload: dict[str, Any] = {
        "model": model or CHAT_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    result = _post("/chat/completions", api_key,
                   json.dumps(payload, allow_nan=False).encode("utf-8"),
                   "application/json", timeout)
    choices = result.get("choices") or []
    message = choices[0].get("message") if choices and isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise APIError("Groq returned no reply. Try again.")
    calls = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") if isinstance(call, dict) else None
        if not isinstance(function, dict) or not function.get("name"):
            continue
        arguments = function.get("arguments")
        calls.append({"id": str(call.get("id") or "call_" + uuid.uuid4().hex[:12]),
                      "name": str(function["name"]),
                      "arguments": arguments if isinstance(arguments, str) and arguments else "{}"})
    text = message.get("content")
    return {"text": " ".join(str(text).split()) if isinstance(text, str) else "", "tool_calls": calls}