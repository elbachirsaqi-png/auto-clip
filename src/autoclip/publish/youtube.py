"""YouTube Shorts via YouTube Data API v3 (extra `publish`).

Quota par défaut : 10 000 unités/jour, un upload en coûte ~1 600, soit environ 6 Shorts/jour.
Tant que le projet Google n'a pas passé l'audit, les vidéos restent privées.
"""

from pathlib import Path

from ..models import EditDecision

DAILY_UPLOAD_LIMIT = 6


async def upload_short(video: Path, decision: EditDecision) -> str:
    """Upload la vidéo et retourne l'ID YouTube. Ajouter #Shorts au titre ou à la description."""
    raise NotImplementedError("YouTube : videos.insert avec OAuth utilisateur")
