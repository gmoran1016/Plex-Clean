"""Regression coverage for Plex authentication rate limits."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import time
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import httpx
from fastapi import HTTPException

from app.auth_storage import PlexCredentialStore, SavedPlexCredential
from app.config import Settings
from app.plex.auth import PlexAuthService, PlexResourceService, _json_or_xml
from app.sessions import AppSession, PendingPlexAuth


class _FakeAsyncClient:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.requested: tuple[str, str, dict[str, object]] | None = None

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        self.requested = (method, url, kwargs)
        return self.response


class _FakePlexProbeClient:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = responses
        self.urls: list[str] = []

    async def __aenter__(self) -> _FakePlexProbeClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get(self, url: str, **_: object) -> httpx.Response:
        self.urls.append(url)
        return self.responses.pop(0)


class PlexAuthRateLimitTests(unittest.IsolatedAsyncioTestCase):
    def test_xml_resource_response_preserves_nested_device_connections(self) -> None:
        response = httpx.Response(
            200,
            content=(
                b'<MediaContainer size="1"><Device name="Test Plex Server" provides="server" '
                b'clientIdentifier="server-01" accessToken="server-token"><Connection '
                b'uri="http://plex.example:32400" local="1" protocol="http"/>'
                b'</Device></MediaContainer>'
            ),
            headers={"Content-Type": "application/xml"},
        )

        payload = _json_or_xml(response)

        self.assertEqual(payload["MediaContainer"]["Device"]["name"], "Test Plex Server")
        self.assertEqual(
            payload["MediaContainer"]["Device"]["Connection"]["uri"],
            "http://plex.example:32400",
        )

    def test_resource_json_array_is_normalized(self) -> None:
        response = httpx.Response(200, json=[{"provides": "server"}])

        payload = _json_or_xml(response)

        self.assertEqual(payload, {"resources": [{"provides": "server"}]})

    async def test_rate_limit_is_propagated_with_retry_after(self) -> None:
        response = httpx.Response(429, headers={"Retry-After": "45"})
        service = PlexAuthService(Settings(Path("."), None, False), "test-client")

        with patch("app.plex.auth.httpx.AsyncClient", return_value=_FakeAsyncClient(response)):
            with self.assertRaises(HTTPException) as raised:
                await service._request("GET", "https://clients.plex.tv/api/v2/pins/1")

        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.headers, {"Retry-After": "45"})

    async def test_strong_pin_auth_registers_device_key_with_plex(self) -> None:
        response = httpx.Response(200, json={"id": 12345, "code": "4821", "expiresIn": 600})
        plex_client = _FakeAsyncClient(response)
        service = PlexAuthService(Settings(Path("."), None, False), "test-client")
        session = AppSession()

        with patch("app.plex.auth.httpx.AsyncClient", return_value=plex_client):
            started = await service.start_pin(session)

        self.assertIsNotNone(plex_client.requested)
        request_options = plex_client.requested[2]
        self.assertEqual(plex_client.requested[1], "https://clients.plex.tv/api/v2/pins")
        self.assertEqual(request_options["json"], {"jwk": service._device_jwk(), "strong": True})
        self.assertNotIn("params", request_options)
        self.assertNotIn("Origin", request_options["headers"])
        self.assertEqual(request_options["headers"]["X-Plex-Platform"], "Web")
        self.assertEqual(request_options["headers"]["X-Plex-Platform-Version"], "1.0")
        self.assertEqual(request_options["headers"]["X-Plex-Device"], "Browser")
        self.assertEqual(request_options["headers"]["X-Plex-Device-Name"], "Plex-Clean")
        auth_parameters = parse_qs(urlsplit(started.authorization_url).fragment.lstrip("!?"))
        self.assertTrue(started.authorization_url.startswith("https://app.plex.tv/auth/#!?"))
        self.assertEqual(auth_parameters["clientID"], ["test-client"])
        self.assertEqual(auth_parameters["code"], ["4821"])
        self.assertEqual(auth_parameters["context[device][product]"], ["Plex-Clean"])
        self.assertEqual(auth_parameters["context[device][version]"], ["0.1.0"])
        self.assertEqual(auth_parameters["context[device][platform]"], ["Web"])
        self.assertEqual(auth_parameters["context[device][platformVersion]"], ["1.0"])
        self.assertEqual(auth_parameters["context[device][device]"], ["Browser"])
        self.assertEqual(auth_parameters["context[device][deviceName]"], ["Plex-Clean"])
        self.assertEqual(auth_parameters["context[device][model]"], ["Web"])
        self.assertEqual(auth_parameters["context[device][layout]"], ["desktop"])
        self.assertNotIn("forwardUrl", auth_parameters)
        self.assertEqual(started.pin_code, "4821")
        self.assertEqual(session.pending_auth.pin_id, 12345)

    async def test_parallel_pin_polls_share_one_plex_request(self) -> None:
        service = PlexAuthService(Settings(Path("."), None, False), "test-client")
        session = AppSession()
        session.pending_auth = PendingPlexAuth(12345, time.time() + 600)
        nonce_count = 0
        pin_poll_count = 0
        request_started = asyncio.Event()
        release_request = asyncio.Event()

        async def pending_pin_response(_method: str, url: str, **_: object) -> httpx.Response:
            nonlocal nonce_count, pin_poll_count
            if url.endswith("/auth/nonce"):
                nonce_count += 1
                request_started.set()
                await release_request.wait()
                return httpx.Response(200, json={"nonce": "test-nonce"})
            pin_poll_count += 1
            return httpx.Response(200, json={})

        with patch.object(service, "_request", new=pending_pin_response):
            first = asyncio.create_task(service.poll_pin(session))
            await request_started.wait()
            second = asyncio.create_task(service.poll_pin(session))
            release_request.set()
            first_status, second_status = await asyncio.gather(first, second)

        self.assertEqual(nonce_count, 1)
        self.assertEqual(pin_poll_count, 1)
        self.assertTrue(first_status.pending)
        self.assertTrue(second_status.pending)

    async def test_pin_poll_reuses_nonce_jwt_until_near_expiry(self) -> None:
        service = PlexAuthService(Settings(Path("."), None, False), "test-client")
        session = AppSession()
        session.pending_auth = PendingPlexAuth(12345, time.time() + 600)
        nonce_count = 0
        pin_poll_count = 0

        async def pending_pin_response(_method: str, url: str, **_: object) -> httpx.Response:
            nonlocal nonce_count, pin_poll_count
            if url.endswith("/auth/nonce"):
                nonce_count += 1
                return httpx.Response(200, json={"nonce": f"test-nonce-{nonce_count}"})
            pin_poll_count += 1
            return httpx.Response(200, json={})

        with patch.object(service, "_request", new=pending_pin_response):
            await service.poll_pin(session)
            first_jwt = session.pending_auth.device_jwt

            session.pending_auth.next_poll_at = 0
            await service.poll_pin(session)
            self.assertEqual(session.pending_auth.device_jwt, first_jwt)

            session.pending_auth.device_jwt_expires_at = time.time() + 1
            session.pending_auth.next_poll_at = 0
            await service.poll_pin(session)

        self.assertEqual(nonce_count, 2)
        self.assertEqual(pin_poll_count, 3)
        self.assertNotEqual(session.pending_auth.device_jwt, first_jwt)

    async def test_rate_limited_pin_poll_waits_for_plex_retry_after(self) -> None:
        service = PlexAuthService(Settings(Path("."), None, False), "test-client")
        session = AppSession()
        session.pending_auth = PendingPlexAuth(12345, time.time() + 600)
        started_at = time.monotonic()
        request_count = 0

        async def rate_limited_response(*_: object, **__: object) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            raise HTTPException(status_code=429, headers={"Retry-After": "45"})

        with patch.object(service, "_request", new=rate_limited_response):
            with self.assertRaises(HTTPException):
                await service.poll_pin(session)
            retry_status = await service.poll_pin(session)

        self.assertGreaterEqual(session.pending_auth.next_poll_at, started_at + 44)
        self.assertEqual(request_count, 1)
        self.assertTrue(retry_status.pending)

    async def test_pin_poll_exchanges_pin_with_a_signed_jwt_and_parses_xml(self) -> None:
        token_payload = base64.urlsafe_b64encode(
            json.dumps({"exp": int(time.time()) + 3600}).encode("utf-8")
        ).rstrip(b"=").decode("ascii")
        returned_token = f"eyJraWQiOiJ0ZXN0In0.{token_payload}.signature"
        service = PlexAuthService(Settings(Path("."), None, False), "test-client")
        session = AppSession()
        session.pending_auth = PendingPlexAuth(12345, time.time() + 600)
        requests: list[tuple[str, str, dict[str, object]]] = []

        async def plex_request(method: str, url: str, **kwargs: object) -> httpx.Response:
            requests.append((method, url, kwargs))
            if url == "https://clients.plex.tv/api/v2/auth/nonce":
                return httpx.Response(200, json={"nonce": "test-nonce"})
            if url == "https://clients.plex.tv/api/v2/pins/12345":
                return httpx.Response(
                    200,
                    content=f'<MediaContainer authToken="{returned_token}"/>'.encode("ascii"),
                    headers={"Content-Type": "application/xml"},
                )
            raise AssertionError(f"Unexpected Plex request: {method} {url}")

        async def load_profile(auth_session: AppSession) -> None:
            auth_session.username = "test-user"

        with (
            patch.object(service, "_request", new=plex_request),
            patch.object(service, "_load_account_profile", new=load_profile),
        ):
            status = await service.poll_pin(session)

        self.assertEqual(
            [(method, url) for method, url, _ in requests],
            [
                ("GET", "https://clients.plex.tv/api/v2/auth/nonce"),
                ("GET", "https://clients.plex.tv/api/v2/pins/12345"),
            ],
        )
        pin_request = requests[1][2]
        self.assertEqual(pin_request["headers"]["Accept"], "application/xml")
        device_jwt = pin_request["params"]["deviceJWT"]
        header_part, payload_part, signature_part = device_jwt.split(".")
        decode = lambda part: json.loads(
            base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
        )
        header = decode(header_part)
        payload = decode(payload_part)
        signing_input = f"{header_part}.{payload_part}".encode("ascii")
        service.device_private_key.public_key().verify(
            base64.urlsafe_b64decode(signature_part + "=" * (-len(signature_part) % 4)),
            signing_input,
        )
        self.assertEqual(header["alg"], "EdDSA")
        self.assertEqual(header["kid"], service.device_key_id)
        self.assertEqual(payload["aud"], "plex.tv")
        self.assertEqual(payload["iss"], "test-client")
        self.assertEqual(payload["nonce"], "test-nonce")
        self.assertEqual(payload["scope"], "username")
        self.assertTrue(status.authenticated)
        self.assertIsNone(session.pending_auth)
        self.assertEqual(session.plex_token, returned_token)
        self.assertGreater(session.plex_token_expires_at, time.time())

    async def test_account_profile_uses_plex_tv_api_host(self) -> None:
        response = httpx.Response(200, json={"username": "test-user"})
        plex_client = _FakeAsyncClient(response)
        service = PlexAuthService(Settings(Path("."), None, False), "test-client")
        session = AppSession(plex_token="test-plex-token")

        with patch("app.plex.auth.httpx.AsyncClient", return_value=plex_client):
            await service._load_account_profile(session)

        self.assertIsNotNone(plex_client.requested)
        self.assertEqual(plex_client.requested[1], "https://plex.tv/api/v2/user")
        self.assertEqual(session.username, "test-user")

    async def test_server_discovery_retries_canonical_host_after_resource_401(self) -> None:
        rejected = _FakeAsyncClient(httpx.Response(401))
        accepted = _FakeAsyncClient(httpx.Response(200, json={"MediaContainer": {"resources": []}}))
        service = PlexAuthService(Settings(Path("."), None, False), "test-client")
        session = AppSession(plex_token="test-plex-token", plex_token_expires_at=time.time() + 3600)

        with patch("app.plex.auth.httpx.AsyncClient", side_effect=[rejected, accepted]):
            servers = await PlexResourceService(service).list_servers(session)

        self.assertEqual(servers, [])
        self.assertEqual(rejected.requested[1], "https://clients.plex.tv/api/v2/resources")
        self.assertEqual(accepted.requested[1], "https://plex.tv/api/v2/resources")

    async def test_empty_clients_resources_retry_plex_tv_before_configured_probe(self) -> None:
        resources = [
            httpx.Response(200, json={"MediaContainer": {"resources": []}}),
            httpx.Response(
                200,
                json={
                    "MediaContainer": {
                        "Device": [
                            {
                                "name": "Plex from canonical host",
                                "provides": "server",
                                "clientIdentifier": "server-04",
                                "accessToken": "server-token",
                                "connections": [
                                    {
                                        "uri": "http://plex.example:32400",
                                        "local": True,
                                        "relay": False,
                                        "protocol": "http",
                                    }
                                ],
                            }
                        ]
                    }
                },
            ),
        ]
        probe = _FakePlexProbeClient(
            [
                httpx.Response(200, json={"MediaContainer": {"machineIdentifier": "server-04"}}),
                httpx.Response(200, json={"MediaContainer": {"allowMediaDeletion": 0}}),
            ]
        )

        class _ResourceAuthStub:
            settings = Settings(Path("."), None, False)

            def __init__(self) -> None:
                self.requested_hosts: list[str] = []

            async def refresh_if_needed(self, session: AppSession) -> None:
                return None

            async def _account_request(
                self,
                session: AppSession,
                path: str,
                *,
                api_base: str = "https://clients.plex.tv/api/v2",
                **_: object,
            ) -> httpx.Response:
                self.requested_hosts.append(api_base)
                return resources.pop(0)

        auth = _ResourceAuthStub()
        session = AppSession(plex_token="account-token", plex_token_expires_at=time.time() + 3600)
        with patch("app.plex.auth.httpx.AsyncClient", return_value=probe):
            servers = await PlexResourceService(auth).list_servers(session)

        self.assertEqual(auth.requested_hosts, ["https://clients.plex.tv/api/v2", "https://plex.tv/api/v2"])
        self.assertEqual([server.machine_identifier for server in servers], ["server-04"])

    async def test_server_discovery_accepts_documented_device_collection(self) -> None:
        resource_response = httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "size": 1,
                    "Device": [
                        {
                            "name": "Test Plex Server",
                            "provides": "server",
                            "clientIdentifier": "server-01",
                            "accessToken": "server-token",
                            "connections": [],
                        }
                    ],
                }
            },
        )
        probe = _FakePlexProbeClient(
            [
                httpx.Response(200, json={"MediaContainer": {"machineIdentifier": "server-01"}}),
                httpx.Response(200, json={"MediaContainer": {"allowMediaDeletion": 1, "version": "1.40"}}),
            ]
        )

        class _ResourceAuthStub:
            settings = Settings(Path("."), "http://plex.example:32400", False)

            async def refresh_if_needed(self, session: AppSession) -> None:
                return None

            async def _account_request(self, session: AppSession, path: str, **_: object) -> httpx.Response:
                if path != "resources":
                    raise AssertionError(f"Unexpected Plex account path: {path}")
                return resource_response

        session = AppSession(plex_token="account-token", plex_token_expires_at=time.time() + 3600)
        with patch("app.plex.auth.httpx.AsyncClient", return_value=probe):
            servers = await PlexResourceService(_ResourceAuthStub()).list_servers(session)

        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0].machine_identifier, "server-01")
        self.assertEqual(servers[0].name, "Test Plex Server")
        self.assertTrue(servers[0].allow_media_deletion)
        self.assertEqual(session.server_access["server-01"].access_token, "server-token")
        self.assertEqual(probe.urls, ["http://plex.example:32400/identity", "http://plex.example:32400/"])

    async def test_jwt_resource_token_is_replaced_with_legacy_device_token(self) -> None:
        responses = {
            "resources": httpx.Response(
                200,
                json={
                    "MediaContainer": {
                        "Device": [
                            {
                                "name": "Test Plex Server",
                                "provides": "server",
                                "clientIdentifier": "server-05",
                                "accessToken": "header.payload.signature",
                                "connections": [
                                    {
                                        "uri": "http://plex.example:32400",
                                        "local": True,
                                        "relay": False,
                                    }
                                ],
                            }
                        ]
                    }
                },
            ),
            "devices": httpx.Response(
                200,
                json={
                    "MediaContainer": {
                        "Device": [
                            {
                                "provides": "server",
                                "clientIdentifier": "server-05",
                                "accessToken": "legacy-server-token",
                            }
                        ]
                    }
                },
            ),
        }
        probe = _FakePlexProbeClient(
            [
                httpx.Response(200, json={"MediaContainer": {"machineIdentifier": "server-05"}}),
                httpx.Response(200, json={"MediaContainer": {"allowMediaDeletion": 0}}),
            ]
        )

        class _ResourceAuthStub:
            settings = Settings(Path("."), None, False)

            def __init__(self) -> None:
                self.requested_paths: list[str] = []

            async def refresh_if_needed(self, session: AppSession) -> None:
                return None

            async def _account_request(self, session: AppSession, path: str, **_: object) -> httpx.Response:
                self.requested_paths.append(path)
                return responses[path]

        auth = _ResourceAuthStub()
        session = AppSession(plex_token="account-jwt", plex_token_expires_at=time.time() + 3600)
        with patch("app.plex.auth.httpx.AsyncClient", return_value=probe):
            servers = await PlexResourceService(auth).list_servers(session)

        self.assertEqual(auth.requested_paths, ["resources", "devices"])
        self.assertEqual([server.machine_identifier for server in servers], ["server-05"])
        self.assertEqual(session.server_access["server-05"].access_token, "legacy-server-token")

    async def test_configured_server_is_verified_when_plex_resources_are_empty(self) -> None:
        resource_response = httpx.Response(200, json={"MediaContainer": {"size": 0, "resources": []}})
        probe = _FakePlexProbeClient(
            [
                httpx.Response(200, json={"MediaContainer": {"machineIdentifier": "server-02"}}),
                httpx.Response(
                    200,
                    json={"MediaContainer": {"friendlyName": "Configured Plex", "allowMediaDeletion": 1}},
                ),
            ]
        )

        class _ResourceAuthStub:
            settings = Settings(Path("."), "http://plex.example:32400", False)

            async def refresh_if_needed(self, session: AppSession) -> None:
                return None

            async def _account_request(self, session: AppSession, path: str, **_: object) -> httpx.Response:
                if path != "resources":
                    raise AssertionError(f"Unexpected Plex account path: {path}")
                return resource_response

        session = AppSession(plex_token="account-token", plex_token_expires_at=time.time() + 3600)
        with patch("app.plex.auth.httpx.AsyncClient", return_value=probe):
            servers = await PlexResourceService(_ResourceAuthStub()).list_servers(session)

        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0].machine_identifier, "server-02")
        self.assertEqual(servers[0].name, "Configured Plex")
        self.assertTrue(servers[0].allow_media_deletion)
        self.assertEqual(session.server_access["server-02"].access_token, "account-token")
        self.assertEqual(session.selected_server_id, "server-02")
        self.assertEqual(probe.urls, ["http://plex.example:32400/identity", "http://plex.example:32400/"])

    async def test_configured_server_requires_authenticated_root_response(self) -> None:
        resource_response = httpx.Response(200, json={"MediaContainer": {"resources": []}})
        probe = _FakePlexProbeClient(
            [
                httpx.Response(200, json={"MediaContainer": {"machineIdentifier": "server-03"}}),
                httpx.Response(401),
            ]
        )

        class _ResourceAuthStub:
            settings = Settings(Path("."), "http://plex.example:32400", False)

            async def refresh_if_needed(self, session: AppSession) -> None:
                return None

            async def _account_request(self, session: AppSession, path: str, **_: object) -> httpx.Response:
                if path != "resources":
                    raise AssertionError(f"Unexpected Plex account path: {path}")
                return resource_response

        session = AppSession(plex_token="account-token", plex_token_expires_at=time.time() + 3600)
        with patch("app.plex.auth.httpx.AsyncClient", return_value=probe):
            servers = await PlexResourceService(_ResourceAuthStub()).list_servers(session)

        self.assertEqual(servers, [])
        self.assertEqual(session.server_access, {})
        self.assertIsNone(session.selected_server_id)

    def test_device_signing_key_persists_with_private_file_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(Path(directory), None, False)
            first = settings.load_or_create_plex_device_key()
            second = settings.load_or_create_plex_device_key()

            first_public = first.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
            second_public = second.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
            key_path = Path(directory) / "plex-device-ed25519.pem"
            mode = key_path.stat().st_mode & 0o077

        self.assertEqual(first_public, second_public)
        if os.name == "posix":
            self.assertEqual(mode, 0)

    def test_auth_identity_migration_uses_a_fresh_stable_pair_without_saved_logins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(Path(directory), None, False)
            previous_client_id = settings.load_or_create_client_identifier()
            previous_key = settings.load_or_create_plex_device_key()
            credentials = PlexCredentialStore(Path(directory))

            client_id, key = settings.load_or_create_plex_auth_identity(
                preserve_existing_identity=credentials.has_saved_credentials()
            )
            restarted_client_id, restarted_key = settings.load_or_create_plex_auth_identity(
                preserve_existing_identity=credentials.has_saved_credentials()
            )

            previous_public_key = previous_key.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
            public_key = key.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
            restarted_public_key = restarted_key.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )

        self.assertNotEqual(client_id, previous_client_id)
        self.assertNotEqual(public_key, previous_public_key)
        self.assertEqual(restarted_client_id, client_id)
        self.assertEqual(restarted_public_key, public_key)

    def test_auth_identity_migration_preserves_identity_with_saved_browser_logins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(Path(directory), None, False)
            client_id = settings.load_or_create_client_identifier()
            key = settings.load_or_create_plex_device_key()
            credentials = PlexCredentialStore(Path(directory))
            credentials.save(
                "A" * 43,
                SavedPlexCredential("saved-token", None, "test-user", time.time()),
            )

            selected_client_id, selected_key = settings.load_or_create_plex_auth_identity(
                preserve_existing_identity=credentials.has_saved_credentials()
            )
            restarted_client_id, restarted_key = settings.load_or_create_plex_auth_identity(
                preserve_existing_identity=False
            )

            public_key = key.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
            selected_public_key = selected_key.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
            restarted_public_key = restarted_key.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )

        self.assertEqual(selected_client_id, client_id)
        self.assertEqual(selected_public_key, public_key)
        self.assertEqual(restarted_client_id, client_id)
        self.assertEqual(restarted_public_key, public_key)


if __name__ == "__main__":
    unittest.main()
