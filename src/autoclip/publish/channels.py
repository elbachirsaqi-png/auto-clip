"""Chaînes de destination : quel clip part sur quelle chaîne YouTube / TikTok.

Chaque chaîne a ses règles (streamers et/ou catégories), son profil Chrome par plateforme
et son rythme de publication. Le premier qui correspond l'emporte ; un clip sans chaîne
reste en publication manuelle (kit dans data/a_publier/).
Stocké dans data/channels.json, modifiable depuis l'application (page Chaînes).
"""

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from ..models import Moment

CHANNELS_FILE = Path("data/channels.json")
PROFILES_DIR = Path("data/profiles")
PLATFORMS = ("youtube", "tiktok")


class Destination(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_-]{2,40}$")
    name: str
    enabled: bool = True
    # Règles : le clip correspond si son streamer est dans la liste OU sa catégorie contient
    # un des mots-clés (insensible à la casse). Listes vides = règle ignorée.
    streamers: list[str] = []
    categories: list[str] = []
    youtube: bool = True
    tiktok: bool = True
    youtube_visibility: str = "public"  # public, unlisted, private
    max_per_day: int = 3  # par plateforme
    min_gap_minutes: int = 120  # entre deux publications sur la même plateforme

    def matches(self, m: Moment) -> bool:
        if not self.enabled:
            return False
        if m.channel.lower() in {s.lower() for s in self.streamers}:
            return True
        category = (m.category or "").lower()
        return bool(category) and any(c.lower() in category for c in self.categories if c.strip())

    def platforms(self) -> list[str]:
        return [p for p in PLATFORMS if getattr(self, p)]

    def profile_dir(self, platform: str) -> Path:
        return PROFILES_DIR / f"{self.id}-{platform}"

    def logged_in(self, platform: str) -> bool:
        return (self.profile_dir(platform) / ".connected").exists()


DEFAULTS = [
    Destination(id="streamers", name="Streamers connus",
                streamers=["kaicenat", "clavicular"], categories=["IRL", "Just Chatting"]),
    Destination(id="counter-strike", name="Counter-Strike", enabled=False,
                categories=["Counter-Strike"]),
    # Prête pour la sortie : activer dans l'application le moment venu.
    Destination(id="gta6", name="GTA 6", enabled=False,
                categories=["Grand Theft Auto VI", "GTA VI", "GTA 6"]),
]


def load() -> list[Destination]:
    if not CHANNELS_FILE.exists():
        return [d.model_copy() for d in DEFAULTS]
    data = json.loads(CHANNELS_FILE.read_text(encoding="utf-8"))
    return [Destination.model_validate(d) for d in data]


def save(destinations: list[Destination]) -> None:
    CHANNELS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHANNELS_FILE.write_text(
        json.dumps([d.model_dump() for d in destinations], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def route(m: Moment, destinations: list[Destination] | None = None) -> Destination | None:
    """La chaîne qui publiera ce clip, ou None (publication manuelle)."""
    for d in destinations if destinations is not None else load():
        if d.matches(m):
            return d
    return None


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "chaine"
