"""Publication YouTube / TikTok en pilotant Chrome (Playwright), sans API.

Chaque chaîne a son propre profil Chrome (data/profiles/<chaîne>-<plateforme>) : tu t'y
connectes une fois depuis l'application (bouton « Connecter »), la session reste ensuite.
Le navigateur remplit les mêmes formulaires que toi dans YouTube Studio / TikTok Studio.
Les interfaces de ces sites changent : en cas d'échec, une capture d'écran est enregistrée
dans data/publish_errors/ pour voir où ça a bloqué.

En ligne de commande (utilisé par l'application) :
    python -m autoclip.publish.browser login <chaîne> <youtube|tiktok>
"""

import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import BrowserContext, Page, async_playwright
from playwright.async_api import TimeoutError as PWTimeout

from .channels import Destination, load

log = logging.getLogger(__name__)

ERRORS_DIR = Path("data/publish_errors")
LOGIN_URLS = {
    "youtube": "https://studio.youtube.com",
    "tiktok": "https://www.tiktok.com/tiktokstudio/upload",
}
UPLOAD_TIMEOUT_MS = 20 * 60 * 1000  # envoi + traitement d'un Short


class NotLoggedIn(Exception):
    """Le profil Chrome n'est pas (ou plus) connecté : reconnecte-le depuis l'application."""


class PublishError(Exception):
    pass


async def _open(dest: Destination, platform: str, headless: bool, pw) -> BrowserContext:
    profile = dest.profile_dir(platform)
    profile.mkdir(parents=True, exist_ok=True)
    return await pw.chromium.launch_persistent_context(
        str(profile), channel="chrome", headless=headless,
        viewport={"width": 1280, "height": 900}, locale="fr-FR",
    )


async def _pause(page: Page, ms: int = 800) -> None:
    # Laisse l'interface se mettre à jour entre deux actions (rythme d'un humain pressé).
    await page.wait_for_timeout(ms)


async def _screenshot(page: Page, label: str) -> Path | None:
    try:
        ERRORS_DIR.mkdir(parents=True, exist_ok=True)
        path = ERRORS_DIR / f"{datetime.now().astimezone():%Y%m%d-%H%M%S}_{label}.png"
        await page.screenshot(path=str(path), full_page=True)
        return path
    except Exception:  # noqa: BLE001  (la capture ne doit pas masquer l'erreur d'origine)
        return None


async def _replace_text(page: Page, selector: str, text: str) -> None:
    box = page.locator(selector).first
    await box.click()
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Delete")
    await page.keyboard.insert_text(text)


# --- YouTube ------------------------------------------------------------------------------


async def _youtube(page: Page, video: Path, title: str, description: str, visibility: str,
                   dry_run: bool) -> str | None:
    await page.goto("https://www.youtube.com/upload", wait_until="domcontentloaded")
    await _pause(page, 3000)
    if "accounts.google.com" in page.url:
        raise NotLoggedIn("YouTube")

    await page.locator('input[type="file"]').first.set_input_files(str(video))
    await page.locator("#title-textarea #textbox").first.wait_for(timeout=120_000)
    await _pause(page, 2000)
    await _replace_text(page, "#title-textarea #textbox", title[:100])
    await _pause(page)
    await _replace_text(page, "#description-textarea #textbox", description[:5000])
    await _pause(page)
    await page.locator('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]').first.click()
    await _pause(page)

    for _ in range(3):  # Détails -> Éléments vidéo -> Vérifications -> Visibilité
        await page.locator("#next-button").first.click()
        await _pause(page, 1500)

    radio = {"public": "PUBLIC", "unlisted": "UNLISTED", "private": "PRIVATE"}.get(visibility, "PUBLIC")
    await page.locator(f'tp-yt-paper-radio-button[name="{radio}"]').first.click()
    await _pause(page)

    url = None
    link = page.locator("span.video-url-fadeable a, ytcp-video-info a.ytcp-video-info").first
    try:
        url = await link.get_attribute("href", timeout=30_000)
    except PWTimeout:
        log.warning("Lien de la vidéo YouTube introuvable (publication quand même)")

    if dry_run:
        log.info("Test YouTube : formulaire rempli, publication non validée (brouillon privé)")
        return url

    # « Publier » ne s'active qu'une fois l'envoi terminé.
    done = page.locator("#done-button").first
    await page.wait_for_function(
        "b => b && b.getAttribute('aria-disabled') !== 'true'",
        arg=await done.element_handle(), timeout=UPLOAD_TIMEOUT_MS,
    )
    await done.click()
    await page.locator("ytcp-video-share-dialog, ytcp-prechecks-warning-dialog").first.wait_for(
        timeout=120_000)
    warning = page.locator("ytcp-prechecks-warning-dialog #publish-button")
    if await warning.count():
        await warning.first.click()  # « Publier quand même » après les vérifications
    await _pause(page, 3000)
    return url


# --- TikTok -------------------------------------------------------------------------------


def split_caption(caption: str) -> tuple[str, list[str]]:
    """Sépare la légende en texte et hashtags (tapés à part pour fermer les suggestions)."""
    text_lines, tags = [], []
    for line in caption.splitlines():
        words = line.split()
        if words and all(w.startswith("#") for w in words):
            tags += words
        else:
            text_lines.append(line)
    return "\n".join(text_lines).strip(), tags


async def _tiktok(page: Page, video: Path, caption: str, dry_run: bool) -> str | None:
    await page.goto(LOGIN_URLS["tiktok"], wait_until="domcontentloaded")
    await _pause(page, 4000)
    if "/login" in page.url:
        raise NotLoggedIn("TikTok")

    await page.locator('input[type="file"]').first.set_input_files(str(video))
    editor = 'div[contenteditable="true"]'
    await page.locator(editor).first.wait_for(timeout=120_000)
    await _pause(page, 3000)

    # Texte d'abord, puis chaque hashtag suivi d'un espace (ferme la liste de suggestions).
    text, tags = split_caption(caption)
    await _replace_text(page, editor, text + "\n\n")
    for tag in tags:
        await page.keyboard.type(tag, delay=60)
        await _pause(page, 700)
        await page.keyboard.press("Space")
        await page.keyboard.press("Escape")
    await _pause(page)

    if dry_run:
        log.info("Test TikTok : légende remplie, publication non validée")
        return None

    post = page.locator('button[data-e2e="post_video_button"]').first
    await page.wait_for_function(
        "b => b && !b.disabled && b.getAttribute('aria-disabled') !== 'true' "
        "&& b.getAttribute('data-disabled') !== 'true'",
        arg=await post.element_handle(), timeout=UPLOAD_TIMEOUT_MS,
    )
    await post.click()
    await _pause(page, 3000)
    confirm = page.locator('button:has-text("Post now"), button:has-text("Publier maintenant")')
    if await confirm.count():
        await confirm.first.click()  # avertissement de vérification du contenu
    await page.wait_for_url("**/tiktokstudio/content**", timeout=180_000)
    return None


# --- Points d'entrée ----------------------------------------------------------------------


async def publish(dest: Destination, platform: str, video: Path, *, title: str, description: str,
                  caption: str, headless: bool = False, dry_run: bool = False) -> str | None:
    """Publie la vidéo sur la plateforme de cette chaîne. Retourne l'URL si connue."""
    async with async_playwright() as pw:
        ctx = await _open(dest, platform, headless, pw)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            if platform == "youtube":
                return await _youtube(page, video, title, description, dest.youtube_visibility,
                                      dry_run)
            return await _tiktok(page, video, caption, dry_run)
        except NotLoggedIn:
            (dest.profile_dir(platform) / ".connected").unlink(missing_ok=True)
            raise
        except Exception as e:
            shot = await _screenshot(page, f"{dest.id}-{platform}")
            where = f" (capture : {shot})" if shot else ""
            raise PublishError(f"{platform} / {dest.name} : {type(e).__name__}: {e}"[:400] + where) from e
        finally:
            if dry_run:
                await _pause(page, 60_000)  # une minute pour vérifier le formulaire
            await ctx.close()


def chrome_exe() -> str:
    for base in (os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", ""),
                 os.environ.get("LOCALAPPDATA", "")):
        exe = Path(base) / "Google/Chrome/Application/chrome.exe"
        if base and exe.exists():
            return str(exe)
    raise RuntimeError("Google Chrome introuvable")


def login(dest: Destination, platform: str) -> bool:
    """Ouvre ton vrai Chrome (non piloté) avec le profil de la chaîne ; rend la main à sa fermeture.

    Google refuse de se connecter dans un Chrome piloté par Playwright (« navigateur non
    sécurisé ») : la connexion se fait donc dans un Chrome normal, et la session enregistrée
    dans le profil sert ensuite aux publications. La fenêtre reste ouverte tant que tu veux
    (pratique aussi pour regarder des vidéos avec la chaîne avant de publier).
    """
    profile = dest.profile_dir(platform)
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.run([chrome_exe(), f"--user-data-dir={profile.resolve()}", "--no-first-run",
                    "--new-window", LOGIN_URLS[platform]], check=False)
    # Chrome rend la main dès que la fenêtre est fermée. Une session = des cookies enregistrés.
    connected = (profile / "Default" / "Network" / "Cookies").exists()
    if connected:
        (profile / ".connected").write_text(datetime.now().astimezone().isoformat())
    return connected


def main() -> None:
    if len(sys.argv) != 4 or sys.argv[1] != "login" or sys.argv[3] not in LOGIN_URLS:
        print(__doc__)
        sys.exit(2)
    dest = next((d for d in load() if d.id == sys.argv[2]), None)
    if dest is None:
        print(f"Chaîne inconnue : {sys.argv[2]}")
        sys.exit(2)
    ok = login(dest, sys.argv[3])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
