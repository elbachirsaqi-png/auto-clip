"""Transcription mot par mot avec faster-whisper (extra `transcribe`)."""

import asyncio
import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np

# Windows sans mode développeur : pas de symlinks dans le cache Hugging Face, c'est sans gravité.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

SAMPLE_RATE = 16000  # fréquence attendue par Whisper


def _load_audio(video: Path) -> np.ndarray:
    """Décode l'audio en mono 16 kHz float32 avec FFmpeg.

    On ne laisse pas faster-whisper décoder lui-même : il passe par PyAV, dont l'API
    change d'une version à l'autre (ex. `metadata_errors` retiré dans PyAV 19).
    """
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(video),
         "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg a échoué : {proc.stderr.decode(errors='replace')[-500:]}")
    return np.frombuffer(proc.stdout, np.int16).astype(np.float32) / 32768.0


@lru_cache(maxsize=1)
def _model(size: str = "small", device: str = "auto"):
    from faster_whisper import WhisperModel  # import tardif : dépendance lourde

    return WhisperModel(size, device=device, compute_type="int8")


def _transcribe_sync(video: Path, model_size: str) -> dict:
    segments, info = _model(model_size).transcribe(
        _load_audio(video), word_timestamps=True, vad_filter=True
    )
    words = [
        {"w": w.word.strip(), "start": round(w.start, 2), "end": round(w.end, 2)}
        for seg in segments
        for w in (seg.words or [])
    ]
    return {"language": info.language, "words": words}


async def transcribe(video: Path, out_path: Path, model_size: str = "small") -> Path:
    """Écrit {language, words: [{w, start, end}]} en JSON et retourne le chemin."""
    result = await asyncio.to_thread(_transcribe_sync, video, model_size)
    out_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return out_path
