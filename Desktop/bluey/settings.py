"""Preferences are non-secret JSON; credentials belong in the OS keyring."""
from __future__ import annotations

import json
import os
from pathlib import Path
from dataclasses import dataclass, asdict

from platformdirs import user_config_path, user_log_path


@dataclass
class Preferences:
    cursor_size: int = 72
    phone_position: float = 0.5
    follow_mouse: bool = True
    show_cursor: bool = True
    glow: bool = False
    reduced_motion: bool = False
    mood: str = "listening"
    personality: str = ""
    # Consent to computer actions is session-scoped and deliberately not persisted.

    @classmethod
    def load(cls, path: Path | None = None) -> "Preferences":
        path = path or config_file()
        result = cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return result
            for key, default in asdict(result).items():
                value = raw.get(key, default)
                if type(value) is type(default):
                    setattr(result, key, value)
            result.cursor_size = max(48, min(120, result.cursor_size))
            result.phone_position = max(0.0, min(1.0, result.phone_position))
            if result.mood not in MOODS:
                result.mood = "listening"
            result.personality = result.personality[:8000]
        except (OSError, ValueError, TypeError):
            pass
        return result

    def save(self, path: Path | None = None) -> None:
        path = path or config_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        os.replace(temporary, path)


MOODS = ("listening", "resting", "thinking", "talking", "pointing", "happy", "sleepy")


def provider_for_key(key: str) -> str:
    """Which service a saved key belongs to. Groq keys start with `gsk_`."""
    return "groq" if key.strip().startswith("gsk_") else "openai"


def config_file() -> Path:
    return user_config_path("BlueyDesktop", appauthor=False) / "settings.json"


def log_file() -> Path:
    """Where the desktop writes its log, so problems can be looked at afterwards."""
    return user_log_path("BlueyDesktop", appauthor=False) / "bluey-desktop.log"


class Credentials:
    """No plaintext fallback: when keyring is unavailable, keep a key in memory."""
    service = "BlueyDesktop"
    account = "openai-api-key"

    def __init__(self, use_keyring: bool = True):
        self._key = ""
        self.persisted = False
        self.use_keyring = use_keyring
        if use_keyring:
            try:
                import keyring
                self._key = keyring.get_password(self.service, self.account) or ""
                self.persisted = bool(self._key)
            except Exception:
                pass

    def get(self) -> str:
        return self._key

    def set(self, key: str) -> bool:
        key = key.strip()
        if not key or len(key) > 1024 or any(c.isspace() for c in key):
            raise ValueError("Paste a complete API key without spaces.")
        self._key = key
        self.persisted = False
        if self.use_keyring:
            try:
                import keyring
                keyring.set_password(self.service, self.account, key)
                self.persisted = True
            except Exception:
                pass
        return self.persisted

    def clear(self) -> None:
        if self.use_keyring:
            try:
                import keyring
                if keyring.get_password(self.service, self.account):
                    keyring.delete_password(self.service, self.account)
            except Exception as exc:
                raise RuntimeError("The credential vault is unavailable. Unlock it before removing the saved key.") from exc
        self._key = ""
        self.persisted = False
