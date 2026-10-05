"""Kit de publication manuelle : la vidéo + les textes à copier dans YouTube Studio et TikTok.

Rien n'est publié automatiquement : tu postes toi-même. Le titre est vérifié avec le linter
de /yt-package (youtube-agent-skill de Jake Schincariol, licence MIT), repris ci-dessous.
"""

import re
import shutil
from datetime import datetime
from pathlib import Path

from ..models import EditDecision, Moment

PUBLISH_DIR = Path("data/a_publier")
PUBLISHED_DIR = Path("data/publie")  # kits publiés automatiquement


def find_kit(moment_id: int) -> Path | None:
    found = sorted(PUBLISH_DIR.glob(f"*_{moment_id:06d}_*.mp4"))
    return found[0] if found else None


def archive_kit(video: Path) -> None:
    """Après publication automatique : déplace la vidéo et son texte dans data/publie/."""
    PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
    for f in (video, video.with_suffix(".txt")):
        if f.exists():
            shutil.move(str(f), PUBLISHED_DIR / f.name)
CHANNEL_URLS = {"twitch": "https://twitch.tv/{}", "kick": "https://kick.com/{}"}
# Ligne de contact pour les demandes de retrait, dans la langue du public.
CONTACT_LINES = {
    "en": "📩 Featured in this clip and want it removed? Contact: {}",
    "fr": "📩 Vous apparaissez dans ce clip et souhaitez son retrait ? Contact : {}",
}

# --- Linter de titre (title.py de youtube-agent-skill, MIT) ------------------------------

DESKTOP, MOBILE, HARD = 60, 40, 100
VAGUE = {"amazing", "incredible", "insane", "crazy", "huge", "massive", "ultimate", "best",
         "powerful", "secret", "revolutionary", "mindblowing", "epic", "perfect", "complete",
         "everything"}
STOP = {"the", "a", "an", "of", "for", "to", "in", "on", "and", "or", "is", "are", "with", "your",
        "you", "my", "i", "this", "that", "it", "how", "what", "why"}


def _words(t: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", t.lower())


def lint_title(title: str, thumb: str | None = None) -> dict:
    """Vérifie le titre (et le texte d'accroche, qui joue le rôle de la miniature)."""
    t = title.strip()
    n = len(t)
    issues, good = [], []
    if n > HARD:
        issues.append(f"{n} caractères : la limite de YouTube est {HARD}")
    elif n > DESKTOP:
        issues.append(f"{n} caractères : la recherche sur ordinateur coupe vers {DESKTOP}")
    else:
        good.append(f"{n} caractères, sous la coupure de {DESKTOP}")
    if n > MOBILE:
        head = t[:MOBILE].rsplit(" ", 1)[0]
        issues.append(f'sur mobile on voit environ « {head}… » : vérifie que le sujet y est')
    caps = [w for w in t.split() if len(w) > 2 and w.isupper()]
    if len(caps) > 2:
        issues.append(f"{len(caps)} mots en majuscules : au-delà de deux, ça fait spam")
    vague = sorted({w for w in _words(t) if w in VAGUE})
    if vague:
        issues.append(f"mots vagues ({', '.join(vague)}) : remplace par un nom, un chiffre ou une date")
    if not [w for w in _words(t)[:3] if w not in STOP]:
        issues.append("les trois premiers mots sont du remplissage : avance le sujet")
    if thumb:
        shared = (set(_words(t)) - STOP) & (set(_words(thumb)) - STOP)
        if shared:
            issues.append(f"l'accroche répète le titre ({', '.join(sorted(shared))}) : "
                          "elle devrait dire ce que le titre ne dit pas")
        else:
            good.append("accroche et titre disent des choses différentes")
    score = max(0, min(100, 100 - 14 * len(issues) + 4 * len(good)))
    return {"score": score, "issues": issues, "good": good}


# --- Kit ---------------------------------------------------------------------------------


def _slug(text: str, limit: int = 50) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text).strip(" .")
    return re.sub(r"\s+", " ", text)[:limit].strip() or "clip"


def hashtags(decision: EditDecision) -> list[str]:
    tags = [h if h.startswith("#") else f"#{h}" for h in decision.hashtags]
    if not any(t.lower() == "#shorts" for t in tags):
        tags.append("#Shorts")
    return tags


def description(decision: EditDecision, m: Moment, contact_email: str = "", language: str = "en") -> str:
    """Description complète : 2 lignes, crédit du streamer, contact pour retrait, hashtags."""
    body = decision.description.strip() or decision.hook
    credit = f"🎥 {m.channel} : {CHANNEL_URLS.get(m.platform, '{}').format(m.channel)}"
    parts = [body, credit]
    if contact_email:
        parts.append(CONTACT_LINES.get(language, CONTACT_LINES["en"]).format(contact_email))
    parts.append(" ".join(hashtags(decision)))
    return "\n\n".join(parts)


def tiktok_caption(decision: EditDecision, m: Moment) -> str:
    """Légende TikTok : pas de titre séparé, seule la première ligne s'affiche avant « plus ».

    On ouvre donc sur le titre, puis le crédit, puis 5 hashtags au plus (sans #Shorts, propre à
    YouTube) : au-delà, TikTok n'en tient pas compte et ça fait spam.
    """
    tags = [t for t in hashtags(decision) if t.lower() != "#shorts"][:5]
    if not any(t.lower().lstrip("#") == m.channel.lower() for t in tags):
        tags = [f"#{m.channel}", *tags][:5]
    credit = f"🎥 {m.channel} ({m.platform.capitalize()})"
    return f"{decision.title}\n\n{credit}\n\n{' '.join(tags)}"[:2200]


def build_kit(m: Moment, decision: EditDecision, video: Path, contact_email: str = "",
              language: str = "en") -> tuple[Path, Path]:
    """Déplace la vidéo dans data/a_publier/ et écrit le fichier texte à côté."""
    PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
    base = f"{datetime.now().astimezone():%Y-%m-%d}_{m.id:06d}_{m.channel}_{_slug(decision.title)}"
    target = PUBLISH_DIR / f"{base}.mp4"
    shutil.move(str(video), target)

    lint = lint_title(decision.title, decision.hook)
    report = "\n".join([f"  ✗ {i}" for i in lint["issues"]] + [f"  ✓ {g}" for g in lint["good"]])
    text = (
        f"===== YOUTUBE =====\nTITRE\n{decision.title}\n\n"
        f"DESCRIPTION\n{description(decision, m, contact_email, language)}\n\n"
        f"===== TIKTOK =====\nLÉGENDE\n{tiktok_caption(decision, m)}\n\n"
        f"----\nVérification du titre : {lint['score']}/100\n{report}\n"
        f"Streamer : {m.channel} ({m.platform}) · catégorie : {m.category or '?'}\n"
    )
    txt = target.with_suffix(".txt")
    txt.write_text(text, encoding="utf-8")
    return target, txt
