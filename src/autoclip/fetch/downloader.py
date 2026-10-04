import asyncio
import sys
from pathlib import Path


async def download_clip(url: str, out_dir: Path) -> Path:
    """Télécharge un clip avec yt-dlp et retourne le chemin du MP4."""
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "source.mp4"
    # Via le Python courant plutôt que l'exécutable yt-dlp : le dossier Scripts du venv n'est
    # pas dans le PATH quand le pipeline est lancé par AutoClip.exe (venv non activé).
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "yt_dlp", "-f", "best[ext=mp4]/best", "--merge-output-format", "mp4",
        "-o", str(target), "--no-progress", url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0 or not target.exists():
        raise RuntimeError(f"yt-dlp a échoué ({proc.returncode}) : {err.decode(errors='replace')[-500:]}")
    return target
