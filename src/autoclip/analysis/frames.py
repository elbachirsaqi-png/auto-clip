import asyncio
import json
from pathlib import Path


async def _run(*args: str) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"{args[0]} a échoué : {err.decode(errors='replace')[-500:]}")
    return out


async def probe_duration(video: Path) -> float:
    out = await _run("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video))
    return float(json.loads(out)["format"]["duration"])


async def probe_size(video: Path) -> tuple[int, int]:
    out = await _run(
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "json", str(video),
    )
    stream = json.loads(out)["streams"][0]
    return stream["width"], stream["height"]


async def extract_frames(video: Path, out_dir: Path, count: int = 4) -> list[Path]:
    """Extrait `count` frames réparties uniformément (JPEG 480p) pour l'analyse par Claude.

    480p plutôt que 720p : une image coûte environ largeur x hauteur / 750 tokens,
    soit ~550 tokens au lieu de ~1 230, et reste lisible pour juger le clip.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = await probe_duration(video)
    paths = []
    for i in range(count):
        t = duration * (i + 0.5) / count
        p = out_dir / f"frame_{i:02d}_{t:.1f}s.jpg"
        await _run(
            "ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video),
            "-frames:v", "1", "-vf", "scale=-2:480", "-q:v", "4", str(p),
        )
        paths.append(p)
    return paths
