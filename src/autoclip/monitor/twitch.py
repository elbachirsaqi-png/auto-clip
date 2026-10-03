"""Twitch : top streams (Helix), chat en lecture anonyme (IRC/WebSocket), clips."""

import asyncio
import logging
import random
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import httpx
import websockets

from ..config import Settings
from .detector import message_weight

log = logging.getLogger(__name__)

HELIX = "https://api.twitch.tv/helix"
IRC_URL = "wss://irc-ws.chat.twitch.tv:443"


class TwitchAPI:
    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        self.s = settings
        self.http = http
        self._app_token: str | None = None
        self._app_token_exp = 0.0

    async def _token(self) -> str:
        if self._app_token and time.time() < self._app_token_exp - 60:
            return self._app_token
        # Identifiants dans le corps de la requête, pas dans l'URL (qui finit dans les logs).
        r = await self.http.post(
            "https://id.twitch.tv/oauth2/token",
            data={
                "client_id": self.s.twitch_client_id,
                "client_secret": self.s.twitch_client_secret,
                "grant_type": "client_credentials",
            },
        )
        r.raise_for_status()
        data = r.json()
        self._app_token = data["access_token"]
        self._app_token_exp = time.time() + data["expires_in"]
        return self._app_token

    async def _get(self, path: str, **params) -> dict:
        headers = {"Client-Id": self.s.twitch_client_id, "Authorization": f"Bearer {await self._token()}"}
        r = await self.http.get(f"{HELIX}{path}", params=params, headers=headers)
        r.raise_for_status()
        return r.json()

    async def top_streams(self, n: int, language: str = "") -> list[dict]:
        """Retourne [{user_id, user_login, viewer_count, ...}] des n plus gros streams en direct."""
        params = {"first": n}
        if language:
            params["language"] = language
        return (await self._get("/streams", **params))["data"]

    async def user_id(self, login: str) -> str:
        """Identifiant Twitch d'une chaîne, même si elle n'est plus dans le top."""
        data = (await self._get("/users", login=login))["data"]
        if not data:
            raise RuntimeError(f"Chaîne Twitch introuvable : {login}")
        return data[0]["id"]

    async def recent_clips(self, broadcaster_id: str, since: datetime) -> list[dict]:
        data = await self._get(
            "/clips",
            broadcaster_id=broadcaster_id,
            started_at=since.isoformat().replace("+00:00", "Z"),
            first=20,
        )
        return data["data"]

    async def create_clip(self, broadcaster_id: str) -> str:
        """Crée un clip (token utilisateur avec scope clips:edit). Retourne l'URL du clip.

        Le clip met quelques secondes à être disponible après la création.
        """
        headers = {"Client-Id": self.s.twitch_client_id, "Authorization": f"Bearer {self.s.twitch_user_token}"}
        r = await self.http.post(f"{HELIX}/clips", params={"broadcaster_id": broadcaster_id}, headers=headers)
        r.raise_for_status()
        return f"https://clips.twitch.tv/{r.json()['data'][0]['id']}"

    async def find_clip_for_moment(self, broadcaster_id: str, detected_at: datetime) -> str | None:
        """Cherche le clip le plus vu créé par les viewers juste après le moment détecté."""
        clips = await self.recent_clips(broadcaster_id, detected_at - timedelta(seconds=60))
        if not clips:
            return None
        return max(clips, key=lambda c: c["view_count"])["url"]


class TwitchChat:
    """Une seule connexion IRC anonyme, plusieurs chaînes. Accumule un score d'activité par chaîne."""

    def __init__(self):
        self.activity: dict[str, float] = defaultdict(float)
        self._wanted: set[str] = set()
        self._joined: set[str] = set()
        self._ws = None

    def set_channels(self, logins: set[str]) -> None:
        self._wanted = {c.lower() for c in logins}

    def drain(self) -> dict[str, float]:
        """Retourne l'activité accumulée depuis le dernier appel et remet les compteurs à zéro."""
        out, self.activity = dict(self.activity), defaultdict(float)
        return out

    async def _sync_joins(self) -> None:
        for c in self._wanted - self._joined:
            await self._ws.send(f"JOIN #{c}")
        for c in self._joined - self._wanted:
            await self._ws.send(f"PART #{c}")
        self._joined = set(self._wanted)

    async def run(self) -> None:
        while True:
            try:
                async with websockets.connect(IRC_URL) as ws:
                    self._ws, self._joined = ws, set()
                    await ws.send("PASS SCHMOOPIIE")
                    await ws.send(f"NICK justinfan{random.randint(10000, 99999)}")
                    sync = asyncio.create_task(self._join_loop())
                    try:
                        async for raw in ws:
                            for line in raw.split("\r\n"):
                                self._handle(line)
                                if line.startswith("PING"):
                                    await ws.send("PONG :tmi.twitch.tv")
                    finally:
                        sync.cancel()
            except Exception:
                log.exception("Chat Twitch déconnecté, reconnexion dans 5 s")
                await asyncio.sleep(5)

    async def _join_loop(self) -> None:
        while True:
            await self._sync_joins()
            await asyncio.sleep(5)

    def _handle(self, line: str) -> None:
        # :user!user@user.tmi.twitch.tv PRIVMSG #channel :message
        parts = line.split(" ", 3)
        if len(parts) == 4 and parts[1] == "PRIVMSG":
            channel = parts[2].lstrip("#")
            self.activity[channel] += message_weight(parts[3].lstrip(":"))


def utcnow() -> datetime:
    return datetime.now(UTC)
