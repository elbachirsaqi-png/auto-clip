"""Soumission automatique des clips publiés aux campagnes Content Rewards (contentrewards.com).

Une campagne paie les vues des clips d'un streamer précis. Après chaque publication d'un clip
de ce streamer, on soumet le lien de la vidéo à sa campagne : le site n'accepte que les
publications de moins de 30 minutes. Pas d'API : Chrome remplit la fenêtre « Soumettre le clip »
avec ta session (profil data/profiles/contentrewards, connexion :
python -m autoclip.publish.rewards login).

Campagnes dans data/campaigns.json (à rejoindre d'abord sur le site) :
  id          : numéro de la campagne (dans son adresse contentrewards.com/c/campaigns/<id>)
  streamers   : chaînes Twitch / Kick dont les clips vont dans cette campagne
  category    : mot obligatoire dans la catégorie du stream (ex. « grand theft auto »), vide = tout
  channels    : chaînes AutoClip autorisées (ex. ["gta6"] si la campagne exige un compte GTA 6)
  hashtags    : hashtags imposés ; cta : ligne imposée dans la légende et la description
  until       : date limite AAAA-MM-JJ (vide = aucune)
"""

import asyncio
import json
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from ..models import EditDecision, Moment

log = logging.getLogger(__name__)

CAMPAIGNS_FILE = Path("data/campaigns.json")
PROFILE = Path("data/profiles/contentrewards")
SITE = "https://contentrewards.com"


class Campaign(BaseModel):
    id: str
    name: str
    enabled: bool = True
    streamers: list[str]
    category: str = ""
    channels: list[str] = []
    platforms: list[str] = ["youtube", "tiktok"]
    hashtags: list[str] = []
    cta: str = ""
    until: str = ""


def load() -> list[Campaign]:
    if not CAMPAIGNS_FILE.exists():
        return []
    return [Campaign.model_validate(c) for c in json.loads(CAMPAIGNS_FILE.read_text(encoding="utf-8"))]


def match(m: Moment, channel_id: str, campaigns: list[Campaign] | None = None) -> Campaign | None:
    """La campagne qui paie ce clip sur cette chaîne, ou None. Un clip = une seule campagne."""
    for c in campaigns if campaigns is not None else load():
        if (c.enabled and m.channel.lower() in {s.lower() for s in c.streamers}
                and c.category.lower() in (m.category or "").lower()
                and (not c.channels or channel_id in c.channels)
                and (not c.until or datetime.now().astimezone().date().isoformat() <= c.until)):
            return c
    return None


def apply(decision: EditDecision, c: Campaign | None) -> EditDecision:
    """Hashtags de la campagne en tête (TikTok n'en garde que 5)."""
    if c is None:
        return decision
    have = {h.lower().lstrip("#") for h in decision.hashtags}
    extra = [h for h in c.hashtags if h.lower().lstrip("#") not in have]
    return decision.model_copy(update={"hashtags": [*extra, *decision.hashtags]})


async def _tiktok_link(page, c: Campaign, title: str, since: float) -> str | None:
    """Lien TikTok de la vidéo qui vient de sortir : le site liste lui-même les publications
    récentes des comptes reliés (avec quelques minutes de retard sur TikTok)."""
    for _ in range(25):
        r = await page.request.get(f"{SITE}/api/submission/submissions/candidate-videos?campaignId={c.id}")
        for v in (await r.json()).get("data", {}).get("videos", []):
            published = datetime.fromisoformat(v["publishedAt"]).timestamp()
            if (v["platform"] == "tiktok" and v.get("state") != "too_old" and published >= since - 120
                    and title[:30].lower() in (v.get("title") or "").lower()):
                return v["url"]
        await asyncio.sleep(60)
    return None


async def _submitted_count(page) -> int:
    r = await page.request.get(f"{SITE}/api/submission/submissions/counts?creatorView=true")
    return int((await r.json())["data"]["all"])


async def submit(c: Campaign, platform: str, url: str | None, title: str, since: float) -> str:
    """Soumet la publication à la campagne. Retourne le lien soumis, lève RuntimeError sinon."""
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            str(PROFILE.resolve()), channel="chrome", headless=True, viewport={"width": 1400, "height": 1000})
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            await page.goto(f"{SITE}/c/campaigns/{c.id}", wait_until="domcontentloaded")
            await page.wait_for_timeout(4000)
            if "/c/campaigns/" not in page.url:
                raise RuntimeError("session Content Rewards expirée : python -m autoclip.publish.rewards login")
            if platform == "tiktok" and not url:
                url = await _tiktok_link(page, c, title, since)
            if not url:
                raise RuntimeError("lien de la vidéo introuvable")
            before = await _submitted_count(page)
            await page.get_by_role("button", name="Soumettre le clip").first.click()
            dialog = page.get_by_role("dialog", name="Soumettre un clip")
            await dialog.locator("input:not([type=checkbox])").first.fill(url)
            await page.wait_for_timeout(3000)  # le site vérifie le lien
            await dialog.locator("input[type=checkbox]").first.check(force=True)
            await dialog.get_by_role("button", name="Soumettre le clip").click()
            for _ in range(20):
                await page.wait_for_timeout(2000)
                if await _submitted_count(page) > before:
                    return url
            raise RuntimeError(f"soumission non confirmée : {(await dialog.inner_text())[:300]}")
        except Exception:
            shot = Path("data/publish_errors") / f"{time.strftime('%Y%m%d-%H%M%S')}_contentrewards.png"
            shot.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(shot))
            raise
        finally:
            await ctx.close()


def login() -> None:
    """Ouvre ton vrai Chrome sur le profil Content Rewards ; ferme la fenêtre une fois connecté."""
    from .browser import chrome_exe

    PROFILE.mkdir(parents=True, exist_ok=True)
    subprocess.run([chrome_exe(), f"--user-data-dir={PROFILE.resolve()}", "--no-first-run",
                    "--new-window", f"{SITE}/c/discover"], check=False)


if __name__ == "__main__":
    if sys.argv[1:] == ["login"]:
        login()
    else:
        print(__doc__)
