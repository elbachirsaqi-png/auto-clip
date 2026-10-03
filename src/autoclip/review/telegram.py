"""Validation manuelle : envoie chaque rendu sur Telegram avec deux boutons Publier / Rejeter.

Appels directs à l'API Bot (https://core.telegram.org/bots/api) avec httpx. Le token fait
partie de l'URL : on ne laisse jamais une URL ou une exception httpx brute remonter dans
les logs, seulement la description d'erreur renvoyée par Telegram.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from ..config import Settings
from ..models import EditDecision

log = logging.getLogger(__name__)

DecisionCallback = Callable[[int, bool], Awaitable[bool]]

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # limite d'envoi de fichiers pour un bot
POLL_TIMEOUT_S = 50  # long polling de getUpdates


class TelegramError(Exception):
    pass


class ReviewBot:
    def __init__(self, settings: Settings, on_decision: DecisionCallback):
        self.s = settings
        self.on_decision = on_decision
        self._base = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
        # Timeout plus long que le long polling, et large pour l'envoi des vidéos.
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(POLL_TIMEOUT_S + 20, write=300))

    @property
    def enabled(self) -> bool:
        return bool(self.s.telegram_bot_token and self.s.telegram_chat_id)

    async def _call(self, method: str, **kwargs) -> dict:
        try:
            r = await self._http.post(f"{self._base}/{method}", **kwargs)
        except httpx.HTTPError as e:
            # Message sans l'URL (qui contient le token).
            raise TelegramError(f"{method} : {type(e).__name__}") from None
        data = r.json()
        if not data.get("ok"):
            raise TelegramError(f"{method} : {data.get('description', r.status_code)}")
        return data["result"]

    async def send_for_review(
        self, moment_id: int, channel: str, score: float, video: Path, decision: EditDecision
    ) -> None:
        if video.stat().st_size > MAX_UPLOAD_BYTES:
            video = await _shrink_for_telegram(video)
        caption = "\n".join([
            f"#{moment_id} · {channel} · pic x{score:.1f}",
            f"🎬 {decision.title}",
            f"🪝 {decision.hook}",
            " ".join(decision.hashtags),
        ])[:1024]
        keyboard = {"inline_keyboard": [[
            {"text": "✅ Publier", "callback_data": f"approve:{moment_id}"},
            {"text": "❌ Rejeter", "callback_data": f"reject:{moment_id}"},
        ]]}
        with video.open("rb") as f:
            await self._call(
                "sendVideo",
                data={
                    "chat_id": self.s.telegram_chat_id,
                    "caption": caption,
                    "supports_streaming": "true",
                    "reply_markup": json.dumps(keyboard),
                },
                files={"video": (video.name, f, "video/mp4")},
            )

    async def run(self) -> None:
        """Écoute les clics sur les boutons (long polling) et transmet les décisions."""
        offset = None
        while True:
            try:
                updates = await self._call(
                    "getUpdates",
                    json={"offset": offset, "timeout": POLL_TIMEOUT_S,
                          "allowed_updates": ["callback_query"]},
                )
                for u in updates:
                    offset = u["update_id"] + 1
                    if "callback_query" in u:
                        await self._handle_click(u["callback_query"])
            except Exception:
                # TelegramError est levée sans chaînage : la trace ne contient pas le token.
                log.exception("Telegram indisponible, nouvel essai dans 10 s")
                await asyncio.sleep(10)

    async def _handle_click(self, query: dict) -> None:
        message = query.get("message") or {}
        # Seuls les boutons envoyés dans le chat de validation comptent.
        if str(message.get("chat", {}).get("id")) != str(self.s.telegram_chat_id):
            await self._call("answerCallbackQuery", json={"callback_query_id": query["id"]})
            return

        action, _, raw_id = query.get("data", "").partition(":")
        if action not in ("approve", "reject") or not raw_id.isdigit():
            return
        approved = action == "approve"
        applied = await self.on_decision(int(raw_id), approved)

        if applied:
            verdict = "✅ Approuvé" if approved else "❌ Rejeté (fichiers supprimés)"
        else:
            verdict = "Déjà traité"
        await self._call("answerCallbackQuery",
                         json={"callback_query_id": query["id"], "text": verdict})
        if applied:
            # Retire les boutons et note le verdict sous la vidéo.
            await self._call("editMessageCaption", json={
                "chat_id": message["chat"]["id"],
                "message_id": message["message_id"],
                "caption": f"{message.get('caption', '')}\n\n{verdict}"[:1024],
            })

    async def close(self) -> None:
        await self._http.aclose()


async def _shrink_for_telegram(video: Path) -> Path:
    """Copie allégée pour passer sous la limite de 50 Mo (la vidéo finale reste intacte)."""
    preview = video.with_name("telegram_preview.mp4")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
        "-c:v", "libx264", "-crf", "30", "-preset", "veryfast", "-c:a", "aac", "-b:a", "96k",
        str(preview),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Compression pour Telegram échouée : {err.decode(errors='replace')[-300:]}")
    return preview
