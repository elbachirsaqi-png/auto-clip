"""Orchestrateur : un moniteur qui crée des moments, puis un worker par étape.

Chaque worker prend un moment dans un statut donné, fait son travail et le fait passer
au statut suivant. Un échec relâche le moment pour un nouvel essai (3 maximum).
"""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .analysis.frames import extract_frames, probe_duration, probe_size
from .analysis.transcribe import transcribe
from .config import Settings
from .db import Database
from .editor.claude import ClipComposer, ClipEditor
from .editor.subscription import ClaudeUnavailable, UsageLimitReached
from .fetch.downloader import download_clip
from .gui.envfile import read_env
from .models import EditDecision, Moment, Platform, Status
from .monitor.detector import SpikeDetector
from .monitor.kick import KickAPI, KickChat
from .monitor.twitch import TwitchAPI, TwitchChat
from .procutil import PID_FILE
from .publish import channels
from .publish.browser import NotLoggedIn, publish
from .publish.kit import archive_kit, build_kit, description, find_kit, tiktok_caption
from .render.hyperframes import LintError, Renderer, build_segments, remap_words
from .review.telegram import COMMANDS, ReviewBot

log = logging.getLogger(__name__)

WINDOW_S = 10  # taille d'une fenêtre d'activité du chat
# Présent = pipeline en pause (/pause sur Telegram) ; survit à un redémarrage.
PAUSE_FILE = Path("data/paused")
PLATFORM_NAMES = {"youtube": "YouTube", "tiktok": "TikTok"}
IDLE_SLEEP_S = 3

Step = Callable[[Moment], Awaitable[None]]


async def supervised(name: str, factory: Callable[[], Awaitable[None]]) -> None:
    """Relance une tâche de fond qui plante, au lieu de laisser tomber tout le pipeline."""
    while True:
        try:
            await factory()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Tâche « %s » plantée, relance dans 10 s", name)
        await asyncio.sleep(10)


def folder_name(category: str | None) -> str:
    """Nom de dossier Windows valide pour une catégorie Twitch (« Just Chatting », jeux…)."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", category or "").strip(" .")
    return name[:80] or "Autre"


class Pipeline:
    def __init__(self, settings: Settings):
        self.s = settings
        self.db = Database(settings.db_path)
        self.http = httpx.AsyncClient(timeout=20)
        self.twitch = TwitchAPI(settings, self.http)
        self.twitch_chat = TwitchChat()
        self.kick = KickAPI(settings, self.http)
        self.kick_chat = KickChat(self.kick)
        self.detector = SpikeDetector(
            ratio=settings.spike_ratio,
            cooldown_s=settings.dedup_window_s,
            warmup=settings.detector_warmup,
            min_activity=settings.detector_min_activity,
            alpha=settings.detector_alpha,
        )
        self.editor = ClipEditor(settings)
        self.composer = ClipComposer(settings)
        self.renderer = Renderer(settings.max_parallel_renders, settings.render_quality)
        self.review = ReviewBot(settings, self.on_review, self.on_command)
        self.started_at = time.time()
        # Publication automatique : échecs par (moment, cible) et alertes déjà envoyées.
        self.publish_failures: dict[tuple[int, str], tuple[int, float]] = {}
        self.publish_alerts: set[str] = set()
        # login -> broadcaster_id / catégorie, remplis à chaque rafraîchissement.
        self.twitch_ids: dict[str, str] = {}
        self.twitch_games: dict[str, str] = {}
        self.kick_games: dict[str, str] = {}
        # Abonnement Claude à sa limite : les étapes qui appellent Claude attendent cette heure.
        self.claude_paused_until = 0.0

    def moment_dirs(self, moment_id: int) -> list[Path]:
        """Dossiers d'un moment : data/work/<catégorie>/<id>_… (ou data/work/<id>_… avant)."""
        pattern = f"{moment_id:06d}_*"
        return [d for d in [*self.s.work_dir.glob(pattern), *self.s.work_dir.glob(f"*/{pattern}")]
                if d.is_dir()]

    def work_dir(self, m: Moment) -> Path:
        existing = self.moment_dirs(m.id)
        if existing:
            return existing[0]
        return self.s.work_dir / folder_name(m.category) / f"{m.id:06d}_{m.platform}_{m.channel}"

    # --- Nettoyage du disque ----------------------------------------------------

    def discard_files(self, moment_id: int) -> None:
        """Supprime tout le dossier de travail d'un moment qui ne sera pas publié."""
        for d in self.moment_dirs(moment_id):
            parent = d.parent
            shutil.rmtree(d, ignore_errors=True)
            log.info("Fichiers du moment #%d supprimés", moment_id)
            if parent != self.s.work_dir and not any(parent.iterdir()):
                parent.rmdir()  # dossier de catégorie devenu vide

    def slim_files(self, moment_id: int) -> None:
        """Après validation : garde final.mp4 (et la composition), supprime les intermédiaires."""
        for d in self.moment_dirs(moment_id):
            for name in ("source.mp4", "render/source.mp4", "telegram_preview.mp4"):
                (d / name).unlink(missing_ok=True)
            shutil.rmtree(d / "frames", ignore_errors=True)

    async def cleanup_work_dirs(self) -> None:
        """Au démarrage : supprime les dossiers des moments rejetés, abandonnés ou inconnus."""
        if not self.s.work_dir.exists():
            return
        statuses = await self.db.statuses()
        dead = {Status.REJECTED.value, Status.FAILED.value}
        candidates = [*self.s.work_dir.iterdir(), *self.s.work_dir.glob("*/*")]
        for d in candidates:
            if not (d.is_dir() and d.name[:6].isdigit() and d.exists()):
                continue
            moment_id = int(d.name[:6])
            status = statuses.get(moment_id)
            if status is None or status in dead:
                shutil.rmtree(d, ignore_errors=True)
                log.info("Nettoyage : dossier du moment #%d supprimé (%s)", moment_id, status)
                continue
            if status == Status.APPROVED.value:
                self.slim_files(moment_id)
            # La copie de la source dans render/ ne sert plus une fois le rendu fait.
            if (d / "final.mp4").exists():
                (d / "render" / "source.mp4").unlink(missing_ok=True)
        for d in self.s.work_dir.iterdir():  # dossiers de catégorie vides
            if d.is_dir() and not d.name[:6].isdigit() and not any(d.iterdir()):
                d.rmdir()

    # --- Surveillance ---------------------------------------------------------

    @property
    def paused(self) -> bool:
        return PAUSE_FILE.exists()

    async def on_command(self, command: str) -> str:
        """Commandes Telegram : /statut, /pause, /reprendre, /redemarrer, /aide."""
        if command == "pause":
            if self.paused:
                return "⏸ Déjà en pause. /reprendre pour relancer."
            PAUSE_FILE.write_text(datetime.now(UTC).isoformat())
            self.twitch_chat.set_channels(set())
            self.kick_chat.set_channels(set())
            log.info("Pipeline mis en pause depuis Telegram")
            return ("⏸ Pause : plus de surveillance ni de nouveau montage.\n"
                    "Un rendu déjà commencé se termine. Les boutons des vidéos reçues marchent "
                    "toujours.\n/reprendre pour relancer.")
        if command == "reprendre":
            if not self.paused:
                return "▶ Le pipeline tourne déjà."
            PAUSE_FILE.unlink(missing_ok=True)
            log.info("Pipeline relancé depuis Telegram")
            return "▶ C'est reparti : surveillance et montages relancés (chats rejoints sous 1 min)."
        if command == "redemarrer":
            asyncio.get_running_loop().call_later(1, lambda: asyncio.ensure_future(self.restart()))
            return "🔄 Redémarrage du pipeline… (environ 30 s)"
        if command == "statut":
            return await self.status_text()
        return "Commandes :\n" + "\n".join(f"/{c} — {d}" for c, d in COMMANDS.items())

    async def status_text(self) -> str:
        counts = await self.db.status_counts()
        uptime = int(time.time() - self.started_at) // 60
        state = "⏸ En pause" if self.paused else "▶ En marche"
        if time.time() < self.claude_paused_until:
            reset = datetime.fromtimestamp(self.claude_paused_until, UTC).astimezone()
            state += f" · montages en attente de Claude jusqu'à {reset:%H:%M}"
        twitch = len(self.twitch_ids)
        kick = len(self.kick_games) if self.kick.enabled else 0
        waiting = counts.get("source_pending", 0) + counts.get("pending_review", 0)
        in_progress = sum(counts.get(s, 0) for s in
                          ("source_approved", "analyzed", "decided", "rendered"))
        return (f"{state} depuis {uptime // 60} h {uptime % 60:02d}\n"
                f"Surveillés : {twitch} Twitch · {kick} Kick\n"
                f"À valider sur Telegram : {waiting}\n"
                f"En cours de montage : {in_progress}\n"
                f"Vidéos approuvées : {counts.get('approved', 0)}")

    async def restart(self) -> None:
        """Relance un nouveau pipeline puis arrête celui-ci (et ses rendus en cours)."""
        log.info("Redémarrage demandé depuis Telegram")
        try:
            await self.review.acknowledge()
        except Exception:  # noqa: BLE001  (au pire, la commande sera relue une fois)
            log.warning("Impossible de confirmer les messages Telegram avant le redémarrage")
        PID_FILE.unlink(missing_ok=True)  # sinon le nouveau croirait le pipeline déjà lancé
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([sys.executable, "-m", "autoclip"], cwd=os.getcwd(),  # noqa: ASYNC220  (on quitte juste après)
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        if sys.platform == "win32":
            # /T : arrête aussi les rendus (Node, Chrome) lancés par ce processus.
            subprocess.run(["taskkill", "/PID", str(os.getpid()), "/T", "/F"],  # noqa: ASYNC221
                           capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                           check=False)
        os._exit(0)

    def watched_channels(self, key: str = "WATCH_CHANNELS") -> list[str]:
        """Liste relue dans le .env à chaque fois : modifiable depuis l'application sans redémarrer."""
        default = self.s.watch_channels if key == "WATCH_CHANNELS" else self.s.kick_watch_channels
        raw = read_env(Path(".env")).get(key, default)
        return sorted({c.strip().lower() for c in raw.split(",") if c.strip()})

    async def refresh_kick_streams(self) -> None:
        while True:
            if self.paused:
                self.kick_chat.set_channels(set())
                await asyncio.sleep(5)
                continue
            try:
                streams = []
                if self.s.kick_include_top:
                    streams += await self.kick.top_streams(self.s.kick_top_n, self.s.stream_language)
                watched = self.watched_channels("KICK_WATCH_CHANNELS")
                streams += await self.kick.live_streams(watched) if watched else []
                self.kick_games = {s["slug"]: s["game_name"] for s in streams}
                self.kick_chat.set_channels(set(self.kick_games))
                log.info("Kick surveillé : %s", ", ".join(self.kick_games) or "aucun stream en live")
            except Exception:
                log.exception("Impossible de récupérer les streams Kick")
            await asyncio.sleep(self.s.poll_interval_s)

    async def refresh_top_streams(self) -> None:
        while True:
            if self.paused:
                self.twitch_chat.set_channels(set())
                await asyncio.sleep(5)
                continue
            try:
                streams = []
                if self.s.include_top_streams:
                    streams += await self.twitch.top_streams(
                        self.s.top_n_streams, self.s.stream_language)
                watched = self.watched_channels()
                live_watched = await self.twitch.live_streams(watched) if watched else []
                streams += live_watched
                self.twitch_ids = {s["user_login"]: s["user_id"] for s in streams}
                self.twitch_games = {s["user_login"]: s.get("game_name") or "" for s in streams}
                self.twitch_chat.set_channels(set(self.twitch_ids))
                log.info("Twitch surveillé : %s", ", ".join(self.twitch_ids) or "aucun stream en live")
                if watched:
                    live = {s["user_login"] for s in live_watched}
                    log.info("Liste perso : %d/%d en live", len(live), len(watched))
            except Exception:
                log.exception("Impossible de récupérer les streams Twitch")
            await asyncio.sleep(self.s.poll_interval_s)

    async def detect_loop(self) -> None:
        while True:
            await asyncio.sleep(WINDOW_S)
            now = time.time()
            sources = [(Platform.TWITCH, self.twitch_chat.drain(), self.twitch_games)]
            if self.kick.enabled:
                sources.append((Platform.KICK, self.kick_chat.drain(), self.kick_games))
            if self.paused:
                continue
            for platform, activities, games in sources:
                for channel, activity in activities.items():
                    # Moyenne propre à chaque plateforme : un même pseudo peut exister des deux côtés.
                    key = channel if platform == Platform.TWITCH else f"kick:{channel}"
                    score = self.detector.update(key, activity, now)
                    if score is None:
                        continue
                    m = Moment(
                        platform=platform,
                        channel=channel,
                        category=games.get(channel) or None,
                        detected_at=datetime.now(UTC),
                        score=score,
                    )
                    m.id = await self.db.insert_moment(m)
                    log.info("Moment fort #%d sur %s %s · %s (x%.1f)", m.id, platform, channel,
                             m.category or "?", score)

    # --- Étapes ---------------------------------------------------------------

    async def step_get_clip(self, m: Moment) -> None:
        if m.platform == Platform.KICK:
            # Kick ne permet pas de créer des clips par API : on attend ceux des viewers.
            async def find():
                return await self.kick.find_clip_for_moment(m.channel, m.detected_at)
        else:
            # Absente du top en mémoire (redémarrage, sortie du top) : on demande à Twitch.
            broadcaster_id = self.twitch_ids.get(m.channel) or await self.twitch.user_id(m.channel)
            if self.s.auto_create_clips:
                url = await self.twitch.create_clip(broadcaster_id)
                await self.db.advance(m, Status.CLIPPED, clip_url=url)
                return

            async def find():
                return await self.twitch.find_clip_for_moment(broadcaster_id, m.detected_at)

        url = None
        max_wait = self.s.clip_max_wait_s
        while url is None:
            elapsed = (datetime.now(UTC) - m.detected_at).total_seconds()
            if elapsed >= max_wait:
                raise RuntimeError(f"Aucun clip créé par les viewers en {max_wait} s")
            await asyncio.sleep(min(self.s.clip_poll_s, max_wait - elapsed))
            url = await find()
        await self.db.advance(m, Status.CLIPPED, clip_url=url)

    async def step_download(self, m: Moment) -> None:
        path = await download_clip(m.clip_url, self.work_dir(m))
        await self.db.advance(m, Status.DOWNLOADED, video_path=path)

    @property
    def source_review(self) -> bool:
        return self.s.review_source_clips and self.review.enabled

    async def step_source_review(self, m: Moment) -> None:
        """Envoie le clip brut sur Telegram ; il ne part au montage que si tu le valides."""
        await self.review.send_source_for_review(
            m.id, m.channel, m.category, m.score, Path(m.video_path))
        await self.db.advance(m, Status.SOURCE_PENDING)
        log.info("Clip #%d envoyé sur Telegram : à toi de choisir s'il part au montage", m.id)

    async def step_analyze(self, m: Moment) -> None:
        wd = self.work_dir(m)
        video = Path(m.video_path)
        transcript = await transcribe(video, wd / "transcript.json", self.s.whisper_model)
        await extract_frames(video, wd / "frames")
        await self.db.advance(m, Status.ANALYZED, transcript_path=transcript, frames_dir=wd / "frames")

    async def step_decide(self, m: Moment) -> None:
        video = Path(m.video_path)
        transcript = json.loads(Path(m.transcript_path).read_text(encoding="utf-8"))
        # Presque personne ne parle : Claude rejetterait le clip, inutile de payer l'appel.
        # (Sauf si tu l'as validé toi-même sur Telegram.)
        if not self.source_review and len(transcript["words"]) < self.s.min_transcript_words:
            await self.db.advance(m, Status.REJECTED,
                                  error=f"Moins de {self.s.min_transcript_words} mots transcrits")
            self.discard_files(m.id)
            return
        decision = await self.editor.decide(
            channel=m.channel,
            platform=m.platform,
            duration_s=await probe_duration(video),
            transcript=transcript,
            frames=sorted(Path(m.frames_dir).glob("*.jpg")),
            approved_by_human=self.source_review,
        )
        if not decision.keep:
            await self.db.advance(m, Status.REJECTED, decision_json=decision.model_dump_json(),
                                  error=f"Claude : {decision.reason}")
            self.discard_files(m.id)
            return
        await self.db.advance(m, Status.DECIDED, decision_json=decision.model_dump_json())

    async def step_render(self, m: Moment) -> None:
        """Claude écrit la composition HyperFrames, on la valide au lint puis on la rend."""
        decision = EditDecision.model_validate_json(m.decision_json)
        transcript = json.loads(Path(m.transcript_path).read_text(encoding="utf-8"))
        video, wd = Path(m.video_path), self.work_dir(m)

        # Nouvel essai après une coupure réseau ou un lint : on réutilise la composition déjà
        # payée. Seul un rendu qui a planté justifie d'en redemander une à Claude.
        previous = wd / "render" / "index.html"
        if previous.exists() and "Rendu échoué" not in (m.error or ""):
            html = previous.read_text(encoding="utf-8")
            log.info("Moment #%d : composition existante réutilisée", m.id)
        else:
            html = await self.composer.compose(
                decision=decision,
                segments=build_segments(decision.cuts),
                words=remap_words(transcript["words"], decision.cuts),
                source_size=await probe_size(video),
                frames=sorted(Path(m.frames_dir).glob("*.jpg")),
            )
        for attempt in range(self.s.max_lint_fixes + 1):
            project = self.renderer.prepare(html, video, wd)
            try:
                await self.renderer.lint(project)
                break
            except LintError as e:
                if attempt == self.s.max_lint_fixes:
                    raise
                log.info("Composition #%d refusée par le lint, correction par Claude", m.id)
                html = await self.composer.fix(html, str(e))

        out = await self.renderer.render(project, wd / "final.mp4")
        (project / "source.mp4").unlink(missing_ok=True)  # copie de travail, plus utile
        await self.db.advance(m, Status.RENDERED, render_path=out)

    async def step_review(self, m: Moment) -> None:
        """Envoie le rendu sur Telegram ; la suite dépend du bouton cliqué (on_review)."""
        decision = EditDecision.model_validate_json(m.decision_json)
        await self.review.send_for_review(m.id, m.channel, m.score, Path(m.render_path), decision)
        (Path(m.render_path).parent / "telegram_preview.mp4").unlink(missing_ok=True)
        await self.db.advance(m, Status.PENDING_REVIEW)
        log.info("Moment #%d envoyé sur Telegram pour validation", m.id)

    async def on_review(self, moment_id: int, approved: bool, stage: str = "final") -> bool:
        applied = await self.db.set_review_result(moment_id, approved, stage)
        if applied:
            if stage == "source":
                log.info("Clip #%d %s sur Telegram", moment_id,
                         "envoyé au montage" if approved else "ignoré")
            else:
                log.info("Moment #%d %s sur Telegram", moment_id,
                         "approuvé" if approved else "rejeté")
            if not approved:
                self.discard_files(moment_id)
            elif stage == "final":
                await self.prepare_publish_kit(moment_id)
        return applied

    async def prepare_publish_kit(self, moment_id: int) -> None:
        """Vidéo approuvée : kit dans data/a_publier/ et textes envoyés sur Telegram.

        La publication sur YouTube se fait à la main : rien ne part automatiquement.
        """
        m = await self.db.get(moment_id)
        if m is None or not m.render_path or not Path(m.render_path).exists():
            log.warning("Moment #%d : vidéo finale introuvable, pas de kit", moment_id)
            return
        decision = EditDecision.model_validate_json(m.decision_json)
        video, _ = build_kit(m, decision, Path(m.render_path), self.s.contact_email, self.s.clip_language)
        self.discard_files(moment_id)  # la vidéo est dans le kit, le reste ne sert plus
        log.info("Kit de publication prêt : %s", video.name)
        dest = channels.route(m)
        if dest is not None and self.s.auto_publish and dest.platforms():
            where = " + ".join(PLATFORM_NAMES[p] for p in dest.platforms())
            await self.review.send_text(
                f"🚀 #{moment_id} part en publication automatique sur « {dest.name} » ({where}).\n"
                f"Titre : {decision.title}\n"
                f"Au plus {dest.max_per_day} par jour, espacées de {dest.min_gap_minutes} min.")
            return
        # Pas de chaîne pour ce clip : publication à la main.
        # Deux messages séparés : un appui long pour copier chacun dans YouTube Studio.
        await self.review.send_text(f"📋 À publier (#{moment_id}) · YouTube : titre puis description")
        await self.review.send_text(decision.title)
        await self.review.send_text(description(decision, m, self.s.contact_email, self.s.clip_language))
        await self.review.send_text("🎵 TikTok : légende")
        await self.review.send_text(tiktok_caption(decision, m))
        await self.review.send_text(f"📁 Sur le PC : data/a_publier/{video.name}")

    # --- Publication automatique -----------------------------------------------

    def _slot_free(self, dest: channels.Destination, last: str | None, count: int) -> bool:
        if count >= dest.max_per_day:
            return False
        if last is None:
            return True
        elapsed = (datetime.now(UTC) - datetime.fromisoformat(last)).total_seconds()
        return elapsed >= dest.min_gap_minutes * 60

    async def _alert_once(self, key: str, text: str) -> None:
        if key not in self.publish_alerts:
            self.publish_alerts.add(key)
            try:
                await self.review.send_text(text)
            except Exception:  # noqa: BLE001
                log.warning("Alerte Telegram non envoyée")

    async def publish_next(self) -> bool:
        """Publie au plus une vidéo approuvée (la plus ancienne dont la chaîne a un créneau libre)."""
        dests = channels.load()
        for m in await self.db.list_by_status(Status.APPROVED):
            dest = channels.route(m, dests)
            video = find_kit(m.id)
            if dest is None or video is None:
                continue  # publication manuelle
            done = await self.db.published_targets(m.id)
            todo = [p for p in dest.platforms() if f"{p}:{dest.id}" not in done]
            if not todo:
                await self.db.set_status(m.id, Status.PUBLISHED)
                archive_kit(video)
                continue
            for platform in todo:
                target = f"{platform}:{dest.id}"
                if not dest.logged_in(platform):
                    await self._alert_once(target, f"🔑 Connecte le profil {PLATFORM_NAMES[platform]} de "
                                                   f"« {dest.name} » dans l'application (page Chaînes).")
                    continue
                fails, retry_at = self.publish_failures.get((m.id, target), (0, 0.0))
                if fails >= 3 or time.time() < retry_at:
                    continue
                last, count = await self.db.target_activity(target)
                if not self._slot_free(dest, last, count):
                    continue
                await self._publish_one(m, dest, platform, video)
                return True
        return False

    async def _publish_one(self, m: Moment, dest, platform: str, video: Path) -> None:
        decision = EditDecision.model_validate_json(m.decision_json)
        target = f"{platform}:{dest.id}"
        log.info("Publication de #%d sur %s (%s)…", m.id, platform, dest.name)
        try:
            url = await publish(dest, platform, video, title=decision.title,
                                description=description(decision, m, self.s.contact_email, self.s.clip_language),
                                caption=tiktok_caption(decision, m),
                                headless=self.s.publish_headless)
        except NotLoggedIn:
            await self._alert_once(target, f"🔑 Le profil {PLATFORM_NAMES[platform]} de « {dest.name} » "
                                           "est déconnecté : reconnecte-le dans l'application.")
            return
        except Exception as e:
            fails = self.publish_failures.get((m.id, target), (0, 0.0))[0] + 1
            self.publish_failures[(m.id, target)] = (fails, time.time() + 30 * 60)
            log.exception("Publication de #%d sur %s échouée (%d/3)", m.id, platform, fails)
            await self.review.send_text(
                f"⚠️ Échec de publication #{m.id} sur {PLATFORM_NAMES[platform]} (« {dest.name} », "
                f"essai {fails}/3"
                + (", nouvel essai dans 30 min" if fails < 3 else ", publie-le à la main")
                + f") :\n{str(e)[:500]}")
            return
        await self.db.record_publication(m.id, target, url or "")
        log.info("Publié : #%d sur %s (%s) %s", m.id, platform, dest.name, url or "")
        await self.review.send_text(
            f"✅ Publié sur {PLATFORM_NAMES[platform]} (« {dest.name} »)"
            + (f"\n{url}" if url else ""))

    async def publish_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            if self.paused or not self.s.auto_publish:
                continue
            try:
                await self.publish_next()
            except Exception:
                log.exception("Boucle de publication")

    # --- Boucle générique -------------------------------------------------------

    async def worker(
        self, status: Status, step: Step, concurrency: int = 1, uses_claude: bool = False
    ) -> None:
        sem = asyncio.Semaphore(concurrency)
        tasks: set[asyncio.Task] = set()

        async def run(m: Moment) -> None:
            try:
                await step(m)
            except UsageLimitReached as e:
                # Pas un échec : le moment garde son statut et sera repris à la réinitialisation.
                newly_paused = time.time() >= self.claude_paused_until
                self.claude_paused_until = max(self.claude_paused_until, e.resets_at)
                if newly_paused and self.review.enabled:
                    hint = ("\nLance « claude update » (ou reconnecte-toi), le pipeline réessaie "
                            "tout seul toutes les 15 min." if isinstance(e, ClaudeUnavailable) else "")
                    try:
                        await self.review.send_text(f"⏸ Montages en pause : {e}{hint}")
                    except Exception:  # noqa: BLE001  (Telegram indisponible : on log seulement)
                        log.warning("Alerte Telegram non envoyée")
                reset = datetime.fromtimestamp(e.resets_at, UTC).astimezone().strftime("%H:%M")
                log.warning("%s : moment #%d en pause jusqu'à %s", e, m.id, reset)
                await self.db.pause(m, f"En pause : {e} (reprise vers {reset})")
            except Exception as e:
                log.exception("Étape %s échouée pour le moment #%d", status, m.id)
                if await self.db.fail(m, f"{type(e).__name__}: {e}"):
                    self.discard_files(m.id)  # abandonné après 3 essais
            finally:
                sem.release()

        while True:
            if self.paused:
                await asyncio.sleep(IDLE_SLEEP_S)
                continue
            if uses_claude and time.time() < self.claude_paused_until:
                await asyncio.sleep(min(60, self.claude_paused_until - time.time() + 5))
                continue
            await sem.acquire()
            m = await self.db.claim(status)
            if m is None:
                sem.release()
                await asyncio.sleep(IDLE_SLEEP_S)
                continue
            task = asyncio.create_task(run(m))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    async def run(self) -> None:
        await self.db.connect()
        await self.cleanup_work_dirs()
        if self.paused:
            log.info("Démarrage en pause (/reprendre sur Telegram pour relancer)")
        tasks = {
            "chat Twitch": self.twitch_chat.run,
            "streams Twitch": self.refresh_top_streams,
            "détection": self.detect_loop,
            "recherche des clips": lambda: self.worker(
                Status.DETECTED, self.step_get_clip, self.s.max_clip_searches),
            "téléchargement": lambda: self.worker(Status.CLIPPED, self.step_download),
            "clips bruts": lambda: self.worker(
                Status.DOWNLOADED, self.step_source_review if self.source_review else self.step_analyze),
            "analyse": lambda: self.worker(Status.SOURCE_APPROVED, self.step_analyze),
            "décision": lambda: self.worker(Status.ANALYZED, self.step_decide, uses_claude=True),
            "montage": lambda: self.worker(Status.DECIDED, self.step_render, uses_claude=True),
            "publication": self.publish_loop,
        }
        if self.kick.enabled:
            tasks |= {"chat Kick": self.kick_chat.run, "streams Kick": self.refresh_kick_streams}
        else:
            log.info("Kick désactivé (identifiants absents ou KICK_ENABLED=false)")
        if self.review.enabled:
            tasks |= {"Telegram": self.review.run,
                      "envoi des rendus": lambda: self.worker(Status.RENDERED, self.step_review)}
        else:
            log.warning("Telegram non configuré : les rendus resteront au statut rendered")
        try:
            await asyncio.gather(*(supervised(name, f) for name, f in tasks.items()))
        finally:
            await self.http.aclose()
            await self.review.close()
            await self.db.close()
