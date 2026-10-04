from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Platform(StrEnum):
    TWITCH = "twitch"
    KICK = "kick"


class Status(StrEnum):
    """Cycle de vie d'un moment. Chaque worker fait passer un statut au suivant."""

    DETECTED = "detected"
    CLIPPED = "clipped"  # on a une URL de clip
    DOWNLOADED = "downloaded"
    SOURCE_PENDING = "source_pending"  # clip brut envoyé sur Telegram, en attente de ton choix
    SOURCE_APPROVED = "source_approved"  # tu as validé le montage
    ANALYZED = "analyzed"  # transcription + frames prêtes
    DECIDED = "decided"  # JSON de montage reçu de Claude
    RENDERED = "rendered"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"
    FAILED = "failed"


class Moment(BaseModel):
    id: int | None = None
    platform: Platform
    channel: str
    detected_at: datetime
    score: float
    category: str | None = None  # jeu ou catégorie Twitch (Just Chatting, IRL…)
    status: Status = Status.DETECTED
    clip_url: str | None = None
    video_path: str | None = None
    transcript_path: str | None = None
    frames_dir: str | None = None
    decision_json: str | None = None
    render_path: str | None = None
    error: str | None = None


# --- Sortie attendue de Claude -------------------------------------------------


class Cut(BaseModel):
    start_s: float = Field(ge=0, description="Début du segment conservé, en secondes")
    end_s: float = Field(gt=0, description="Fin du segment conservé, en secondes")


class FaceCam(BaseModel):
    """Zone de la facecam dans la vidéo source, en fractions (0 à 1) de la largeur/hauteur."""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)


class EditDecision(BaseModel):
    keep: bool = Field(description="False si le clip ne vaut pas la peine d'être publié")
    reason: str
    cuts: list[Cut]
    hook: str = Field(description="Texte d'accroche affiché pendant les 2 premières secondes")
    creative_direction: str = Field(
        description="Mise en page, style et animations imaginés pour ce clip, en quelques phrases"
    )
    facecam: FaceCam | None = None
    highlight_words: list[str] = Field(description="Mots des sous-titres à mettre en valeur")
    title: str
    description: str = Field(
        default="", description="Description YouTube : les 2 premières lignes sont les seules lues"
    )
    hashtags: list[str]

    def validate_against(self, duration_s: float, min_len: float = 8, max_len: float = 60) -> None:
        """Vérifie que les coupes sont cohérentes avec la durée réelle du clip."""
        total = 0.0
        for c in self.cuts:
            if c.end_s <= c.start_s or c.end_s > duration_s + 0.5:
                raise ValueError(f"Coupe invalide {c} pour un clip de {duration_s:.1f}s")
            total += c.end_s - c.start_s
        if self.keep and not (min_len <= total <= max_len):
            raise ValueError(f"Durée montée {total:.1f}s hors de [{min_len}, {max_len}]")
