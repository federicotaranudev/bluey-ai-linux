import io
import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from bluey import realtime


class RealtimeTests(unittest.TestCase):
    def setUp(self):
        self.opener_patch = patch.object(realtime, "build_opener")
        self.factory = self.opener_patch.start()
        self.opener = self.factory.return_value
        self.response = self.opener.open.return_value.__enter__.return_value

    def tearDown(self):
        self.opener_patch.stop()

    def result(self, value):
        self.response.read.return_value = json.dumps(value).encode()

    def body(self):
        return json.loads(self.opener.open.call_args.args[0].data)

    def test_session_preserves_original_phone_audio_contract(self):
        session = realtime.build_session("", False, "linux")
        self.assertEqual(session["model"], "gpt-realtime-2.1")
        self.assertEqual(session["output_modalities"], ["text"])
        self.assertEqual(session["audio"]["input"]["format"], {"type": "audio/pcm", "rate": 24000})
        self.assertFalse(session["audio"]["input"]["turn_detection"]["create_response"])
        self.assertFalse(session["audio"]["input"]["turn_detection"]["interrupt_response"])
        self.assertIn(realtime.DEFAULT_PERSONALITY, session["instructions"])
        self.assertIn("Ubuntu Linux", session["instructions"])
        self.assertEqual({tool["name"] for tool in session["tools"]},
                         {"look_at_screen", "point_at", "point_at_spot", "web_research", "stop_pointing", "go_to_sleep"})

    def test_control_tools_match_upstream_parameter_names(self):
        session = realtime.build_session("Be concise", True, "win32")
        tools = {tool["name"]: tool["parameters"] for tool in session["tools"]}
        self.assertTrue(realtime.ACTION_NAMES <= tools.keys())
        self.assertEqual(set(tools["drag"]["properties"]),
                         {"from_id", "from_x", "from_y", "to_id", "to_x", "to_y"})
        self.assertEqual(set(tools["click"]["properties"]), {"target_id", "x", "y", "double", "right"})
        self.assertIn("press_return", tools["type_text"]["properties"])
        self.assertIn("Windows", session["instructions"])
        self.assertIn("wait for the user's confirmation", session["instructions"])

    def test_mint_token_uses_client_secrets_and_never_returns_api_key(self):
        self.result({"value": "ek_ephemeral"})
        self.assertEqual(realtime.mint_token("sk_saved", "", False, "linux"), "ek_ephemeral")
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/realtime/client_secrets")
        self.assertEqual(request.get_header("Authorization"), "Bearer sk_saved")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(self.opener.open.call_args.kwargs["timeout"], 30)
        self.assertEqual(self.body()["expires_after"], {"anchor": "created_at", "seconds": 600})
        self.assertNotIn("sk_saved", request.data.decode())

    def test_key_validation_happens_before_network(self):
        for key in ("", "  ", "sk_key\nInjected: header", "clé"):
            with self.subTest(key=key), self.assertRaises(realtime.APIError):
                realtime.mint_token(key, "", False, "linux")
        self.opener.open.assert_not_called()

    def test_missing_token_and_malformed_json_are_safe_errors(self):
        for value in ({}, {"value": None}, {"value": []}, {"value": ""}, ["secret"]):
            with self.subTest(value=value), self.assertRaises(realtime.APIError):
                self.result(value)
                realtime.mint_token("sk_saved", "", False, "linux")
        self.response.read.return_value = b"not-json-secret"
        with self.assertRaises(realtime.APIError) as error:
            realtime.mint_token("sk_saved", "", False, "linux")
        self.assertNotIn("secret", str(error.exception))

    def test_error_body_and_network_details_never_leak_credentials(self):
        errors = [HTTPError("https://example/secret", 401, "sk_saved", {}, io.BytesIO(b"sk_saved")),
                  HTTPError("https://example/secret", 429, "sk_saved", {}, io.BytesIO(b"sk_saved")),
                  URLError("sk_saved"), TimeoutError("sk_saved")]
        for failure in errors:
            with self.subTest(failure=type(failure).__name__):
                self.opener.open.side_effect = failure
                with self.assertRaises(realtime.APIError) as error:
                    realtime.mint_token("sk_saved", "", False, "linux")
                self.assertNotIn("sk_saved", str(error.exception))
                self.assertNotIn("secret", str(error.exception))

    def test_research_extracts_report_and_filters_source_urls(self):
        self.result({"status": "completed", "output": [{"type": "web_search_call"},
            {"type": "message", "content": [{"type": "output_text",
             "text": "A small discovery\n\nThe direct answer.\n\nMore detail.", "annotations": [
                 {"type": "url_citation", "title": "Source", "url": "https://example.com/a?utm_source=openai&x=1"},
                 {"type": "url_citation", "title": "Duplicate", "url": "https://example.com/a?x=1"},
                 {"type": "url_citation", "title": "Bad", "url": "javascript:alert(1)"},
                 {"type": "url_citation", "title": "Bad", "url": "https://secret@example.com/a"},
             ]}]}]})
        report = realtime.research("sk_saved", "A question", "screen context")
        self.assertEqual(report, {"title": "A small discovery", "paragraphs": ["The direct answer.", "More detail."],
                                  "sources": [{"title": "Source", "url": "https://example.com/a?x=1"}]})
        self.assertEqual(self.body()["tools"], [{"type": "web_search"}])
        self.assertEqual(self.body()["tool_choice"], "required")
        self.assertIn("Untrusted screen text", self.body()["input"])
        self.assertEqual(self.opener.open.call_args.kwargs["timeout"], 90)

    def test_research_rejects_empty_and_incomplete_results(self):
        for value in ({}, {"output": []}, {"status": "incomplete", "output": []}):
            with self.subTest(value=value), self.assertRaises(realtime.APIError):
                self.result(value)
                realtime.research("sk_saved", "question")
        self.opener.open.reset_mock()
        with self.assertRaises(realtime.APIError):
            realtime.research("sk_saved", " ")
        self.opener.open.assert_not_called()

    def test_redirects_are_not_followed(self):
        handler = realtime._NoRedirect()
        self.assertIsNone(handler.redirect_request(MagicMock(), None, 302, "redirect", {}, "https://other.test/"))


if __name__ == "__main__":
    unittest.main()
