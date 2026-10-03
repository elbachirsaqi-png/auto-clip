"""Lecture / écriture du fichier .env en conservant commentaires, ordre et lignes inconnues."""

import re
from pathlib import Path

_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw


def _quote(value: str) -> str:
    # Guillemets seulement si nécessaire (espaces, #, guillemets), comme python-dotenv les lit.
    if value == "" or re.fullmatch(r"[^\s#\"']*", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _LINE.match(line)
        if m:
            values[m.group(1).upper()] = _unquote(m.group(2))
    return values


def write_env(path: Path, updates: dict[str, str]) -> None:
    """Met à jour les clés existantes sur place et ajoute les nouvelles à la fin."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = {k.upper(): v for k, v in updates.items()}
    out = []
    for line in lines:
        m = _LINE.match(line)
        key = m.group(1).upper() if m else None
        if key in pending:
            out.append(f"{key}={_quote(pending.pop(key))}")
        else:
            out.append(line)
    if pending:
        if out and out[-1].strip():
            out.append("")
        out.extend(f"{k}={_quote(v)}" for k, v in pending.items())
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
