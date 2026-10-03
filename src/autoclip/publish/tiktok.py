"""TikTok via Content Posting API.

Tant que l'app n'a pas passé l'audit TikTok, les publications sont forcées en privé
(SELF_ONLY). Prévoir le flux OAuth utilisateur et l'upload par chunks (FILE_UPLOAD).
"""

from pathlib import Path

from ..models import EditDecision


async def upload_video(video: Path, decision: EditDecision) -> str:
    """Publie la vidéo et retourne l'identifiant de publication TikTok."""
    raise NotImplementedError("TikTok : Content Posting API")
