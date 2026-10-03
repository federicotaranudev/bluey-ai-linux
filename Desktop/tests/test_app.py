"""Exercise the UI/controller at its real worker/network boundaries without input."""
import json
import os
import socket
import sys
import threading
import time
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from bluey.app import DesktopApp


@pytest.fixture(scope='module')
def qt():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def desktop(qt):
    app = DesktopApp(port=0, smoke=True)
    yield app
    app.shutdown()
    app.window.hide()
    app.window.deleteLater()
    qt.processEvents()


def spin(qt, condition, timeout=3):
    deadline = time.monotonic() + timeout
    while not condition():
        qt.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError('Timed out waiting for Qt/worker event')
        time.sleep(.005)
    qt.processEvents()


def test_iphone_tool_roundtrip_through_approval_and_qt_worker(desktop, qt, monkeypatch):
    monkeypatch.setattr(desktop.server, '_advertise', lambda: None)
    port = desktop.server.start()
    phone = socket.create_connection(('127.0.0.1', port))
    phone.settimeout(2)
    stream = phone.makefile('rb')
    try:
        phone.sendall(b'{"hello":"Test iPhone"}\n')
        spin(qt, lambda: bool(desktop.approval_boxes))
        peer, box = next(iter(desktop.approval_boxes.items()))
        assert desktop.approved_peer is None
        box.done(QMessageBox.StandardButton.Yes)
        spin(qt, lambda: desktop.approved_peer == peer)
        assert json.loads(stream.readline())['hello']
        called = []
        monkeypatch.setattr(desktop.backend, 'capture', lambda: called.append(True) or SimpleNamespace(text='L1 test', jpeg_base64='aW1hZ2U='))
        phone.sendall(b'{"command":"tool","tool":"look_at_screen","text":"{}","callID":"ios-1"}\n')
        spin(qt, lambda: bool(called) and desktop.jobs == 0)
        packets = [json.loads(stream.readline())]
        while packets[-1].get('command') != 'toolResult':
            packets.append(json.loads(stream.readline()))
        assert packets[-1] == {'command': 'toolResult', 'callID': 'ios-1', 'text': 'L1 test', 'image': 'aW1hZ2U='}
        assert not desktop.overlay.suspended
    finally:
        stream.close()
        phone.close()


def test_emergency_stop_cancels_already_queued_action(desktop, qt, monkeypatch):
    monkeypatch.setattr(desktop.server, 'is_approved', lambda _: True)
    results = []
    monkeypatch.setattr(desktop.server, 'send', lambda peer, packet: results.append(packet))
    called = []
    monkeypatch.setattr(desktop.backend, 'perform', lambda *args: called.append(args))
    desktop.set_control(True)
    release = threading.Event()
    desktop.executor.submit(release.wait, 2)
    desktop.handle_packet('phone', {'command': 'tool', 'tool': 'type_text', 'text': '{"text":"hello"}', 'callID': 'cancel-me'})
    desktop.stop_actions()
    release.set()
    spin(qt, lambda: desktop.jobs == 0)
    assert called == []
    assert not desktop.control_enabled
    assert results[-1]['text'] == 'Request cancelled.'


def test_control_gate_applies_even_if_phone_sends_unadvertised_tool(desktop, monkeypatch):
    monkeypatch.setattr(desktop.backend, 'perform', lambda *args: pytest.fail('Control was invoked'))
    text, image = desktop.run_tool('type_text', {'text': 'hello'}, threading.Event(), '', True)
    assert 'control is off' in text
    assert image is None


def test_cancelled_token_never_reaches_phone(desktop, qt, monkeypatch):
    import bluey.app
    monkeypatch.setattr(desktop.server, 'is_approved', lambda _: True)
    results = []
    monkeypatch.setattr(desktop.server, 'send', lambda peer, packet: results.append(packet))
    started, release = threading.Event(), threading.Event()
    def mint(*args):
        started.set()
        release.wait(2)
        return 'ephemeral-test-secret'
    monkeypatch.setattr(bluey.app, 'mint_token', mint)
    desktop.handle_packet('phone', {'command': 'realtimeToken', 'callID': 'token-1'})
    spin(qt, started.is_set)
    desktop.stop_actions()
    release.set()
    spin(qt, lambda: desktop.jobs == 0)
    assert results[-1]['text'] is None
    assert 'ephemeral-test-secret' not in json.dumps(results)


def test_a_groq_key_hands_the_phone_a_local_voice_endpoint(desktop, qt, monkeypatch):
    import bluey.app
    monkeypatch.setattr(desktop.server, 'is_approved', lambda _: True)
    monkeypatch.setattr(desktop.server, 'address_of', lambda peer: '192.168.1.178:51234')
    monkeypatch.setattr(desktop.server, 'local_address_of', lambda peer: '192.168.1.115')
    monkeypatch.setattr(desktop.credentials, 'get', lambda: 'gsk_free_key')
    results = []
    monkeypatch.setattr(desktop.server, 'send', lambda peer, packet: results.append(packet))
    started = []
    proxy = SimpleNamespace(token=lambda: 'local-voice-token',
                            url_for=lambda host: f'ws://{host}:9001/v1/realtime')
    monkeypatch.setattr(desktop, 'realtime_proxy', lambda: started.append(True) or proxy)
    monkeypatch.setattr(bluey.app, 'mint_token',
                        lambda *args: pytest.fail('OpenAI must not be called for a Groq key'))
    desktop.handle_packet('phone', {'command': 'realtimeToken', 'callID': 'groq-1'})
    spin(qt, lambda: desktop.jobs == 0 and bool(started))
    reply = results[-1]
    assert reply['callID'] == 'groq-1'
    assert reply['text'] == 'local-voice-token'
    # Our own address, never the phone's: telling the phone to call itself fails instantly.
    assert reply['endpoint'] == 'ws://192.168.1.115:9001/v1/realtime'
    assert '192.168.1.178' not in reply['endpoint']


def test_the_endpoint_falls_back_to_this_machines_lan_address(desktop, qt, monkeypatch):
    import bluey.app
    monkeypatch.setattr(desktop.server, 'is_approved', lambda _: True)
    monkeypatch.setattr(desktop.server, 'local_address_of', lambda peer: None)
    monkeypatch.setattr(bluey.app, 'lan_address', lambda: '10.0.0.9')
    monkeypatch.setattr(desktop.credentials, 'get', lambda: 'gsk_free_key')
    results = []
    monkeypatch.setattr(desktop.server, 'send', lambda peer, packet: results.append(packet))
    proxy = SimpleNamespace(token=lambda: 'tok', url_for=lambda host: f'ws://{host}:9001/v1/realtime')
    monkeypatch.setattr(desktop, 'realtime_proxy', lambda: proxy)
    desktop.handle_packet('phone', {'command': 'realtimeToken', 'callID': 'fallback-1'})
    spin(qt, lambda: desktop.jobs == 0)
    assert results[-1]['endpoint'] == 'ws://10.0.0.9:9001/v1/realtime'


def test_lan_address_is_a_routable_local_address():
    import ipaddress
    from bluey.app import lan_address
    self_ip = ipaddress.ip_address(lan_address())
    assert self_ip.is_private or self_ip.is_loopback


def test_an_openai_key_still_asks_openai(desktop, qt, monkeypatch):
    import bluey.app
    monkeypatch.setattr(desktop.server, 'is_approved', lambda _: True)
    monkeypatch.setattr(desktop.credentials, 'get', lambda: 'sk-openai')
    results = []
    monkeypatch.setattr(desktop.server, 'send', lambda peer, packet: results.append(packet))
    monkeypatch.setattr(desktop, 'realtime_proxy', lambda: pytest.fail('Groq proxy used for an OpenAI key'))
    monkeypatch.setattr(bluey.app, 'mint_token', lambda *args: 'ek_ephemeral')
    desktop.handle_packet('phone', {'command': 'realtimeToken', 'callID': 'oai-1'})
    spin(qt, lambda: desktop.jobs == 0)
    assert results[-1]['text'] == 'ek_ephemeral'
    assert 'endpoint' not in results[-1]


@pytest.mark.skipif(sys.platform != "linux", reason="Qt platform selection is a Linux concern")
def test_a_stale_wayland_environment_still_opens_on_x11(monkeypatch):
    import bluey.__main__ as entry
    monkeypatch.setenv("DISPLAY", ":0.0")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/no/such/dir")
    monkeypatch.setenv("QT_QPA_PLATFORM", "")
    entry.choose_qt_platform()
    assert os.environ["QT_QPA_PLATFORM"] == "xcb"


@pytest.mark.skipif(sys.platform != "linux", reason="Qt platform selection is a Linux concern")
def test_a_real_wayland_session_is_left_to_qt(monkeypatch, tmp_path):
    import bluey.__main__ as entry
    (tmp_path / "wayland-0").write_text("")
    monkeypatch.setenv("DISPLAY", ":0.0")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    entry.choose_qt_platform()
    assert os.environ.get("QT_QPA_PLATFORM", "") != "xcb"


def test_the_log_file_is_where_problems_can_be_read(qt):
    from bluey.settings import log_file
    assert log_file().name.endswith('.log')
