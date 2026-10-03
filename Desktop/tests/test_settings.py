import json
from dataclasses import asdict

from bluey.settings import Credentials, Preferences


def test_preferences_recover_from_corrupt_or_wrongly_typed_values(tmp_path):
    path = tmp_path / 'settings.json'
    path.write_text('{broken')
    assert Preferences.load(path) == Preferences()
    path.write_text(json.dumps({'cursor_size': -100, 'phone_position': 8.0, 'mood': 'invalid', 'glow': 'false'}))
    result = Preferences.load(path)
    assert result.cursor_size == 48
    assert result.phone_position == 1.0
    assert result.mood == 'listening'
    assert result.glow is False


def test_preferences_never_persist_control_consent_or_credentials(tmp_path):
    path = tmp_path / 'settings.json'
    prefs = Preferences(cursor_size=90, personality='Cheeky, please.')
    prefs.save(path)
    assert Preferences.load(path) == prefs
    assert 'computer_control' not in asdict(prefs)
    assert 'api_key' not in path.read_text()


def test_unavailable_keyring_uses_memory_only(monkeypatch):
    import keyring
    def unavailable(*args):
        raise RuntimeError('locked')
    monkeypatch.setattr(keyring, 'get_password', unavailable)
    monkeypatch.setattr(keyring, 'set_password', unavailable)
    credentials = Credentials()
    assert not credentials.set('sk-test-only')
    assert credentials.get() == 'sk-test-only'
    assert not credentials.persisted


def test_session_key_can_be_removed():
    credentials = Credentials(use_keyring=False)
    credentials.set('sk-test-only')
    credentials.clear()
    assert not credentials.get()
