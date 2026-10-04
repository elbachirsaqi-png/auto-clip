"""Appels à Claude via Claude Code (`claude -p`) : utilise l'abonnement Claude au lieu de l'API.

Claude Code tourne en mode non interactif, sans outils, plugins, MCP ni réglages de projet,
avec notre prompt système. Les messages (texte + images) sont envoyés en stream-json.
Quand la limite d'usage de l'abonnement est atteinte, on lève UsageLimitReached avec
l'heure de réinitialisation : le pipeline met alors les moments en pause.
"""

import asyncio
import json
import logging
import shutil
import tempfile
import time

log = logging.getLogger(__name__)

# Modèles de l'API -> alias compris par Claude Code (les noms complets passent aussi).
DEFAULT_RESET_WAIT_S = 30 * 60  # si Claude Code ne donne pas l'heure de réinitialisation
LIMIT_WORDS = ("usage limit", "rate limit", "limit reached", "limit will reset", "out of extra usage")


class UsageLimitReached(Exception):
    """Limite de l'abonnement atteinte. `resets_at` = timestamp Unix de la réinitialisation."""

    def __init__(self, resets_at: float, message: str = ""):
        self.resets_at = resets_at
        super().__init__(message or "Limite de l'abonnement Claude atteinte")


def claude_cli() -> str:
    path = shutil.which("claude")
    if path is None:
        raise RuntimeError("Claude Code introuvable : installe-le ou repasse en mode API")
    return path


async def run_claude(
    *,
    system: str,
    content: list[dict],
    model: str,
    effort: str,
    json_schema: dict | None = None,
    timeout_s: int = 900,
) -> tuple[dict, dict]:
    """Envoie un message à Claude Code. Retourne (événement result, dernier rate_limit_info)."""
    effort = "high" if effort == "xhigh" else effort  # Claude Code : low, medium, high, max
    message = {"type": "user", "message": {"role": "user", "content": content}}
    with tempfile.TemporaryDirectory(prefix="autoclip_claude_") as cwd:
        cmd = [
            claude_cli(), "-p",
            "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--model", model, "--effort", effort,
            "--system-prompt", system,
            "--tools", "", "--strict-mcp-config", "--disable-slash-commands",
            "--setting-sources", "", "--no-session-persistence",
        ]
        if json_schema is not None:
            cmd += ["--json-schema", json.dumps(json_schema)]
        # Dossier vide : pas de CLAUDE.md ni de fichiers du projet dans le contexte.
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=64 * 1024 * 1024,
        )
        try:
            out, err = await asyncio.wait_for(
                proc.communicate((json.dumps(message) + "\n").encode()), timeout_s
            )
        except TimeoutError:
            proc.kill()
            raise RuntimeError(f"Claude Code n'a pas répondu en {timeout_s} s") from None

    result, rate = None, {}
    for line in out.decode("utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "rate_limit_event":
            rate = event.get("rate_limit_info") or {}
        elif event.get("type") == "result":
            result = event

    if rate.get("status") == "rejected":
        raise UsageLimitReached(_reset_time(rate), _limit_message(rate))
    stderr = err.decode("utf-8", errors="replace")
    if result is None:
        if _looks_like_limit(stderr):
            raise UsageLimitReached(_reset_time(rate))
        raise RuntimeError(f"Claude Code a échoué ({proc.returncode}) : {stderr[-500:]}")
    if result.get("is_error"):
        text = str(result.get("result", "")) + " " + str(result.get("errors", ""))
        if _looks_like_limit(text):
            raise UsageLimitReached(_reset_time(rate))
        raise RuntimeError(f"Claude Code : {text.strip()[:500]}")
    return result, rate


def _looks_like_limit(text: str) -> bool:
    low = text.lower()
    return any(w in low for w in LIMIT_WORDS)


def _reset_time(rate: dict) -> float:
    resets = rate.get("resetsAt")
    if isinstance(resets, (int, float)) and resets > time.time():
        return float(resets)
    return time.time() + DEFAULT_RESET_WAIT_S


def _limit_message(rate: dict) -> str:
    kind = {"five_hour": "limite des 5 heures", "seven_day": "limite hebdomadaire"}.get(
        rate.get("rateLimitType", ""), "limite d'usage"
    )
    return f"Abonnement Claude : {kind} atteinte"
