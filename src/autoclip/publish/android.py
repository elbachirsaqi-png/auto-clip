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
import subprocess
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


def connect(serial: str = ""):
    """Connexion au téléphone. Si le service ADB l'a « perdu » (micro-coupure USB : il reste
    branché mais adb ne le voit plus), on redémarre le service et on réessaie."""
    try:
        return u2.connect(serial or None)
    except Exception:  # noqa: BLE001  (adbutils lève AdbError ou RuntimeError selon les cas)
        adb = adb_path()
        subprocess.run([adb, "kill-server"], capture_output=True, check=False)
        subprocess.run([adb, "start-server"], capture_output=True, check=False)
        time.sleep(3)
        return u2.connect(serial or None)


def _tap_text(d, pattern: str, timeout: float) -> None:
    """Appuie sur le premier élément dont le texte ou la description correspond (FR / EN)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        # Les vrais boutons d'abord : un texte « Post » existe aussi ailleurs à l'écran.
        for sel in (d(textMatches=pattern, className="android.widget.Button"),
                    d(textMatches=pattern), d(descriptionMatches=pattern)):
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
                       # La galerie enregistre /storage/emulated/0/…, pas le raccourci /sdcard.
                       "--projection", "_id", "--where",
                       f"_data LIKE '%{remote.removeprefix('/sdcard')}'"]).output
        found = re.search(r"_id=(\d+)", out)
        if found:
            return f"content://media/external/video/media/{found.group(1)}"
        time.sleep(1)
    raise RuntimeError("TikTok Android : vidéo non indexée par la galerie du téléphone")


def publish_tiktok(video: Path, caption: str, serial: str = "", dry_run: bool = False) -> None:
    import os

    os.environ.setdefault("ADBUTILS_ADB_PATH", adb_path())  # adb hors du PATH (winget)
    d = connect(serial)
    apps = set(d.app_list())
    pkg = next((p for p in TIKTOK_PACKAGES if p in apps), None)
    if pkg is None:
        raise RuntimeError("TikTok n'est pas installé sur le téléphone")

    d.screen_on()
    d.unlock()  # glisse l'écran de verrouillage (sans code)
    d.shell(["rm", "-rf", REMOTE_DIR])  # vidéos des publications précédentes
    d.shell(["mkdir", "-p", REMOTE_DIR])
    # Nom simple : une apostrophe (« Can't ») cassait la recherche dans la galerie.
    remote = f"{REMOTE_DIR}/autoclip_{int(time.time())}.mp4"
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
    # Après un hashtag, TikTok ouvre une liste de suggestions (« #twitch · 40.7M posts ») qui
    # recouvre les boutons du bas : on touche la première suggestion, ce qui la ferme.
    for _ in range(3):
        if not d(textMatches=r"(?i)^[\d.,]+\s*[KMB]?\s*(posts|publications|vidéos|videos)$").exists:
            break
        d(textMatches=r"^#\S+$", className="android.widget.TextView").click()
        time.sleep(1.5)
    if "mInputShown=true" in d.shell(["dumpsys", "input_method"]).output:
        d.press("back")  # le clavier cache les boutons du bas : Retour le ferme d'abord
        time.sleep(1.5)

    if dry_run:
        _tap_text(d, r"(?i)^(brouillons?|drafts?)$", timeout=20)
        time.sleep(3)
        d.press("home")
        return
    _tap_text(d, r"(?i)^(publier|post)$", timeout=20)
    # Après « Post », TikTok ouvre le fil et lit des vidéos en boucle : on retourne à l'accueil
    # dès le début de l'envoi. Pas de fermeture forcée : l'envoi continue en arrière-plan.
    time.sleep(15)
    d.press("home")
    time.sleep(75)  # temps de l'envoi en arrière-plan
    # Pas d'écran éteint : verrouillé des heures, ColorOS repassait l'USB en mode MIDI
    # (plus d'ADB) et la publication suivante échouait. « Stay awake » le garde allumé.


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] != "test":
        print(__doc__)
        sys.exit(2)
    publish_tiktok(Path(sys.argv[2]), "Test AutoClip 🎬\n\n#test", dry_run=True)
    print("Brouillon enregistré dans TikTok : vérifie-le puis supprime-le.")


if __name__ == "__main__":
    main()
