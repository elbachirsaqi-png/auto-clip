from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    twitch_client_id: str = ""
    twitch_client_secret: str = ""
    twitch_user_token: str = ""

    kick_client_id: str = ""
    kick_client_secret: str = ""

    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-5-5"
    # Modèle de l'étape de décision (garder / couper). Vide = même modèle que claude_model.
    decide_model: str = ""
    # Requis si la clé API n'est rattachée à aucun workspace (Console > Settings > Workspaces).
    anthropic_workspace_id: str = ""
    # Effort de réflexion (low, medium, high, xhigh, max) : plus haut = meilleur mais plus cher.
    decide_effort: str = "medium"
    compose_effort: str = "medium"
    # Clips avec moins de mots transcrits : rejetés sans appeler Claude (souvent des temps morts).
    min_transcript_words: int = 6
    # Corrections demandées à Claude si sa composition ne passe pas le lint.
    max_lint_fixes: int = 2

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    top_n_streams: int = 10
    # Langue des streams surveillés (code ISO 639-1, ex. en, fr). Vide = toutes les langues.
    stream_language: str = "en"
    # Langue du public visé : accroche, titre, hashtags et textes à l'écran.
    clip_language: str = "en"
    poll_interval_s: int = 45
    auto_create_clips: bool = False
    # Clips créés par les viewers : recherche toutes les clip_poll_s, jusqu'à clip_max_wait_s.
    clip_poll_s: int = 30
    clip_max_wait_s: int = 180
    max_clip_searches: int = 10

    # Un pic = activité du chat >= SPIKE_RATIO x la moyenne glissante du streamer.
    spike_ratio: float = 3.0
    # Pas deux moments pour le même streamer dans cette fenêtre.
    dedup_window_s: int = 150
    # Fenêtres de 10 s à observer avant de pouvoir déclencher (30 = 5 minutes).
    detector_warmup: int = 30
    # Activité minimale d'une fenêtre pour compter comme un pic (ignore les chats quasi vides).
    detector_min_activity: float = 20.0
    # Vitesse d'adaptation de la moyenne (petit = mémoire longue).
    detector_alpha: float = 0.05

    # Modèle faster-whisper : tiny, base, small, medium, large-v3 (plus gros = plus précis, plus lent).
    whisper_model: str = "small"
    # Qualité HyperFrames : draft, looks, delivery.
    render_quality: str = "looks"
    max_parallel_renders: int = 1

    db_path: Path = Path("data/autoclip.db")
    work_dir: Path = Path("data/work")


settings = Settings()
