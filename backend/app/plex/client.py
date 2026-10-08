"""Authenticated HTTP access to the selected Plex Media Server."""

from __future__ import annotations

import secrets
from typing import Any

import httpx
from fastapi import HTTPException

from app.plex.auth import PlexAuthService
from app.sessions import AppSession


class PlexClient:
    def __init__(self, auth: PlexAuthService | None = None, *, timeout_seconds: float = 30.0) -> None:
        self.auth = auth
        self.timeout_seconds = timeout_seconds

    async def request(
        self,
        method: str,
        path: str,
        *,
        session: AppSession,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        if self.auth is not None:
            await self.auth.refresh_if_needed(session)
        if session.selected_server_id is None:
            raise HTTPException(status_code=409, detail="Choose a Plex server first.")
        access = session.server_access.get(session.selected_server_id)
        if access is None:
            raise HTTPException(status_code=401, detail="Plex server access expired. Sign in again.")
        try:
            async with httpx.AsyncClient(
                timeout=timeout or self.timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = await client.request(
                    method,
                    f"{access.base_url.rstrip('/')}/{path.lstrip('/')}",
                    params=params,
                    json=json,
                    headers={
                        "Accept": "application/json",
                        "X-Plex-Token": access.access_token,
                        "X-Plex-Client-Identifier": session.client_identifier or "",
                        "X-Plex-Product": "Plex-Clean",
                        "X-Plex-Version": "0.1.0",
                    },
                )
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="Plex server request timed out.") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="Could not reach the selected Plex server.") from exc

        if response.status_code == 401:
            session.clear_sensitive()
            session.csrf_token = secrets.token_urlsafe(32)
            raise HTTPException(status_code=401, detail="Plex rejected this server token. Sign in again.")
        return response
