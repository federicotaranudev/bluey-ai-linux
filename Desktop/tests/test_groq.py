"""Groq translation: keys, WAV wrapping, tool shapes and the chat reply."""

import json
import unittest
from unittest.mock import patch

from bluey import groq, realtime
from bluey.settings import provider_for_key


class Response:
    def __init__(self, payload):
        self.payload = payload

    def read(self, limit=-1):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class GroqTests(unittest.TestCase):
    def test_keys_pick_the_right_provider(self):
        self.assertEqual(provider_for_key("gsk_abc123"), "groq")
        self.assertEqual(provider_for_key("  gsk_abc123 "), "groq")
        self.assertEqual(provider_for_key("sk-proj-abc"), "openai")
        self.assertEqual(provider_for_key(""), "openai")

    def test_wav_header_describes_the_pcm(self):
        wav = groq.wav_bytes(b"\x00\x01" * 100, sample_rate=24000)
        self.assertEqual(wav[:4], b"RIFF")
        self.assertEqual(wav[8:12], b"WAVE")
        self.assertEqual(len(wav), 44 + 200)
        channels, rate = int.from_bytes(wav[22:24], "little"), int.from_bytes(wav[24:28], "little")
        self.assertEqual((channels, rate), (1, 24000))

    def test_transcription_posts_multipart_and_returns_text(self):
        seen = {}

        def open_url(request, timeout=None):
            seen["url"] = request.full_url
            seen["body"] = request.data
            seen["type"] = request.headers["Content-type"]
            return Response(json.dumps({"text": "  what's   this? "}).encode())

        with patch("bluey.groq.build_opener") as opener:
            opener.return_value.open.side_effect = lambda request, timeout=None: open_url(request)
            text = groq.transcribe("gsk_test", b"\x00\x01" * 50)
        self.assertEqual(text, "what's this?")
        self.assertTrue(seen["url"].endswith("/audio/transcriptions"))
        self.assertIn("multipart/form-data", seen["type"])
        self.assertIn(b'name="model"', seen["body"])
        self.assertIn(b"whisper", seen["body"])

    def test_empty_audio_never_reaches_the_network(self):
        with patch("bluey.groq._post") as post:
            self.assertEqual(groq.transcribe("gsk_test", b""), "")
        post.assert_not_called()

    def test_missing_key_is_reported_without_a_request(self):
        with self.assertRaises(groq.APIError):
            groq.transcribe("   ", b"\x00\x01")
        with self.assertRaises(groq.APIError):
            groq.chat("", [{"role": "user", "content": "hi"}])

    def test_chat_returns_text_and_tool_calls(self):
        payload = {"choices": [{"message": {"content": "On it.",
                                             "tool_calls": [{"id": "call_1", "type": "function",
                                                             "function": {"name": "look_at_screen",
                                                                          "arguments": "{}"}}]}}]}

        def open_url(request, timeout=None):
            body = json.loads(request.data)
            self.assertTrue(body["tools"][0]["type"] == "function")
            self.assertIn("function", body["tools"][0])
            return Response(json.dumps(payload).encode())

        with patch("bluey.groq.build_opener") as opener:
            opener.return_value.open.side_effect = lambda request, timeout=None: open_url(request)
            reply = groq.chat("gsk_test", [{"role": "user", "content": "look"}],
                              groq.as_chat_tools(realtime.build_session("", False, "Ubuntu Linux")["tools"]))
        self.assertEqual(reply["text"], "On it.")
        self.assertEqual(reply["tool_calls"], [{"id": "call_1", "name": "look_at_screen", "arguments": "{}"}])

    def test_chat_without_a_reply_is_an_error(self):
        with patch("bluey.groq._post", return_value={"choices": []}):
            with self.assertRaises(groq.APIError):
                groq.chat("gsk_test", [{"role": "user", "content": "hi"}])

    def test_only_vision_models_are_given_screenshots(self):
        self.assertTrue(groq.supports_images("meta-llama/llama-4-scout-17b-16e-instruct"))
        self.assertFalse(groq.supports_images("llama-3.3-70b-versatile"))

    def test_groq_session_keeps_the_personality_and_drops_web_research(self):
        instructions, tools = realtime.groq_session("You are a grumpy berry.", False)
        self.assertIn("grumpy berry", instructions)
        self.assertIn("look_at_screen", instructions)
        names = {tool["function"]["name"] for tool in tools}
        self.assertIn("look_at_screen", names)
        self.assertIn("point_at", names)
        self.assertNotIn("web_research", names)
        self.assertNotIn("click", names)  # computer control stays off

    def test_groq_session_offers_computer_control_when_it_is_on(self):
        _, tools = realtime.groq_session("", True)
        names = {tool["function"]["name"] for tool in tools}
        self.assertTrue({"click", "type_text", "open_url"} <= names)


if __name__ == "__main__":
    unittest.main()