"""Publication TikTok depuis un téléphone Android branché au PC (ADB + uiautomator2).

La vidéo est copiée sur le téléphone puis « partagée » vers l'application TikTok (comme
le bouton Partager de la galerie) ; on appuie ensuite sur Suivant, on colle la légende et
on publie. C'est donc l'application qui publie, pas un navigateur.

Prérequis : débogage USB activé et autorisé, écran sans code de verrouillage, option
développeur « Rester activé » (écran allumé pendant la charge), TikTok connecté.

Test sans publier (enregistre en brouillon) :
    python -m autoclip.publish.android test <video.mp4>
"""

import re
import shutil
import sys
import time
from pathlib import Path

import uiautomator2 as u2

TIKTOK_PACKAGES = ("com.zhiliaoapp.musically", "com.ss.android.ugc.trill")
REMOTE_DIR = "/sdcard/DCIM/AutoClip"


def adb_path() -> str:
    found = shutil.which("adb")
    if found:
        return found
    for exe in Path.home().glob("AppData/Local/Microsoft/WinGet/Packages/Google.PlatformTools*/platform-tools/adb.exe"):
        return str(exe)
    raise RuntimeError("adb introuvable : installe Google Platform Tools")


def _tap_text(d, pattern: str, timeout: float) -> None:
    """Appuie sur le premier élément dont le texte ou la description correspond (FR / EN)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for sel in (d(textMatches=pattern), d(descriptionMatches=pattern)):
            if sel.exists:
                sel.click()
                return
        time.sleep(1)
    raise RuntimeError(f"TikTok Android : bouton « {pattern} » introuvable")


def _media_uri(d, remote: str) -> str:
    """URI content:// de la vidéo une fois indexée par la galerie (TikTok refuse les file://)."""
    d.shell(["am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
             "-d", f"file://{remote}"])
    for _ in range(20):
        out = d.shell(["content", "query", "--uri", "content://media/external/video/media",
                       "--projection", "_id", "--where", f"_data='{remote}'"]).output
        found = re.search(r"_id=(\d+)", out)
        if found:
            return f"content://media/external/video/media/{found.group(1)}"
        time.sleep(1)
    raise RuntimeError("TikTok Android : vidéo non indexée par la galerie du téléphone")


def publish_tiktok(video: Path, caption: str, serial: str = "", dry_run: bool = False) -> None:
    import os

    os.environ.setdefault("ADBUTILS_ADB_PATH", adb_path())  # adb hors du PATH (winget)
    d = u2.connect(serial or None)
    apps = set(d.app_list())
    pkg = next((p for p in TIKTOK_PACKAGES if p in apps), None)
    if pkg is None:
        raise RuntimeError("TikTok n'est pas installé sur le téléphone")

    d.screen_on()
    d.unlock()  # glisse l'écran de verrouillage (sans code)
    d.shell(["rm", "-rf", REMOTE_DIR])  # vidéos des publications précédentes
    d.shell(["mkdir", "-p", REMOTE_DIR])
    remote = f"{REMOTE_DIR}/{video.name.replace(' ', '_')}"
    d.push(str(video), remote)
    uri = _media_uri(d, remote)

    d.app_stop(pkg)  # repart d'un écran propre
    d.shell(["am", "start", "-a", "android.intent.action.SEND", "-t", "video/mp4",
             "--eu", "android.intent.extra.STREAM", uri, "--grant-read-uri-permission", "-p", pkg])

    _tap_text(d, r"(?i)^(suivant|next)$", timeout=90)  # éditeur vidéo -> écran de publication
    field = d(className="android.widget.EditText")
    if not field.wait(timeout=30):
        raise RuntimeError("TikTok Android : champ de description introuvable")
    field.click()
    d.send_keys(caption, clear=True)
    time.sleep(2)

    if dry_run:
        _tap_text(d, r"(?i)^(brouillons?|drafts?)$", timeout=20)
        return
    _tap_text(d, r"(?i)^(publier|post)$", timeout=20)
    time.sleep(90)  # TikTok envoie la vidéo en arrière-plan : écran allumé le temps de l'envoi


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] != "test":
        print(__doc__)
        sys.exit(2)
    publish_tiktok(Path(sys.argv[2]), "Test AutoClip 🎬\n\n#test", dry_run=True)
    print("Brouillon enregistré dans TikTok : vérifie-le puis supprime-le.")


if __name__ == "__main__":
    main()
