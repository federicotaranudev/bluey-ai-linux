"""The unchanged iPhone app's Bonjour + newline-delimited JSON protocol.

This is a trusted-LAN protocol, not authenticated or encrypted pairing. Approval
lasts only for the current TCP connection. Callbacks must return promptly and
marshal UI work onto the GUI thread; callback exceptions never escape the server.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import ipaddress
import json
import logging
import math
import select
import socket
import threading
import time
import uuid
from typing import Callable

log = logging.getLogger(__name__)
SERVICE_TYPE = "_googly._tcp.local."
MAX_FRAME_BYTES = 4 * 1024 * 1024
MAX_QUEUED_BYTES = 8 * 1024 * 1024
MAX_QUEUED_PACKETS = 64
MAX_CLIENTS = 8
MAX_PACKETS_PER_SECOND = 160
HELLO_TIMEOUT = 10.0
APPROVAL_TIMEOUT = 90.0
FRAME_TIMEOUT = 10.0
WRITE_TIMEOUT = 10.0


def _packet(data: bytes) -> dict:
    """Decode only complete UTF-8 frames and check the shared Swift field types."""
    def invalid_constant(_):
        raise ValueError("Invalid JSON number")
    packet = json.loads(data.decode("utf-8"), parse_constant=invalid_constant)
    if not isinstance(packet, dict):
        raise ValueError("A packet must be an object")
    for name in ("hello", "command", "audio", "callID", "tool", "text", "image", "endpoint"):
        value = packet.get(name)
        if value is not None and not isinstance(value, str):
            raise ValueError("Invalid string field")
    for name in ("hello", "command", "callID", "tool"):
        if len(packet.get(name) or "") > 200:
            raise ValueError("Identifier too long")
    for name in ("volume", "speech"):
        value = packet.get(name)
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
            raise ValueError("Invalid numeric field")
    if packet.get("speech") is not None and type(packet["speech"]) is not int:
        raise ValueError("Speech identifier must be an integer")
    face = packet.get("face")
    if face is not None:
        if not isinstance(face, dict) or face.get("mood") not in {
            "listening", "resting", "thinking", "talking", "pointing", "happy", "sleepy"
        }:
            raise ValueError("Invalid face")
        for name in ("gazeX", "gazeY", "talk"):
            value = face.get(name)
            if type(value) not in (float, int) or not math.isfinite(value):
                raise ValueError("Invalid face coordinate")
    return packet


@dataclass
class _Peer:
    connection: socket.socket
    address: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    name: str | None = None
    approved: bool = False
    closed: bool = False
    created: float = field(default_factory=time.monotonic)
    outgoing: deque = field(default_factory=deque)
    queued_bytes: int = 0
    thread: threading.Thread | None = None


class PhoneServer:
    """Thread-safe, bounded TCP service compatible with Shared/GooglyLink.swift."""

    def __init__(self, name: str, on_packet: Callable[[str, dict], None],
                 on_pending: Callable[[str, str, str], None],
                 on_phones: Callable[[list[str]], None], port: int = 0,
                 on_disconnect: Callable[[str], None] | None = None):
        self.name = str(name).strip()[:100] or "Bluey"
        self.port = port
        self.on_packet = on_packet
        self.on_pending = on_pending
        self.on_phones = on_phones
        self.on_disconnect = on_disconnect
        self._lock = threading.RLock()
        self._peers: dict[str, _Peer] = {}
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._zeroconf = None
        self._service = None

    def start(self) -> int:
        if self._listener is not None:
            return self.port
        self._stopping.clear()
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # Windows SO_REUSEADDR permits another process to steal the listener.
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("0.0.0.0", self.port))
            listener.listen(MAX_CLIENTS)
            listener.settimeout(0.2)
            self.port = listener.getsockname()[1]
            self._listener = listener
            self._advertise()
        except Exception:
            self.stop()
            listener.close()
            raise
        self._thread = threading.Thread(target=self._serve, name="bluey-listen", daemon=True)
        self._thread.start()
        return self.port

    def _advertise(self) -> None:
        try:
            from zeroconf import IPVersion, ServiceInfo, Zeroconf
            addresses = set()
            try:
                for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                    addresses.add(item[4][0])
            except OSError:
                pass
            # Connecting a UDP socket determines the route; no packet is sent.
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                try:
                    probe.connect(("224.0.0.251", 5353))
                    addresses.add(probe.getsockname()[0])
                except OSError:
                    pass
            addresses = {address for address in addresses
                         if not ipaddress.ip_address(address).is_loopback
                         and not ipaddress.ip_address(address).is_unspecified}
            # A local-only registration still lets the desktop run while offline.
            addresses = addresses or {"127.0.0.1"}
            label = self.name.replace(".", "-").encode("utf-8")[:60].decode("utf-8", "ignore")
            self._zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
            self._service = ServiceInfo(
                SERVICE_TYPE, f"{label}.{SERVICE_TYPE}",
                addresses=[socket.inet_aton(address) for address in sorted(addresses)],
                port=self.port, properties={}, server=f"bluey-{uuid.uuid4().hex[:12]}.local.")
            self._zeroconf.register_service(self._service, allow_name_change=True)
        except Exception as error:
            raise RuntimeError("Could not advertise Bluey on the local network. "
                               "Check zeroconf is installed and UDP port 5353 is allowed.") from error

    def _call(self, callback: Callable, *args) -> None:
        try:
            callback(*args)
        except Exception:
            # Do not log packets: they can contain screenshots or temporary tokens.
            log.warning("A phone callback failed", exc_info=False)

    def _notify_phones(self) -> None:
        with self._lock:
            names = sorted(peer.name for peer in self._peers.values()
                           if peer.approved and not peer.closed and peer.name)
        self._call(self.on_phones, names)

    def accept(self, peer_id: str) -> bool:
        with self._lock:
            peer = self._peers.get(peer_id)
            if peer is None or peer.closed or peer.name is None:
                return False
            if peer.approved:
                return True
            peer.approved = True
            self.send(peer_id, {"hello": self.name})
        self._notify_phones()
        return True

    def reject(self, peer_id: str) -> None:
        with self._lock:
            peer = self._peers.get(peer_id)
        if peer:
            self._close(peer)

    def is_approved(self, peer_id: str) -> bool:
        with self._lock:
            peer = self._peers.get(peer_id)
            return peer is not None and peer.approved and not peer.closed

    def address_of(self, peer_id: str) -> str | None:
        """Where a peer is connecting from, so replies can address it directly."""
        with self._lock:
            peer = self._peers.get(peer_id)
        return peer.address if peer else None

    def send(self, peer_id: str, packet: dict) -> bool:
        data = self._encode(packet)
        with self._lock:
            peer = self._peers.get(peer_id)
            if peer is None or peer.closed or not peer.approved:
                return False
            if (peer.queued_bytes + len(data) > MAX_QUEUED_BYTES
                    or len(peer.outgoing) >= MAX_QUEUED_PACKETS):
                self._close(peer)
                return False
            peer.outgoing.append(data)
            peer.queued_bytes += len(data)
        return True

    def broadcast(self, packet: dict) -> None:
        with self._lock:
            peer_ids = [peer.id for peer in self._peers.values() if peer.approved]
        for peer_id in peer_ids:
            self.send(peer_id, packet)

    @staticmethod
    def _encode(packet: dict) -> bytes:
        if not isinstance(packet, dict):
            raise ValueError("A packet must be an object")
        data = json.dumps(packet, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(data) > MAX_FRAME_BYTES:
            raise ValueError("Packet exceeds the size limit")
        return data + b"\n"

    def _serve(self) -> None:
        listener = self._listener
        while not self._stopping.is_set():
            try:
                connection, address = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            connection.setblocking(False)
            with self._lock:
                if len(self._peers) >= MAX_CLIENTS or self._stopping.is_set():
                    connection.close()
                    continue
                peer = _Peer(connection, address[0])
                self._peers[peer.id] = peer
                peer.thread = threading.Thread(target=self._read_peer, args=(peer,),
                                               name="bluey-phone", daemon=True)
                peer.thread.start()

    def _read_peer(self, peer: _Peer) -> None:
        buffer = bytearray()
        partial_since = None
        frame_approved = False
        pending_write = memoryview(b"")
        write_since = None
        packet_times = deque()
        try:
            while not self._stopping.is_set() and not peer.closed:
                now = time.monotonic()
                if not peer.approved and now - peer.created > (APPROVAL_TIMEOUT if peer.name else HELLO_TIMEOUT):
                    break
                if partial_since is not None and now - partial_since > FRAME_TIMEOUT:
                    break
                if write_since is not None and now - write_since > WRITE_TIMEOUT:
                    break
                with self._lock:
                    if not pending_write and peer.outgoing:
                        pending_write = memoryview(peer.outgoing.popleft())
                        write_since = now
                readable, writable, _ = select.select(
                    [peer.connection], [peer.connection] if pending_write else [], [], 0.05)
                if writable:
                    try:
                        sent = peer.connection.send(pending_write)
                    except BlockingIOError:
                        sent = 0
                    pending_write = pending_write[sent:]
                    with self._lock:
                        peer.queued_bytes -= sent
                    if not pending_write:
                        write_since = None
                if not readable:
                    continue
                with self._lock:
                    approved_at_read = peer.approved
                try:
                    data = peer.connection.recv(64 * 1024)
                except BlockingIOError:
                    continue
                if not data:
                    break
                if not buffer:
                    partial_since = now
                    frame_approved = approved_at_read
                buffer.extend(data)
                while b"\n" in buffer:
                    line, _, rest = buffer.partition(b"\n")
                    buffer = bytearray(rest)
                    if len(line) > MAX_FRAME_BYTES:
                        raise ValueError("Oversized frame")
                    packet = _packet(line)
                    while packet_times and now - packet_times[0] > 1:
                        packet_times.popleft()
                    packet_times.append(now)
                    if len(packet_times) > MAX_PACKETS_PER_SECOND:
                        raise ValueError("Packet rate exceeded")
                    if frame_approved and not peer.closed:
                        # Identity is fixed at admission; later hello fields cannot rename a peer.
                        packet.pop("hello", None)
                        if packet:
                            self._call(self.on_packet, peer.id, packet)
                    elif peer.name is None:
                        name = packet.get("hello")
                        if not name or not name.strip() or any(ord(char) < 32 for char in name):
                            raise ValueError("A device hello is required first")
                        peer.name = name.strip()
                        self._call(self.on_pending, peer.id, peer.name, peer.address)
                    # Commands received before approval are discarded, never deferred.
                    partial_since = now if buffer else None
                    frame_approved = approved_at_read
                if len(buffer) > (MAX_FRAME_BYTES if peer.approved else 4096):
                    raise ValueError("Unterminated frame too large")
        except (OSError, ValueError, TypeError, OverflowError, RecursionError):
            pass
        finally:
            self._close(peer)

    def _close(self, peer: _Peer) -> None:
        with self._lock:
            if peer.closed:
                return
            peer.closed = True
            self._peers.pop(peer.id, None)
            peer.outgoing.clear()
            try:
                peer.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            peer.connection.close()
        if peer.approved:
            self._notify_phones()
        if self.on_disconnect:
            self._call(self.on_disconnect, peer.id)

    def stop(self) -> None:
        self._stopping.set()
        listener, self._listener = self._listener, None
        if listener:
            listener.close()
        with self._lock:
            peers = list(self._peers.values())
        for peer in peers:
            self._close(peer)
        current = threading.current_thread()
        for thread in [self._thread] + [peer.thread for peer in peers]:
            if thread and thread is not current:
                thread.join(timeout=0.5)
        self._thread = None
        if self._zeroconf:
            try:
                if self._service:
                    self._zeroconf.unregister_service(self._service)
            except Exception:
                log.warning("Could not withdraw the Bonjour advertisement")
            finally:
                self._zeroconf.close()
                self._zeroconf = self._service = None
