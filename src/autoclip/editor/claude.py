"""Claude monteur et réalisateur.

- `ClipEditor` : transcription + frames -> EditDecision (JSON validé).
- `ClipComposer` : EditDecision -> composition HyperFrames (HTML) écrite librement par Claude.
"""

import base64
import json
import logging
import re
from pathlib import Path

import anthropic

from ..config import Settings
from ..models import EditDecision
from .subscription import run_claude

log = logging.getLogger(__name__)

# Prix en $ par million de tokens (entrée, sortie), pour estimer le coût dans les logs.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

SYSTEM_PROMPT = (Path(__file__).parent / "prompt.md").read_text(encoding="utf-8")
COMPOSE_PROMPT = (Path(__file__).parent / "compose_prompt.md").read_text(encoding="utf-8")


def _client(settings: Settings) -> anthropic.AsyncAnthropic:
    headers = {}
    if settings.anthropic_workspace_id:
        headers["anthropic-workspace-id"] = settings.anthropic_workspace_id
    return anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key or None, default_headers=headers or None
    )


def _log_usage(step: str, model: str, usage) -> None:
    inp, out = usage.input_tokens, usage.output_tokens
    price = PRICES.get(model)
    cost = f" ≈ ${(inp * price[0] + out * price[1]) / 1e6:.3f}" if price else ""
    log.info("Claude %s : %d tokens en entrée, %d en sortie%s", step, inp, out, cost)


def _log_subscription(step: str, result: dict) -> None:
    usage = result.get("usage") or {}
    log.info("Claude %s (abonnement) : %d tokens en entrée, %d en sortie, %.0f s", step,
             usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0),
             usage.get("output_tokens", 0), result.get("duration_ms", 0) / 1000)


def _compact(data: dict) -> str:
    # JSON sans espaces : moins de tokens pour la transcription mot par mot.
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _frames_content(frames: list[Path]) -> list[dict]:
    content: list[dict] = []
    for f in frames:
        content.append({"type": "text", "text": f"Frame : {f.name}"})
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.standard_b64encode(f.read_bytes()).decode(),
            },
        })
    return content


class ClipEditor:
    def __init__(self, settings: Settings):
        self.s = settings
        self.client = _client(settings)

    async def decide(
        self,
        *,
        channel: str,
        platform: str,
        duration_s: float,
        transcript: dict,
        frames: list[Path],
        facecam_hint: dict | None = None,
        approved_by_human: bool = False,
    ) -> EditDecision:
        content = _frames_content(frames)
        content.append({
            "type": "text",
            "text": _compact(
                {
                    "channel": channel,
                    "platform": platform,
                    "target_language": self.s.clip_language,
                    "duration_s": round(duration_s, 2),
                    "facecam_detection": facecam_hint,
                    "approved_by_human": approved_by_human,
                    "transcript": transcript,
                },
            ),
        })

        model = self.s.decide_model or self.s.claude_model
        if self.s.claude_backend == "subscription":
            result, _ = await run_claude(
                system=SYSTEM_PROMPT, content=content, model=model,
                effort=self.s.decide_effort, json_schema=EditDecision.model_json_schema(),
            )
            _log_subscription("décision", result)
            data = result.get("structured_output")
            if data is None:
                raise RuntimeError("Claude Code n'a pas renvoyé de décision structurée")
            decision = EditDecision.model_validate(data)
            decision.validate_against(duration_s)
            return decision

        response = await self.client.messages.parse(
            model=model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            output_config={"effort": self.s.decide_effort},
            messages=[{"role": "user", "content": content}],
            output_format=EditDecision,
        )
        _log_usage("décision", model, response.usage)
        if response.stop_reason == "refusal":
            raise RuntimeError(f"Claude a refusé : {response.stop_details}")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("Réponse tronquée (max_tokens)")

        decision = response.parsed_output
        decision.validate_against(duration_s)
        return decision


class ClipComposer:
    """Fait écrire à Claude la composition HyperFrames complète, sans template."""

    def __init__(self, settings: Settings):
        self.s = settings
        self.client = _client(settings)

    async def compose(
        self,
        *,
        decision: EditDecision,
        segments: list[dict],
        words: list[dict],
        source_size: tuple[int, int],
        frames: list[Path],
    ) -> str:
        # La décision décrit déjà le clip : deux images suffisent pour situer la mise en page.
        content = _frames_content(frames[::2])
        content.append({
            "type": "text",
            "text": _compact(
                {
                    "decision": decision.model_dump(),
                    "target_language": self.s.clip_language,
                    "source_size": {"width": source_size[0], "height": source_size[1]},
                    "total_duration_s": round(sum(s["duration"] for s in segments), 3),
                    "segments": segments,
                    "words": words,
                },
            ),
        })
        return await self._ask([{"role": "user", "content": content}], "composition")

    async def fix(self, html: str, lint_output: str) -> str:
        """Renvoie la composition à Claude avec les erreurs du lint pour qu'il la corrige."""
        prompt = (
            "Cette composition HyperFrames ne passe pas `hyperframes lint`. Corrige toutes les "
            "erreurs sans changer l'intention créative, et renvoie le document complet.\n\n"
            f"Sortie du lint :\n{lint_output}\n\nComposition :\n```html\n{html}\n```"
        )
        return await self._ask([{"role": "user", "content": prompt}], "correction")

    async def _ask(self, messages: list[dict], step: str) -> str:
        if self.s.claude_backend == "subscription":
            content = messages[-1]["content"]
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            result, _ = await run_claude(
                system=COMPOSE_PROMPT, content=content, model=self.s.claude_model,
                effort=self.s.compose_effort,
            )
            _log_subscription(step, result)
            text = str(result.get("result", ""))
        else:
            text = await self._ask_api(messages, step)

        match = re.search(r"```html\s*(.*?)```", text, re.DOTALL)
        html = (match.group(1) if match else text).strip()
        if 'data-composition-id="main"' not in html:
            raise RuntimeError("La réponse de Claude ne contient pas de composition HyperFrames")
        return html

    async def _ask_api(self, messages: list[dict], step: str) -> str:
        # Streaming : la composition peut être longue, un appel non streamé risquerait d'expirer.
        async with self.client.beta.messages.stream(
            model=self.s.claude_model,
            max_tokens=64000,
            system=COMPOSE_PROMPT,
            output_config={"effort": self.s.compose_effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=messages,
        ) as stream:
            response = await stream.get_final_message()
        _log_usage(step, response.model, response.usage)
        if response.stop_reason == "refusal":
            raise RuntimeError(f"Claude a refusé : {response.stop_details}")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("Composition tronquée (max_tokens)")
        return "".join(b.text for b in response.content if b.type == "text")
