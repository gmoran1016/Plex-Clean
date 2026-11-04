"""Restricted, per-browser storage for Plex account credentials."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_CREDENTIAL_RETENTION_SECONDS = 365 * 24 * 60 * 60
_MAX_TOKEN_LENGTH = 64 * 1024


@dataclass(frozen=True, slots=True)
class SavedPlexCredential:
    token: str
    expires_at: float | None
    username: str | None
    saved_at: float


def is_valid_session_id(session_id: str | None) -> bool:
    return isinstance(session_id, str) and _SESSION_ID_PATTERN.fullmatch(session_id) is not None


class PlexCredentialStore:
    """Store account credentials under a hash of the browser's session ID."""

    def __init__(self, config_dir: Path) -> None:
        self.directory = config_dir / "plex-sessions"

    def has_saved_credentials(self) -> bool:
        """Return whether any browser login must keep its current Plex app identity."""
        try:
            return any(
                path.is_file() and path.suffix == ".json"
                for path in self.directory.iterdir()
            )
        except FileNotFoundError:
            return False

    @staticmethod
    def _finite_number(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        try:
            number = float(value)
        except OverflowError:
            return None
        return number if math.isfinite(number) else None

    def _path_for(self, session_id: str) -> Path:
        if not is_valid_session_id(session_id):
            raise ValueError("Invalid Plex-Clean session identifier")
        digest = hashlib.sha256(session_id.encode("ascii")).hexdigest()
        return self.directory / f"{digest}.json"

    def _ensure_directory(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass

    def load(self, session_id: str) -> SavedPlexCredential | None:
        if not is_valid_session_id(session_id):
            return None
        path = self._path_for(session_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            return None

        if not isinstance(payload, dict):
            path.unlink(missing_ok=True)
            return None
        token = payload.get("token")
        expires_at_value = payload.get("expires_at")
        username = payload.get("username")
        saved_at = self._finite_number(payload.get("saved_at"))
        expires_at = None if expires_at_value is None else self._finite_number(expires_at_value)
        if (
            not isinstance(token, str)
            or not token
            or len(token) > _MAX_TOKEN_LENGTH
            or (username is not None and not isinstance(username, str))
            or saved_at is None
            or (expires_at_value is not None and expires_at is None)
        ):
            path.unlink(missing_ok=True)
            return None

        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return SavedPlexCredential(token, expires_at, username, saved_at)

    def save(self, session_id: str, credential: SavedPlexCredential) -> None:
        if not credential.token or len(credential.token) > _MAX_TOKEN_LENGTH:
            raise ValueError("Invalid Plex account token")
        if not math.isfinite(credential.saved_at):
            raise ValueError("Invalid saved credential timestamp")
        if credential.expires_at is not None and not math.isfinite(credential.expires_at):
            raise ValueError("Invalid Plex token expiration")
        if credential.username is not None and not isinstance(credential.username, str):
            raise ValueError("Invalid Plex account name")

        self._ensure_directory()
        destination = self._path_for(session_id)
        temporary = self.directory / f".{destination.stem}.{secrets.token_hex(12)}.tmp"
        payload = {
            "token": credential.token,
            "expires_at": credential.expires_at,
            "username": credential.username,
            "saved_at": credential.saved_at,
        }
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as credential_file:
                json.dump(payload, credential_file, separators=(",", ":"))
                credential_file.write("\n")
                credential_file.flush()
                os.fsync(credential_file.fileno())
            os.replace(temporary, destination)
            try:
                os.chmod(destination, 0o600)
            except OSError:
                pass
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def delete(self, session_id: str) -> None:
        if not is_valid_session_id(session_id):
            return
        self._path_for(session_id).unlink(missing_ok=True)

    def prune(self, now: float | None = None) -> None:
        self._ensure_directory()
        current_time = time.time() if now is None else now
        if not math.isfinite(current_time):
            return
        for path in self.directory.glob(".*.tmp"):
            path.unlink(missing_ok=True)
        for path in self.directory.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                saved_at = self._finite_number(payload.get("saved_at")) if isinstance(payload, dict) else None
            except (OSError, json.JSONDecodeError):
                saved_at = None
            if saved_at is None or current_time - saved_at >= _CREDENTIAL_RETENTION_SECONDS:
                path.unlink(missing_ok=True)
