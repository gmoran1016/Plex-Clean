"""Regression coverage for Plex authentication rate limits."""

from __future__ import annotations

import asyncio
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

    async def test_strong_pin_auth_uses_tautulli_compatible_popup_flow(self) -> None:
        response = httpx.Response(200, json={"id": 12345, "code": "4821", "expiresIn": 600})
        plex_client = _FakeAsyncClient(response)
        service = PlexAuthService(Settings(Path("."), None, False), "test-client")
        session = AppSession()

        with patch("app.plex.auth.httpx.AsyncClient", return_value=plex_client):
            started = await service.start_pin(session)

        self.assertIsNotNone(plex_client.requested)
        request_options = plex_client.requested[2]
        self.assertEqual(plex_client.requested[1], "https://plex.tv/api/v2/pins")
        self.assertEqual(request_options["params"], {"strong": "true"})
        self.assertNotIn("json", request_options)
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
        request_count = 0
        request_started = asyncio.Event()
        release_request = asyncio.Event()

        async def pending_pin_response(*_: object, **__: object) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            request_started.set()
            await release_request.wait()
            return httpx.Response(200, json={})

        with patch.object(service, "_request", new=pending_pin_response):
            first = asyncio.create_task(service.poll_pin(session))
            await request_started.wait()
            second = asyncio.create_task(service.poll_pin(session))
            release_request.set()
            first_status, second_status = await asyncio.gather(first, second)

        self.assertEqual(request_count, 1)
        self.assertTrue(first_status.pending)
        self.assertTrue(second_status.pending)

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

    async def test_pin_poll_returns_a_legacy_token_without_a_device_jwt(self) -> None:
        pin_client = _FakeAsyncClient(httpx.Response(200, json={"authToken": "test-plex-token"}))
        profile_client = _FakeAsyncClient(httpx.Response(200, json={"username": "test-user"}))
        service = PlexAuthService(Settings(Path("."), None, False), "test-client")
        session = AppSession()
        session.pending_auth = PendingPlexAuth(12345, time.time() + 600)

        with (
            patch("app.plex.auth.httpx.AsyncClient", side_effect=[pin_client, profile_client]),
        ):
            status = await service.poll_pin(session)

        self.assertIsNotNone(pin_client.requested)
        self.assertEqual(pin_client.requested[1], "https://plex.tv/api/v2/pins/12345")
        self.assertNotIn("params", pin_client.requested[2])
        self.assertTrue(status.authenticated)
        self.assertIsNone(session.pending_auth)
        self.assertEqual(session.plex_token, "test-plex-token")
        self.assertIsNone(session.plex_token_expires_at)

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
        self.assertEqual(mode, 0)


if __name__ == "__main__":
    unittest.main()
