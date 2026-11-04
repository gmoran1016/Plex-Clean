"""Environment-backed configuration and stable, non-secret app identity."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _as_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("SESSION_COOKIE_SECURE must be a boolean value")


@dataclass(frozen=True, slots=True)
class Settings:
    config_dir: Path
    plex_server_url: str | None
    session_cookie_secure: bool
    session_cookie_name: str = "plexclean_session"
    session_idle_timeout_seconds: int = 24 * 60 * 60

    @classmethod
    def from_environment(cls) -> Settings:
        config_dir = Path(os.environ.get("PLEX_CONFIG_DIR", "./data")).expanduser()
        server_url = os.environ.get("PLEX_SERVER_URL", "").strip() or None
        if server_url is not None:
            parsed = urlsplit(server_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("PLEX_SERVER_URL must be an http(s) URL")
            if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
                raise ValueError("PLEX_SERVER_URL must be a plain server origin without credentials, path, or query")
            server_url = server_url.rstrip("/")
        return cls(
            config_dir=config_dir,
            plex_server_url=server_url,
            session_cookie_secure=_as_bool(os.environ.get("SESSION_COOKIE_SECURE")),
        )

    def load_or_create_client_identifier(self) -> str:
        """Return a stable UUID stored under the non-secret config directory."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        return self._load_or_create_client_identifier_at(
            self.config_dir / "plex-client-id"
        )

    def load_or_create_plex_auth_identity(
        self,
        *,
        preserve_existing_identity: bool,
    ) -> tuple[str, Ed25519PrivateKey]:
        """Load the selected Plex identity, upgrading unauthenticated installs once."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        marker = self.config_dir / "plex-auth-identity-version"
        version = self._load_or_create_auth_identity_version(
            marker,
            preserve_existing_identity=preserve_existing_identity,
        )
        if version == "1":
            return self.load_or_create_client_identifier(), self.load_or_create_plex_device_key()
        return (
            self._load_or_create_client_identifier_at(self.config_dir / "plex-client-id-v2"),
            self._load_or_create_plex_device_key_at(
                self.config_dir / "plex-device-ed25519-v2.pem"
            ),
        )

    @staticmethod
    def _load_or_create_auth_identity_version(
        path: Path,
        *,
        preserve_existing_identity: bool,
    ) -> str:
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            version = path.read_text(encoding="ascii").strip()
            if version not in {"1", "2"}:
                raise RuntimeError(f"Plex auth identity version file is invalid: {path}")
            return version

        version = "1" if preserve_existing_identity else "2"
        try:
            with os.fdopen(descriptor, "w", encoding="ascii") as version_file:
                version_file.write(version + "\n")
                version_file.flush()
                os.fsync(version_file.fileno())
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return version

    @staticmethod
    def _load_or_create_client_identifier_at(path: Path) -> str:
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            value = path.read_text(encoding="ascii").strip()
            if not value:
                raise RuntimeError(f"Plex client identifier file is empty: {path}")
            try:
                return str(uuid.UUID(value))
            except ValueError as exc:
                raise RuntimeError(f"Plex client identifier file is invalid: {path}") from exc

        value = str(uuid.uuid4())
        try:
            with os.fdopen(descriptor, "w", encoding="ascii") as client_id_file:
                client_id_file.write(value + "\n")
                client_id_file.flush()
                os.fsync(client_id_file.fileno())
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return value

    def load_or_create_plex_device_key(self) -> Ed25519PrivateKey:
        """Load the app's stable Ed25519 signing key from its private config volume."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        return self._load_or_create_plex_device_key_at(
            self.config_dir / "plex-device-ed25519.pem"
        )

    @staticmethod
    def _load_or_create_plex_device_key_at(path: Path) -> Ed25519PrivateKey:
        try:
            encoded = path.read_bytes()
        except FileNotFoundError:
            key = Ed25519PrivateKey.generate()
            encoded = key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                encoded = path.read_bytes()
            else:
                try:
                    with os.fdopen(descriptor, "wb") as key_file:
                        key_file.write(encoded)
                        key_file.flush()
                        os.fsync(key_file.fileno())
                except Exception:
                    path.unlink(missing_ok=True)
                    raise

        try:
            key = serialization.load_pem_private_key(encoded, password=None)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Plex device signing key is invalid: {path}") from exc
        if not isinstance(key, Ed25519PrivateKey):
            raise RuntimeError(f"Plex device signing key has an unsupported type: {path}")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return key
