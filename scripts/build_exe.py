"""Construit AutoClip.exe (l'application de contrôle) à la racine du projet.

    .venv\\Scripts\\python scripts\\build_exe.py

L'exe ne contient que l'interface : il lance le pipeline avec le Python du venv du projet,
il doit donc rester dans le dossier du projet (ou un de ses sous-dossiers).
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICON = ROOT / "src/autoclip/gui/autoclip.ico"


def make_icon() -> None:
    from PIL import Image, ImageDraw

    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, size - 8, size - 8), radius=56, fill=(124, 92, 255))
    # Bouton lecture stylisé + trait de coupe : un clip.
    d.polygon([(96, 70), (96, 186), (190, 128)], fill=(255, 255, 255))
    d.line([(60, 212), (196, 44)], fill=(15, 17, 23), width=14)
    img.save(ICON, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def main() -> None:
    make_icon()
    entry = ROOT / "scripts/autoclip_gui.py"
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed",
            "--name", "AutoClip",
            "--icon", str(ICON),
            "--add-data", f"{ICON};autoclip/gui",
            "--paths", str(ROOT / "src"),
            "--collect-data", "customtkinter",
            "--distpath", str(ROOT / "build/dist"),
            "--workpath", str(ROOT / "build/work"),
            "--specpath", str(ROOT / "build"),
            str(entry),
        ],
        check=True,
        cwd=ROOT,
    )
    target = ROOT / "AutoClip.exe"
    shutil.copy2(ROOT / "build/dist/AutoClip.exe", target)
    print(f"\nPrêt : {target}")


if __name__ == "__main__":
    main()
