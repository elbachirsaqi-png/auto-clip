"""Kick : streams en live (API officielle), chat (websocket Pusher public du site) et clips.

L'API officielle (https://docs.kick.com) donne les lives, mais ni la lecture du chat (seulement
par webhook) ni les clips. Pour ces deux-là on utilise, en lecture seule, les mêmes points
d'accès publics que kick.com : ils peuvent changer sans préavis, d'où l'isolement ici.
"""

import asyncio
import json
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta

import httpx
import websockets

from ..config import Settings
from .detector import message_weight

log = logging.getLogger(__name__)

API = "https://api.kick.com/public"
SITE_API = "https://kick.com/api/v2"
PUSHER_URL = "wss://ws-us2.pusher.com/app/32cbd69e4b950bf97679?protocol=7&client=js&version=8.4.0&flash=false"
# kick.com refuse les requêtes sans en-têtes de navigateur.
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/json",
}
EMOTE = re.compile(r"\[emote:\d+:([^\]]+)\]")


def normalize_message(content: str) -> str:
    """« [emote:37226:KEKW] » -> « KEKW », pour compter les emotes comme sur Twitch."""
    return EMOTE.sub(lambda m: f" {m.group(1)} ", content)


class KickAPI:
    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        self.s = settings
        self.http = http
        self._token: str | None = None
        self._token_exp = 0.0
        self._chatrooms: dict[str, int] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.s.kick_enabled and self.s.kick_client_id and self.s.kick_client_secret)

    async def _auth(self) -> dict:
        if not self._token or time.time() > self._token_exp - 60:
            r = await self.http.post("https://id.kick.com/oauth/token", data={
                "grant_type": "client_credentials",
                "client_id": self.s.kick_client_id,
                "client_secret": self.s.kick_client_secret,
            })
            r.raise_for_status()
            data = r.json()
            self._token = data["access_token"]
            self._token_exp = time.time() + data.get("expires_in", 3600)
        return {"Authorization": f"Bearer {self._token}"}

    async def top_streams(self, n: int, language: str = "") -> list[dict]:
        """Les n plus gros lives. L'API les trie du plus ancien au plus récent : on trie nous-mêmes."""
        params: dict = {"limit": 1000}
        if language:
            params["language_code"] = language
        streams, cursor = [], None
        for _ in range(5):  # 5 000 lives au maximum
            if cursor:
                params["cursor"] = cursor
            r = await self.http.get(f"{API}/v2/livestreams", params=params, headers=await self._auth())
            r.raise_for_status()
            body = r.json()
            streams += [_from_livestream(d) for d in body.get("data", [])]
            cursor = (body.get("pagination") or {}).get("next_cursor")
            if not cursor:
                break
        return sorted(streams, key=lambda s: -s["viewer_count"])[:n]

    async def live_streams(self, slugs: list[str]) -> list[dict]:
        """Lives parmi une liste de chaînes (les chaînes hors ligne sont absentes)."""
        out = []
        for i in range(0, len(slugs), 50):  # 50 chaînes maximum par requête
            r = await self.http.get(f"{API}/v1/channels", params={"slug": slugs[i:i + 50]},
                                    headers=await self._auth())
            r.raise_for_status()
            for ch in r.json().get("data", []):
                stream = ch.get("stream") or {}
                if stream.get("is_live"):
                    out.append({
                        "slug": ch["slug"].lower(),
                        "game_name": (ch.get("category") or {}).get("name") or "",
                        "viewer_count": stream.get("viewer_count", 0),
                    })
        return out

    async def chatroom_id(self, slug: str) -> int:
        if slug not in self._chatrooms:
            r = await self.http.get(f"{SITE_API}/channels/{slug}", headers=BROWSER_HEADERS)
            r.raise_for_status()
            self._chatrooms[slug] = r.json()["chatroom"]["id"]
        return self._chatrooms[slug]

    async def find_clip_for_moment(self, slug: str, detected_at: datetime) -> str | None:
        """Le clip le plus vu créé par les viewers juste après le moment détecté."""
        r = await self.http.get(f"{SITE_API}/channels/{slug}/clips",
                                params={"sort": "date", "time": "day"}, headers=BROWSER_HEADERS)
        r.raise_for_status()
        since = detected_at - timedelta(seconds=60)
        clips = [c for c in r.json().get("clips", [])
                 if datetime.fromisoformat(c["created_at"]) >= since]
        if not clips:
            return None
        best = max(clips, key=lambda c: c.get("views") or c.get("view_count") or 0)
        return best.get("video_url") or best.get("clip_url")


def _from_livestream(d: dict) -> dict:
    return {
        "slug": d["channel"]["slug"].lower(),
        "game_name": (d.get("category") or {}).get("name") or "",
        "viewer_count": d.get("viewer_count", 0),
    }


class KickChat:
    """Une connexion Pusher, plusieurs chats. Accumule un score d'activité par chaîne."""

    def __init__(self, api: KickAPI):
        self.api = api
        self.activity: dict[str, float] = defaultdict(float)
        self._wanted: set[str] = set()
        self._joined: dict[str, int] = {}  # slug -> chatroom id
        self._rooms: dict[int, str] = {}  # chatroom id -> slug
        self._ws = None

    def set_channels(self, slugs: set[str]) -> None:
        self._wanted = {s.lower() for s in slugs}

    def drain(self) -> dict[str, float]:
        out, self.activity = dict(self.activity), defaultdict(float)
        return out

    async def _sync(self) -> None:
        for slug in self._wanted - set(self._joined):
            try:
                room = await self.api.chatroom_id(slug)
            except Exception as e:  # noqa: BLE001  (chaîne introuvable, site indisponible…)
                log.warning("Chat Kick de %s introuvable : %s", slug, type(e).__name__)
                continue
            await self._ws.send(json.dumps({"event": "pusher:subscribe",
                                            "data": {"auth": "", "channel": f"chatrooms.{room}.v2"}}))
            self._joined[slug], self._rooms[room] = room, slug
        for slug in set(self._joined) - self._wanted:
            room = self._joined.pop(slug)
            self._rooms.pop(room, None)
            await self._ws.send(json.dumps({"event": "pusher:unsubscribe",
                                            "data": {"channel": f"chatrooms.{room}.v2"}}))

    async def run(self) -> None:
        while True:
            try:
                async with websockets.connect(PUSHER_URL) as ws:
                    self._ws, self._joined, self._rooms = ws, {}, {}
                    sync = asyncio.create_task(self._sync_loop())
                    try:
                        async for raw in ws:
                            await self._handle(json.loads(raw))
                    finally:
                        sync.cancel()
            except Exception:
                log.exception("Chat Kick déconnecté, reconnexion dans 5 s")
                await asyncio.sleep(5)

    async def _sync_loop(self) -> None:
        while True:
            await self._sync()
            await asyncio.sleep(10)

    async def _handle(self, msg: dict) -> None:
        event = msg.get("event", "")
        if event == "pusher:ping":
            await self._ws.send(json.dumps({"event": "pusher:pong", "data": {}}))
        elif event == "App\\Events\\ChatMessageEvent":
            data = json.loads(msg.get("data") or "{}")
            slug = self._rooms.get(data.get("chatroom_id"))
            if slug:
                self.activity[slug] += message_weight(normalize_message(data.get("content", "")))
