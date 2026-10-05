"""The browser version: same Groq brain, but a web page instead of a window."""

import base64
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from bluey import web


class FakeCredentials:
    def __init__(self, key="gsk_test"):
        self.key = key

    def get(self):
        return self.key


def serve(key="gsk_test"):
    handler = type("H", (web.WebHandler,), {"credentials": FakeCredentials(key), "history": []})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def post(server, path, payload):
    body = json.dumps(payload).encode()
    request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}{path}", data=body,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_the_page_is_served_so_a_phone_can_open_it():
    server = serve()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=30) as response:
            page = response.read().decode()
        assert response.status == 200
        assert "<title>Bluey" in page
    finally:
        server.shutdown()


def test_the_status_never_leaks_the_key():
    server = serve()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/status", timeout=30) as response:
            body = response.read().decode()
        assert "gsk_test" not in body
        assert json.loads(body)["ready"] is True
    finally:
        server.shutdown()


def test_without_a_key_the_browser_is_told_why():
    server = serve(key="")
    try:
        code, body = post(server, "/api/chat", {"text": "hello"})
        assert code == 401
        assert "key" in body["error"].lower()
    finally:
        server.shutdown()


def test_a_recording_of_only_silence_is_never_sent_to_whisper():
    server = serve()
    try:
        silence = base64.b64encode(b"\x00\x00" * 24000).decode()
        code, body = post(server, "/api/transcribe", {"pcm_base64": silence})
        assert code == 200
        assert body["text"] == ""  # no invented sentence from an empty recording
    finally:
        server.shutdown()


def test_a_turn_is_remembered_for_the_next_question():
    server = serve()
    seen = []

    def fake_chat(key, messages, **kwargs):
        seen.append([m["content"] for m in messages])
        return {"text": "I remember.", "tool_calls": []}

    original, web.groq.chat = web.groq.chat, fake_chat
    try:
        post(server, "/api/chat", {"text": "What is your name?"})
        post(server, "/api/chat", {"text": "What did I ask?"})
        assert "What is your name?" in seen[1]
    finally:
        web.groq.chat = original
        server.shutdown()


def test_nonsense_never_reaches_groq():
    server = serve()
    try:
        code, _ = post(server, "/api/chat", {"text": "   "})
        assert code == 400
    finally:
        server.shutdown()