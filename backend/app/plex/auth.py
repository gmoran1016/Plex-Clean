"""Plex PIN-token authentication and reachable server discovery."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import math
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import httpx
from fastapi import HTTPException

from app.config import Settings
from app.models import AuthStart, AuthStatus, ServerConnectionSummary, ServerOption
from app.sessions import AppSession, PendingPlexAuth, ServerAccess

PLEX_API = "https://clients.plex.tv/api/v2"
PLEX_TV_API = "https://plex.tv/api/v2"
PLEX_JWT_LIFETIME_SECONDS = 7 * 24 * 60 * 60
PLEX_JWT_REFRESH_WINDOW_SECONDS = 24 * 60 * 60
PIN_POLL_INTERVAL_SECONDS = 2.0
PLEX_AUTH_REJECTED_STATUSES = frozenset({401, 403, 498})
logger = logging.getLogger(__name__)


def _json_or_xml(response: httpx.Response) -> dict[str, Any]:
    try:
        value = response.json()
        if isinstance(value, dict):
            return value
        return {"resources": value} if isinstance(value, list) else {}
    except ValueError:
        try:
            root = ET.fromstring(response.content)
            return {"MediaContainer": _xml_element_data(root)}
        except ET.ParseError:
            return {}


def _xml_element_data(element: ET.Element) -> dict[str, Any]:
    """Convert Plex XML attributes and nested elements into the API's JSON shape."""
    result: dict[str, Any] = dict(element.attrib)
    for child in element:
        name = child.tag.rsplit("}", 1)[-1]
        value = _xml_element_data(child)
        previous = result.get(name)
        if previous is None:
            result[name] = value
        elif isinstance(previous, list):
            previous.append(value)
        else:
            result[name] = [previous, value]
    return result


def _container(data: dict[str, Any]) -> dict[str, Any]:
    candidate = data.get("MediaContainer", data)
    return candidate if isinstance(candidate, dict) else {}


def _resource_entries(payload: dict[str, Any]) -> tuple[dict[str, Any], list[Any]]:
    resources = payload.get("MediaContainer", payload)
    if not isinstance(resources, dict):
        return {}, []
    raw_resources = (
        resources.get("resources")
        or resources.get("Resource")
        or resources.get("devices")
        or resources.get("Device")
        or resources.get("device")
        or payload.get("resources")
    )
    if isinstance(raw_resources, dict):
        raw_resources = [raw_resources]
    return resources, raw_resources if isinstance(raw_resources, list) else []


def _is_jwt_token(value: Any) -> bool:
    return isinstance(value, str) and value.count(".") == 2


def _jwt_expiration(value: str) -> float | None:
    if not _is_jwt_token(value):
        return None
    try:
        payload_part = value.split(".", 2)[1]
        payload_json = base64.urlsafe_b64decode(payload_part + "=" * (-len(payload_part) % 4))
        payload = json.loads(payload_json)
    except (binascii.Error, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    expiration = payload.get("exp")
    if isinstance(expiration, bool) or not isinstance(expiration, (int, float)):
        return None
    try:
        result = float(expiration)
    except OverflowError:
        return None
    return result if math.isfinite(result) else None


def _device_access_token(device: dict[str, Any]) -> str | None:
    for key in ("accessToken", "access_token", "token", "authToken"):
        value = device.get(key)
        if value:
            return str(value)
    return None


class PlexAuthService:
    def __init__(
        self,
        settings: Settings,
        client_identifier: str,
        device_private_key: Ed25519PrivateKey | None = None,
    ) -> None:
        self.settings = settings
        self.client_identifier = client_identifier
        self.device_private_key = device_private_key or Ed25519PrivateKey.generate()
        public_bytes = self.device_private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        self.device_key_id = hashlib.sha256(public_bytes).hexdigest()

    @staticmethod
    def _base64url(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    def _device_jwk(self) -> dict[str, str]:
        public_bytes = self.device_private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        return {
            "kty": "OKP",
            "crv": "Ed25519",
            "x": self._base64url(public_bytes),
            "kid": self.device_key_id,
            "alg": "EdDSA",
            "use": "sig",
        }

    def _signed_device_jwt(self, nonce: str | None = None) -> str:
        issued_at = int(time.time())
        header = {"alg": "EdDSA", "typ": "JWT", "kid": self.device_key_id}
        payload = {
            "aud": "plex.tv",
            "iss": self.client_identifier,
            "iat": issued_at,
            "exp": issued_at + 300,
        }
        if nonce is not None:
            payload["nonce"] = nonce
            payload["scope"] = "username"
        signing_input = ".".join(
            (
                self._base64url(json.dumps(header, separators=(",", ":")).encode("utf-8")),
                self._base64url(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
            )
        )
        signature = self.device_private_key.sign(signing_input.encode("ascii"))
        return f"{signing_input}.{self._base64url(signature)}"

    async def _request(
        self,
        method: str,
        url: str,
        *,
        allow_unauthorized: bool = False,
        allowed_statuses: frozenset[int] = frozenset(),
        **kwargs: Any,
    ) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}))
        headers.update(
            {
                "Accept": "application/json",
                "X-Plex-Client-Identifier": self.client_identifier,
                "X-Plex-Product": "Plex-Clean",
                "X-Plex-Version": "0.1.0",
                "X-Plex-Platform": "Web",
                "X-Plex-Platform-Version": "1.0",
                "X-Plex-Device": "Browser",
                "X-Plex-Device-Name": "Plex-Clean",
                "X-Plex-Model": "Web",
            }
        )
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
                response = await client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="Could not reach Plex authentication services.") from exc
        if response.status_code == 429:
            retry_headers = {}
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                retry_headers["Retry-After"] = retry_after
            raise HTTPException(
                status_code=429,
                detail="Plex temporarily rate-limited this request.",
                headers=retry_headers or None,
            )
        allowed = response.status_code in allowed_statuses or (
            allow_unauthorized and response.status_code == 401
        )
        if response.status_code >= 400 and not allowed:
            raise HTTPException(
                status_code=502,
                detail="Plex could not complete sign-in. Try again shortly.",
            )
        return response

    async def start_pin(self, session: AppSession) -> AuthStart:
        response = await self._request(
            "POST",
            f"{PLEX_API}/pins",
            json={"jwk": self._device_jwk(), "strong": True},
        )
        payload = _json_or_xml(response)
        try:
            pin_id = int(payload["id"])
            code = str(payload["code"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=502, detail="Plex returned an invalid sign-in PIN.") from exc

        expiration = payload.get("expiresAt") or payload.get("expires_at")
        try:
            if expiration:
                expires_at_dt = datetime.fromisoformat(str(expiration).replace("Z", "+00:00"))
                if expires_at_dt.tzinfo is None:
                    expires_at_dt = expires_at_dt.replace(tzinfo=UTC)
                expiry = expires_at_dt.timestamp()
            else:
                expiry = time.time() + int(payload.get("expiresIn", 600))
        except (ValueError, TypeError):
            expiry = time.time() + 600

        session.pending_auth = PendingPlexAuth(pin_id, expiry)
        session.plex_token = None
        session.plex_token_expires_at = None
        session.username = None
        session.server_options.clear()
        session.server_access.clear()
        session.selected_server_id = None

        authorization_parameters = urlencode(
            {
                "clientID": self.client_identifier,
                "context[device][product]": "Plex-Clean",
                "context[device][version]": "0.1.0",
                "context[device][platform]": "Web",
                "context[device][platformVersion]": "1.0",
                "context[device][device]": "Browser",
                "context[device][deviceName]": "Plex-Clean",
                "context[device][model]": "Web",
                "context[device][layout]": "desktop",
                "code": code,
            }
        )
        return AuthStart(
            authorization_url=f"https://app.plex.tv/auth/#!?{authorization_parameters}",
            pin_code=code,
            expires_at=datetime.fromtimestamp(expiry, tz=UTC),
        )

    async def poll_pin(self, session: AppSession) -> AuthStatus:
        pending = session.pending_auth
        if pending is None:
            return AuthStatus(authenticated=session.authenticated, pending=False, username=session.username)
        async with pending.poll_lock:
            if session.pending_auth is not pending:
                return AuthStatus(
                    authenticated=session.authenticated,
                    pending=session.pending_auth is not None,
                    username=session.username,
                )
            if pending.expires_at <= time.time():
                session.pending_auth = None
                return AuthStatus(pending=False, message="Plex sign-in PIN expired. Start again to get a new PIN.")

            now = time.monotonic()
            if now < pending.next_poll_at:
                return AuthStatus(pending=True)
            pending.next_poll_at = now + PIN_POLL_INTERVAL_SECONDS
            try:
                response = await self._request(
                    "GET",
                    f"{PLEX_API}/pins/{pending.pin_id}",
                    params={"deviceJWT": self._signed_device_jwt()},
                )
            except HTTPException as exc:
                if exc.status_code == 429:
                    retry_after = (exc.headers or {}).get("Retry-After")
                    try:
                        delay = float(retry_after) if retry_after is not None else 60.0
                    except ValueError:
                        delay = 60.0
                    if not math.isfinite(delay):
                        delay = 60.0
                    pending.next_poll_at = time.monotonic() + max(1.0, delay)
                raise

            payload = _json_or_xml(response)
            token = payload.get("authToken") or payload.get("auth_token")
            if not token:
                return AuthStatus(pending=True)

            session.plex_token = str(token)
            session.plex_token_expires_at = _jwt_expiration(session.plex_token)
            session.pending_auth = None
            await self._load_account_profile(session)
            return AuthStatus(authenticated=True, pending=False, username=session.username)

    async def _account_request(
        self,
        session: AppSession,
        path: str,
        *,
        api_base: str = PLEX_API,
        allow_unauthorized: bool = False,
        refresh_expired_token: bool = True,
    ) -> httpx.Response:
        if not session.plex_token:
            raise HTTPException(status_code=401, detail="Sign in to Plex to continue.")
        request_url = f"{api_base}/{path.lstrip('/')}"
        request_params = (
            {"includeHttps": 1, "includeRelay": 1, "includeIPv6": 1}
            if path.strip("/") == "resources"
            else None
        )
        response = await self._request(
            "GET",
            request_url,
            headers={"X-Plex-Token": session.plex_token},
            params=request_params,
            allow_unauthorized=allow_unauthorized,
            allowed_statuses=frozenset({498}),
        )
        if response.status_code == 498 and refresh_expired_token:
            await self._refresh_account_token(session)
            response = await self._request(
                "GET",
                request_url,
                headers={"X-Plex-Token": session.plex_token or ""},
                params=request_params,
                allow_unauthorized=allow_unauthorized,
                allowed_statuses=frozenset({498}),
            )
            if response.status_code in {401, 498}:
                session.clear_sensitive()
                raise HTTPException(status_code=401, detail="Plex sign-in expired. Sign in again.")
        return response

    async def _load_account_profile(self, session: AppSession) -> None:
        try:
            response = await self._account_request(session, "user", api_base=PLEX_TV_API)
            payload = _json_or_xml(response)
            session.username = str(payload.get("username") or payload.get("title") or "Plex user")
        except HTTPException:
            # Authentication succeeded; profile lookup is optional for the UI.
            session.username = "Plex user"

    async def refresh_if_needed(self, session: AppSession) -> None:
        if not session.plex_token:
            raise HTTPException(status_code=401, detail="Plex sign-in expired. Sign in again.")
        if not _is_jwt_token(session.plex_token):
            return
        now = time.time()
        expires_at = session.plex_token_expires_at or _jwt_expiration(session.plex_token)
        if expires_at is None:
            expires_at = now + PLEX_JWT_LIFETIME_SECONDS
        session.plex_token_expires_at = expires_at
        if expires_at > now + PLEX_JWT_REFRESH_WINDOW_SECONDS:
            return

        await self._refresh_account_token(session)

    async def _refresh_account_token(self, session: AppSession) -> None:
        if not session.plex_token:
            raise HTTPException(status_code=401, detail="Plex sign-in expired. Sign in again.")
        nonce_response = await self._request("GET", f"{PLEX_API}/auth/nonce")
        nonce = _json_or_xml(nonce_response).get("nonce")
        if not isinstance(nonce, str) or not nonce:
            raise HTTPException(status_code=502, detail="Plex could not prepare a secure token refresh.")

        token_response = await self._request(
            "POST",
            f"{PLEX_API}/auth/token",
            json={"jwt": self._signed_device_jwt(nonce)},
            allowed_statuses=PLEX_AUTH_REJECTED_STATUSES,
        )
        if token_response.status_code in PLEX_AUTH_REJECTED_STATUSES:
            session.clear_sensitive()
            raise HTTPException(status_code=401, detail="Plex sign-in expired. Sign in again.")

        refreshed_payload = _json_or_xml(token_response)
        refreshed_token = refreshed_payload.get("auth_token") or refreshed_payload.get("authToken")
        if not isinstance(refreshed_token, str) or not refreshed_token:
            raise HTTPException(status_code=502, detail="Plex returned an invalid refreshed token.")
        session.plex_token = refreshed_token
        session.plex_token_expires_at = _jwt_expiration(refreshed_token) or (
            time.time() + PLEX_JWT_LIFETIME_SECONDS
        )


class PlexResourceService:
    def __init__(self, auth: PlexAuthService) -> None:
        self.auth = auth

    async def list_servers(self, session: AppSession) -> list[ServerOption]:
        await self.auth.refresh_if_needed(session)
        response = await self.auth._account_request(session, "resources", allow_unauthorized=True)
        resource_api_base = PLEX_API
        used_canonical_resource_host = False
        if response.status_code == 401:
            response = await self.auth._account_request(
                session,
                "resources",
                api_base=PLEX_TV_API,
                allow_unauthorized=True,
            )
            resource_api_base = PLEX_TV_API
            used_canonical_resource_host = True
        if response.status_code == 401:
            await self.auth._refresh_account_token(session)
            response = await self.auth._account_request(
                session,
                "resources",
                api_base=resource_api_base,
                allow_unauthorized=True,
                refresh_expired_token=False,
            )
            if response.status_code in {401, 498}:
                session.clear_sensitive()
                raise HTTPException(status_code=401, detail="Plex sign-in expired. Sign in again.")
        if response.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail="Plex could not return the servers linked to this account. Try again shortly.",
            )
        payload = _json_or_xml(response)
        resources, raw_resources = _resource_entries(payload)
        if not raw_resources and not used_canonical_resource_host:
            canonical_response = await self.auth._account_request(
                session,
                "resources",
                api_base=PLEX_TV_API,
                allow_unauthorized=True,
            )
            if canonical_response.status_code != 401 and canonical_response.status_code < 400:
                response = canonical_response
                resource_api_base = PLEX_TV_API
                payload = _json_or_xml(response)
                resources, raw_resources = _resource_entries(payload)

        jwt_server_resources = [
            resource
            for resource in raw_resources
            if isinstance(resource, dict)
            and "server" in str(resource.get("provides", "")).split(",")
            and _is_jwt_token(resource.get("accessToken") or session.plex_token)
        ]
        legacy_device_tokens: dict[str, str] = {}
        device_lookup_result = "not-needed"
        if jwt_server_resources:
            device_response = await self.auth._account_request(
                session,
                "devices",
                api_base=resource_api_base,
                allow_unauthorized=True,
            )
            if device_response.status_code == 401 and resource_api_base != PLEX_TV_API:
                device_response = await self.auth._account_request(
                    session,
                    "devices",
                    api_base=PLEX_TV_API,
                    allow_unauthorized=True,
                )
            device_lookup_result = f"http-{device_response.status_code}"
            if device_response.status_code < 400:
                _, devices = _resource_entries(_json_or_xml(device_response))
                for device in devices:
                    if not isinstance(device, dict):
                        continue
                    machine_id = str(device.get("clientIdentifier") or device.get("machineIdentifier") or "")
                    token = _device_access_token(device)
                    if (
                        machine_id
                        and token
                        and not _is_jwt_token(token)
                        and "server" in str(device.get("provides", "")).split(",")
                    ):
                        legacy_device_tokens[machine_id] = token

        servers: list[ServerOption] = []
        accesses: dict[str, ServerAccess] = {}
        server_resource_count = 0
        connection_candidate_count = 0
        identity_status_counts: dict[int, int] = {}
        matching_identity_count = 0
        mismatched_identity_count = 0
        network_failure_count = 0
        configured_probe_result = "not-needed"
        for resource in raw_resources:
            if not isinstance(resource, dict):
                continue
            provides = str(resource.get("provides", ""))
            if "server" not in provides.split(","):
                continue
            server_resource_count += 1
            machine_id = str(resource.get("clientIdentifier") or resource.get("machineIdentifier") or "")
            if not machine_id:
                continue
            token = _device_access_token(resource) or session.plex_token
            if _is_jwt_token(token):
                token = legacy_device_tokens.get(machine_id)
            if not token:
                continue
            raw_connections = resource.get("connections") or resource.get("Connection") or []
            if isinstance(raw_connections, dict):
                raw_connections = [raw_connections]
            connections = [item for item in raw_connections if isinstance(item, dict)] if isinstance(raw_connections, list) else []
            connections.sort(key=lambda item: (bool(item.get("relay")), not bool(item.get("local"))))
            if self.auth.settings.plex_server_url:
                override_source = connections[0] if connections else {}
                connection_candidates = [{**override_source, "uri": self.auth.settings.plex_server_url, "relay": False}]
            else:
                connection_candidates = connections
            connection_candidate_count += len(connection_candidates)

            reachable: tuple[str, bool, bool, str | None] | None = None
            deletion_allowed: bool | None = None
            version: str | None = str(resource.get("productVersion")) if resource.get("productVersion") else None
            for connection in connection_candidates:
                uri = str(connection.get("uri", "")).rstrip("/")
                if not uri.startswith(("http://", "https://")):
                    continue
                # Resource URLs are sent back to the UI as safe connection
                # summaries. Never allow embedded user info or query secrets.
                parsed_uri = urlsplit(uri)
                if parsed_uri.username or parsed_uri.password or parsed_uri.query or parsed_uri.fragment:
                    continue
                uri = urlunsplit((parsed_uri.scheme, parsed_uri.netloc, parsed_uri.path.rstrip("/"), "", ""))
                try:
                    async with httpx.AsyncClient(timeout=4.0, follow_redirects=False) as client:
                        identity = await client.get(
                            f"{uri}/identity",
                            headers={"Accept": "application/json", "X-Plex-Token": str(token)},
                        )
                        if identity.status_code >= 400:
                            identity_status_counts[identity.status_code] = identity_status_counts.get(identity.status_code, 0) + 1
                            continue
                        info = _container(_json_or_xml(identity))
                        actual_id = str(info.get("machineIdentifier", machine_id))
                        if actual_id != machine_id:
                            mismatched_identity_count += 1
                            continue
                        matching_identity_count += 1
                        root = await client.get(
                            f"{uri}/",
                            headers={"Accept": "application/json", "X-Plex-Token": str(token)},
                        )
                        if root.status_code >= 400:
                            identity_status_counts[root.status_code] = identity_status_counts.get(root.status_code, 0) + 1
                            continue
                        root_info = _container(_json_or_xml(root))
                        flag = root_info.get("allowMediaDeletion")
                        if flag is not None:
                            deletion_allowed = str(flag).lower() in {"1", "true", "yes"}
                        version = str(root_info.get("version", version or "")) or version
                        reachable = (
                            uri,
                            bool(connection.get("local", False)),
                            bool(connection.get("relay", False)),
                            str(connection.get("protocol")) if connection.get("protocol") else None,
                        )
                        break
                except httpx.HTTPError:
                    network_failure_count += 1
                    continue
            if reachable is None:
                continue
            uri, local, relay, protocol = reachable
            servers.append(
                ServerOption(
                    machine_identifier=machine_id,
                    name=str(resource.get("name") or resource.get("product") or "Plex Media Server"),
                    product_version=version,
                    connections=[ServerConnectionSummary(uri=uri, local=local, relay=relay, protocol=protocol)],
                    allow_media_deletion=deletion_allowed,
                )
            )
            accesses[machine_id] = ServerAccess(machine_id, servers[-1].name, uri, str(token), local, relay)

        if not servers and self.auth.settings.plex_server_url and session.plex_token:
            uri = self.auth.settings.plex_server_url.rstrip("/")
            headers = {"Accept": "application/json", "X-Plex-Token": session.plex_token}
            configured_probe_result = "network-error"
            try:
                async with httpx.AsyncClient(timeout=4.0, follow_redirects=False) as client:
                    identity = await client.get(f"{uri}/identity", headers=headers)
                    if identity.status_code >= 400:
                        configured_probe_result = f"identity-http-{identity.status_code}"
                    else:
                        identity_info = _container(_json_or_xml(identity))
                        machine_id = str(identity_info.get("machineIdentifier") or "")
                        if not machine_id:
                            configured_probe_result = "identity-missing-machine-id"
                        else:
                            root = await client.get(f"{uri}/", headers=headers)
                            if root.status_code >= 400:
                                configured_probe_result = f"root-http-{root.status_code}"
                            else:
                                root_info = _container(_json_or_xml(root))
                                name = str(root_info.get("friendlyName") or "Configured Plex Server")
                                deletion_flag = root_info.get("allowMediaDeletion")
                                deletion_allowed = (
                                    str(deletion_flag).lower() in {"1", "true", "yes"}
                                    if deletion_flag is not None
                                    else None
                                )
                                protocol = urlsplit(uri).scheme
                                servers.append(
                                    ServerOption(
                                        machine_identifier=machine_id,
                                        name=name,
                                        product_version=str(root_info.get("version") or "") or None,
                                        connections=[
                                            ServerConnectionSummary(
                                                uri=uri,
                                                local=False,
                                                relay=False,
                                                protocol=protocol,
                                            )
                                        ],
                                        allow_media_deletion=deletion_allowed,
                                    )
                                )
                                accesses[machine_id] = ServerAccess(
                                    machine_identifier=machine_id,
                                    name=name,
                                    base_url=uri,
                                    access_token=session.plex_token,
                                    local=False,
                                    relay=False,
                                )
                                configured_probe_result = "accepted"
            except httpx.HTTPError:
                pass

        if not servers:
            logger.warning(
                "Plex server discovery found no reachable server: status=%s content_type=%s "
                "payload_keys=%s container_keys=%s resource_count=%s server_resources=%s "
                "connection_candidates=%s jwt_server_resources=%s device_token_count=%s "
                "devices_lookup=%s server_probe_statuses=%s matching_identities=%s "
                "mismatched_identities=%s network_failures=%s configured_server_probe=%s",
                response.status_code,
                response.headers.get("content-type", "unknown"),
                sorted(payload.keys()),
                sorted(resources.keys()),
                len(raw_resources),
                server_resource_count,
                connection_candidate_count,
                len(jwt_server_resources),
                len(legacy_device_tokens),
                device_lookup_result,
                identity_status_counts,
                matching_identity_count,
                mismatched_identity_count,
                network_failure_count,
                configured_probe_result,
            )

        session.server_options = servers
        session.server_access = accesses
        if len(servers) == 1:
            session.selected_server_id = servers[0].machine_identifier
        elif session.selected_server_id not in accesses:
            session.selected_server_id = None
        return servers
