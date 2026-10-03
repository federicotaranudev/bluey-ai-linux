import json
import queue
import socket
import threading
import time
import unittest
from unittest.mock import patch

from bluey import protocol


class PhoneServerTests(unittest.TestCase):
    def setUp(self):
        self.pending = queue.Queue()
        self.packets = queue.Queue()
        self.phones = queue.Queue()
        self.disconnected = queue.Queue()
        self.server = protocol.PhoneServer(
            "Ubuntu desk", lambda *args: self.packets.put(args),
            lambda *args: self.pending.put(args), self.phones.put,
            on_disconnect=self.disconnected.put)
        self.advertise = patch.object(self.server, "_advertise")
        self.advertise.start()
        self.port = self.server.start()
        self.connections = []

    def tearDown(self):
        for connection in self.connections:
            connection.close()
        self.server.stop()
        self.advertise.stop()

    def connect(self, name="iPhone"):
        connection = socket.create_connection(("127.0.0.1", self.port), timeout=2)
        connection.settimeout(2)
        self.connections.append(connection)
        if name is not None:
            connection.sendall(json.dumps({"hello": name}, ensure_ascii=False).encode() + b"\n")
            peer_id, actual_name, address = self.pending.get(timeout=2)
            self.assertEqual(actual_name, name)
            self.assertEqual(address, "127.0.0.1")
            return connection, peer_id
        return connection

    def receive(self, connection):
        data = bytearray()
        while b"\n" not in data:
            chunk = connection.recv(65536)
            if not chunk:
                self.fail("Socket closed before a packet arrived")
            data.extend(chunk)
        return json.loads(data)

    def approve(self, connection, peer_id):
        self.assertTrue(self.server.accept(peer_id))
        self.assertTrue(self.server.is_approved(peer_id))
        self.assertEqual(self.receive(connection), {"hello": "Ubuntu desk"})

    def assert_closed(self, connection):
        try:
            self.assertEqual(connection.recv(1024), b"")
        except ConnectionResetError:
            pass

    def test_admission_blocks_inbound_and_outbound(self):
        connection, peer_id = self.connect()
        connection.sendall(b'{"command":"realtimeToken","callID":"early"}\n')
        self.assertFalse(self.server.send(peer_id, {"command": "wake"}))
        self.server.broadcast({"face": {"gazeX": 0}})
        connection.settimeout(0.15)
        with self.assertRaises(socket.timeout):
            connection.recv(1)
        with self.assertRaises(queue.Empty):
            self.packets.get_nowait()
        connection.settimeout(2)
        self.approve(connection, peer_id)
        self.assertEqual(self.phones.get(timeout=2), ["iPhone"])
        connection.sendall(b'{"command":"realtimeToken","callID":"ready"}\n')
        self.assertEqual(self.packets.get(timeout=2),
                         (peer_id, {"command": "realtimeToken", "callID": "ready"}))

    def test_fragmented_utf8_and_multiple_messages(self):
        connection = self.connect(None)
        hello = '{"hello":"Téléphone 🫐"}\n'.encode()
        emoji = hello.index("🫐".encode())
        for chunk in (hello[:emoji + 1], hello[emoji + 1:emoji + 3], hello[emoji + 3:]):
            connection.sendall(chunk)
        peer_id, name, _ = self.pending.get(timeout=2)
        self.assertEqual(name, "Téléphone 🫐")
        self.approve(connection, peer_id)
        connection.sendall('{"command":"caption","text":"hé 🫐"}\n{"command":"awake"}\n'.encode())
        self.assertEqual(self.packets.get(timeout=2)[1]["text"], "hé 🫐")
        self.assertEqual(self.packets.get(timeout=2)[1], {"command": "awake"})

    def test_the_desktop_can_address_a_reply_to_the_phone_it_came_from(self):
        connection, peer_id = self.connect()
        self.approve(connection, peer_id)
        self.assertEqual(self.server.address_of(peer_id), "127.0.0.1")
        self.assertIsNone(self.server.address_of("not-a-peer"))

    def test_a_local_voice_endpoint_travels_to_the_phone(self):
        connection, peer_id = self.connect()
        self.approve(connection, peer_id)
        connection.sendall(b'{"command":"realtimeToken","callID":"t1"}\n')
        self.assertEqual(self.packets.get(timeout=2)[1]["callID"], "t1")
        self.assertTrue(self.server.send(peer_id, {
            "command": "realtimeToken", "callID": "t1", "text": "local-token",
            "endpoint": "ws://127.0.0.1:9999/v1/realtime"}))
        self.assertEqual(self.receive(connection)["endpoint"], "ws://127.0.0.1:9999/v1/realtime")

    def test_hello_command_and_preapproval_batch_cannot_execute(self):
        original = self.server.on_pending
        def immediate_accept(peer_id, name, address):
            original(peer_id, name, address)
            self.server.accept(peer_id)
        self.server.on_pending = immediate_accept
        connection = self.connect(None)
        connection.sendall(b'{"hello":"Phone","command":"tool"}\n{"command":"realtimeToken"}\n')
        self.pending.get(timeout=2)
        self.assertEqual(self.receive(connection), {"hello": "Ubuntu desk"})
        with self.assertRaises(queue.Empty):
            self.packets.get(timeout=0.1)

    def test_partly_received_preapproval_command_is_discarded(self):
        connection, peer_id = self.connect()
        connection.sendall(b'{"command":"realtime')
        time.sleep(0.1)
        self.approve(connection, peer_id)
        connection.sendall(b'Token"}\n{"command":"awake"}\n')
        self.assertEqual(self.packets.get(timeout=2)[1], {"command": "awake"})
        with self.assertRaises(queue.Empty):
            self.packets.get_nowait()

    def test_reject_and_reconnect_require_new_approval(self):
        connection, peer_id = self.connect()
        self.approve(connection, peer_id)
        self.server.reject(peer_id)
        self.assert_closed(connection)
        self.assertEqual(self.disconnected.get(timeout=2), peer_id)
        self.assertFalse(self.server.is_approved(peer_id))
        replacement, replacement_id = self.connect()
        self.assertNotEqual(peer_id, replacement_id)
        self.assertFalse(self.server.is_approved(replacement_id))
        self.server.reject(replacement_id)
        self.assert_closed(replacement)

    def test_invalid_packets_disconnect_only_the_bad_peer(self):
        good, good_id = self.connect("Good")
        self.approve(good, good_id)
        for data in (b'[]\n', b'{"hello":42}\n', b'{"hello":"x","volume":NaN}\n',
                     b'\xff\n', b'not json\n', b'{"command":"tool"}\n'):
            with self.subTest(data=data):
                bad = self.connect(None)
                bad.sendall(data)
                self.assert_closed(bad)
        good.sendall(b'{"command":"awake"}\n')
        self.assertEqual(self.packets.get(timeout=2), (good_id, {"command": "awake"}))

    def test_frame_limit_and_partial_timeout(self):
        with patch.object(protocol, "MAX_FRAME_BYTES", 64):
            connection = self.connect(None)
            connection.sendall(b'{"hello":"' + b"x" * 80 + b'"}\n')
            self.assert_closed(connection)
        connection, peer_id = self.connect()
        self.approve(connection, peer_id)
        with patch.object(protocol, "FRAME_TIMEOUT", 0.08):
            connection.sendall(b'{"command":')
            self.assert_closed(connection)

    def test_callback_failure_does_not_kill_connection(self):
        connection, peer_id = self.connect()
        self.approve(connection, peer_id)
        called = threading.Event()
        def broken(*_):
            called.set()
            raise RuntimeError("simulated UI error")
        self.server.on_packet = broken
        with self.assertLogs(protocol.log, level="WARNING"):
            connection.sendall(b'{"command":"awake"}\n')
            self.assertTrue(called.wait(2))
            time.sleep(0.02)
        self.server.on_packet = lambda *args: self.packets.put(args)
        connection.sendall(b'{"command":"asleep"}\n')
        self.assertEqual(self.packets.get(timeout=2)[1], {"command": "asleep"})

    def test_backpressure_disconnects_and_shutdown_is_idempotent(self):
        connection, peer_id = self.connect()
        self.approve(connection, peer_id)
        with patch.object(protocol, "MAX_QUEUED_BYTES", 5):
            self.assertFalse(self.server.send(peer_id, {"command": "wake"}))
        self.assert_closed(connection)
        self.server.stop()
        self.server.stop()

    def test_handshake_timeout(self):
        with patch.object(protocol, "HELLO_TIMEOUT", 0.05):
            connection = self.connect(None)
            self.assert_closed(connection)


if __name__ == "__main__":
    unittest.main()
