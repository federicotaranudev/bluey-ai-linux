"""The local Realtime stand-in: the token gate, transcription, replies, tool loop."""

import asyncio
import base64
import json
import unittest
from unittest.mock import patch

from bluey import realtime_proxy
from bluey.realtime_proxy import PATH, PhoneSession, RealtimeProxy

AUDIO = base64.b64encode(b"\x01\x02" * 480).decode()  # 20 ms of 24 kHz PCM16


class FakeSocket:
    """Stands in for the phone's WebSocket."""

    def __init__(self, incoming=()):
        self.sent = []
        self.incoming = list(incoming)
        self.closed = None

    async def send(self, text):
        self.sent.append(json.loads(text))

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.incoming:
            raise StopAsyncIteration
        return self.incoming.pop(0)

    async def close(self, code=None, reason=None):
        self.closed = (code, reason)

    def types(self):
        return [event["type"] for event in self.sent]

    def only(self, kind):
        return [event for event in self.sent if event["type"] == kind]


def session(**kwargs):
    return PhoneSession(api_key="gsk_test", tools=[], instructions="Be brief.", **kwargs)


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_commit_transcribes_and_the_words_join_the_history(self):
        socket = FakeSocket([json.dumps({"type": "input_audio_buffer.commit"})])
        with patch("bluey.groq.transcribe", return_value="what is this"):
            talker = session()
            await talker.run(socket)
        committed = socket.only("input_audio_buffer.committed")
        words = socket.only("conversation.item.input_audio_transcription.completed")
        self.assertEqual(len(committed), 1)
        self.assertEqual(committed[0]["item_id"], words[0]["item_id"])
        self.assertEqual(words[0]["transcript"], "what is this")
        self.assertEqual(talker.messages[-1], {"role": "user", "content": "what is this"})

    async def test_audio_is_buffered_until_the_user_lets_go(self):
        socket = FakeSocket([
            json.dumps({"type": "input_audio_buffer.append", "audio": AUDIO}),
            json.dumps({"type": "input_audio_buffer.append", "audio": AUDIO}),
            json.dumps({"type": "input_audio_buffer.commit"}),
        ])
        with patch("bluey.groq.transcribe", return_value="") as heard:
            await session().run(socket)
        self.assertEqual(heard.call_count, 1)
        self.assertEqual(len(heard.call_args.args[1]), 2 * len(base64.b64decode(AUDIO)))

    async def test_a_reply_streams_then_finishes(self):
        socket = FakeSocket([json.dumps({"type": "response.create"})])
        with patch("bluey.groq.chat", return_value={"text": "Right there.", "tool_calls": []}):
            talker = session()
            await talker.run(socket)
        deltas = "".join(event["delta"] for event in socket.only("response.output_text.delta"))
        self.assertEqual(deltas.strip(), "Right there.")
        done = socket.only("response.done")[0]
        self.assertEqual(done["response"]["output"][0]["content"][0]["text"], "Right there.")
        self.assertEqual(talker.messages[-1], {"role": "assistant", "content": "Right there."})

    async def test_instructions_for_one_turn_do_not_stick(self):
        socket = FakeSocket([
            json.dumps({"type": "response.create", "response": {"instructions": "Say hi."}}),
            json.dumps({"type": "response.create"}),
        ])
        seen = []

        def chat(api_key, messages, tools, **kwargs):
            seen.append([message.get("content") for message in messages])
            return {"text": "hi", "tool_calls": []}

        with patch("bluey.groq.chat", side_effect=chat):
            await session().run(socket)
        self.assertIn("Say hi.", seen[0])
        self.assertNotIn("Say hi.", seen[1])
        self.assertEqual(seen[0][0], "Be brief.")

    async def test_a_tool_call_reaches_the_phone_and_its_result_comes_back(self):
        socket = FakeSocket([
            json.dumps({"type": "response.create"}),
            json.dumps({"type": "conversation.item.create",
                        "item": {"type": "function_call_output", "call_id": "call_1",
                                 "output": "Primary screen. L1 @10,20 hello"}}),
            json.dumps({"type": "response.create"}),
        ])
        replies = [{"text": "", "tool_calls": [{"id": "call_1", "name": "look_at_screen", "arguments": "{}"}]},
                   {"text": "It says hello.", "tool_calls": []}]
        seen = []

        def chat(api_key, messages, tools, **kwargs):
            seen.append(messages)
            return replies[len(seen) - 1]

        with patch("bluey.groq.chat", side_effect=chat):
            talker = session()
            await talker.run(socket)
        first = socket.only("response.done")[0]["response"]["output"]
        self.assertEqual(first[0]["type"], "function_call")
        self.assertEqual((first[0]["name"], first[0]["call_id"]), ("look_at_screen", "call_1"))
        self.assertEqual(talker.messages[-2], {"role": "tool", "tool_call_id": "call_1",
                                        "content": "Primary screen. L1 @10,20 hello"})
        self.assertEqual(talker.messages[-1], {"role": "assistant", "content": "It says hello."})
        self.assertEqual(seen[-1][-1]["content"], "Primary screen. L1 @10,20 hello")

    async def test_a_broken_provider_becomes_an_error_the_phone_reads(self):
        socket = FakeSocket([json.dumps({"type": "response.create"})])
        from bluey.groq import APIError
        with patch("bluey.groq.chat", side_effect=APIError("Groq's free rate limit was reached.")):
            await session().run(socket)
        error = socket.only("error")[0]["error"]
        self.assertEqual(error["message"], "Groq's free rate limit was reached.")
        self.assertEqual(socket.only("response.done"), [])

    async def test_screenshots_are_dropped_for_a_text_only_model(self):
        socket = FakeSocket([
            json.dumps({"type": "conversation.item.create",
                        "item": {"type": "message", "role": "user",
                                 "content": [{"type": "input_image",
                                              "image_url": "data:image/jpeg;base64,AAAA"}]}}),
            json.dumps({"type": "response.create"}),
        ])
        seen = []

        def chat(api_key, messages, tools, **kwargs):
            seen.append(messages)
            return {"text": "ok", "tool_calls": []}

        with patch("bluey.groq.chat", side_effect=chat):
            await session().run(socket)
        self.assertEqual(seen[0][-1]["role"], "system")

    async def test_old_speech_is_transcribed_into_context_and_discarded(self):
        seconds = realtime_proxy.CONTEXT_CHUNK_SECONDS + 2
        long_audio = base64.b64encode(b"\x03\x04" * (seconds * realtime_proxy.BYTES_PER_SECOND // 2)).decode()
        socket = FakeSocket([
            json.dumps({"type": "input_audio_buffer.append", "audio": long_audio}),
            json.dumps({"type": "input_audio_buffer.commit"}),
        ])
        with patch("bluey.groq.transcribe", side_effect=["earlier chatter", "and now this"]) as heard:
            talker = session()
            await talker.run(socket)
        self.assertEqual(heard.call_count, 2)
        # The flush takes the older 20 seconds; the commit keeps what came after it.
        self.assertGreater(len(heard.call_args_list[0].args[1]), len(heard.call_args_list[1].args[1]))
        self.assertEqual(talker.messages[0], {"role": "user", "content": "earlier chatter"})
        self.assertEqual(talker.messages[-1], {"role": "user", "content": "and now this"})

    async def test_junk_is_ignored_rather_than_crashing(self):
        socket = FakeSocket(["not json", json.dumps({"type": "session.update"}),
                             json.dumps({"type": "response.create"})])
        with patch("bluey.groq.chat", return_value={"text": "hi", "tool_calls": []}):
            await session().run(socket)
        self.assertIn("response.done", socket.types())


class ProxyServerTests(unittest.TestCase):
    """A real WebSocket round trip against the server the phone connects to."""

    def setUp(self):
        self.proxy = RealtimeProxy(api_key=lambda: "gsk_test", tools=lambda: [],
                                   instructions=lambda: "Be brief.", host="127.0.0.1")
        self.proxy.start()
        self.addCleanup(self.proxy.stop)

    def turn(self, authorization):
        from websockets.asyncio.client import connect

        async def run():
            async with connect(self.proxy.url_for("127.0.0.1"),
                               additional_headers={"Authorization": authorization}) as socket:
                await socket.send(json.dumps({"type": "input_audio_buffer.append", "audio": AUDIO}))
                await socket.send(json.dumps({"type": "input_audio_buffer.commit"}))
                await socket.send(json.dumps({"type": "response.create"}))
                kinds = []
                while "response.done" not in kinds:
                    kinds.append(json.loads(await asyncio.wait_for(socket.recv(), 10))["type"])
                return kinds

        return asyncio.run(run())

    def test_a_whole_turn_over_a_real_socket(self):
        with patch("bluey.groq.transcribe", return_value="what is this"), \
                patch("bluey.groq.chat", return_value={"text": "Your cursor.", "tool_calls": []}):
            kinds = self.turn("Bearer " + self.proxy.token())
        self.assertEqual(kinds[0], "input_audio_buffer.committed")
        self.assertIn("conversation.item.input_audio_transcription.completed", kinds)
        self.assertIn("response.output_text.delta", kinds)
        self.assertEqual(kinds[-1], "response.done")

    def test_a_wrong_token_is_refused(self):
        with self.assertRaises(Exception):
            self.turn("Bearer not-the-token")

    def test_the_url_points_at_the_phone_and_needs_the_running_proxy(self):
        self.assertEqual(self.proxy.url_for("192.168.1.20"), f"ws://192.168.1.20:{self.proxy.port}{PATH}")
        self.proxy.stop()
        with self.assertRaises(RuntimeError):
            self.proxy.url_for("192.168.1.20")


if __name__ == "__main__":
    unittest.main()