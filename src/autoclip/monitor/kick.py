"""Kick : à implémenter après Twitch.

L'API officielle (https://docs.kick.com) couvre les livestreams ; le chat passe par le
websocket Pusher utilisé par le site. Cette partie est plus fragile que Twitch : isoler
toute la logique ici pour pouvoir la désactiver sans toucher au reste.
"""

from ..config import Settings


class KickAPI:
    def __init__(self, settings: Settings):
        self.s = settings

    async def top_streams(self, n: int) -> list[dict]:
        raise NotImplementedError("Kick : top streams")

    async def find_clip_for_moment(self, channel: str, detected_at) -> str | None:
        raise NotImplementedError("Kick : récupération de clip")


class KickChat:
    def set_channels(self, slugs: set[str]) -> None:
        raise NotImplementedError

    def drain(self) -> dict[str, float]:
        raise NotImplementedError

    async def run(self) -> None:
        raise NotImplementedError("Kick : websocket chat")
