"""Rendu 9:16 d'une composition HyperFrames écrite par Claude (pas de template).

Le projet de rendu est un dossier contenant `index.html` (la composition) et `source.mp4`.
On le valide avec `hyperframes lint` avant de lancer `hyperframes render`.
"""

import asyncio
import shutil
from pathlib import Path

from ..models import Cut

WIDTH, HEIGHT = 1080, 1920


def build_segments(cuts: list[Cut]) -> list[dict]:
    """Place les coupes bout à bout sur la timeline de sortie.

    Chaque segment donne `out_start` (début dans la vidéo finale), `media_start` (début
    dans source.mp4) et `duration`, prêts à recopier dans les attributs HyperFrames.
    """
    segments, t = [], 0.0
    for i, c in enumerate(cuts):
        duration = c.end_s - c.start_s
        segments.append({
            "id": f"seg-{i}",
            "out_start": round(t, 3),
            "media_start": round(c.start_s, 3),
            "duration": round(duration, 3),
        })
        t += duration
    return segments


def remap_words(words: list[dict], cuts: list[Cut]) -> list[dict]:
    """Garde les mots des segments conservés, avec leurs timestamps sur la timeline de sortie."""
    out, offset = [], 0.0
    for c in cuts:
        for w in words:
            mid = (w["start"] + w["end"]) / 2
            if c.start_s <= mid < c.end_s:
                start = max(w["start"], c.start_s) - c.start_s + offset
                end = min(w["end"], c.end_s) - c.start_s + offset
                out.append({"w": w["w"], "start": round(start, 2), "end": round(end, 2)})
        offset += c.end_s - c.start_s
    return out


class LintError(Exception):
    """La composition ne passe pas `hyperframes lint` : le message contient la sortie du lint."""


# Le CLI installé par `npm install` à la racine du projet (version fixée dans package.json).
LOCAL_BIN = Path(__file__).resolve().parents[3] / "node_modules" / ".bin"


def _hyperframes() -> list[str]:
    # shutil.which résout aussi hyperframes.cmd sous Windows.
    local = shutil.which("hyperframes", path=str(LOCAL_BIN))
    if local:
        return [local]
    npx = shutil.which("npx")
    if npx is None:
        raise RuntimeError("HyperFrames introuvable : lance `npm install` (Node.js 22+)")
    return [npx, "--yes", "hyperframes"]


async def _run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await proc.communicate()
    return proc.returncode, out.decode(errors="replace")


class Renderer:
    def __init__(self, max_parallel: int = 1, quality: str = "looks"):
        # Chrome headless consomme beaucoup de RAM : on limite les rendus simultanés.
        self._sem = asyncio.Semaphore(max_parallel)
        self.quality = quality

    def prepare(self, html: str, video: Path, work_dir: Path) -> Path:
        project = work_dir / "render"
        project.mkdir(parents=True, exist_ok=True)
        if not (project / "source.mp4").exists():
            shutil.copy(video, project / "source.mp4")
        (project / "index.html").write_text(html, encoding="utf-8")
        return project

    async def lint(self, project: Path) -> None:
        code, out = await _run([*_hyperframes(), "lint"], project)
        if code != 0:
            raise LintError(out[-6000:])

    async def render(self, project: Path, output: Path) -> Path:
        cmd = [*_hyperframes(), "render", "--quality", self.quality, "--output", str(output.resolve())]
        async with self._sem:
            code, out = await _run(cmd, project)
        if code != 0 or not output.exists():
            raise RuntimeError(f"Rendu échoué : {out[-800:]}")
        return output
