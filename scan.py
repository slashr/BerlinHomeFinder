#!/usr/bin/env python3
"""
Apartment-scanner  –  cron-driven Telegram notifier
--------------------------------------------------

• Runs every minute via aiocron, but skips a tick if the previous run is still
  executing (JOB_LOCK).  
• Keeps exactly one Playwright-Chromium instance and one aiohttp session alive
  for the whole program lifetime – fast and avoids fork-storms.  
• Persists already-notified listing IDs to STATE_FILE; if the file-system is
  read-only, state stays in memory and a warning is logged.  
• Python 3.8-3.12, Playwright ≥ 1.30.

Environment variables required
==============================
TELEGRAM_BOT_TOKEN   Telegram bot token
TELEGRAM_USER_ID     Your chat ID
STATE_FILE           (optional) where to store seen-IDs, default ./notified.pkl
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import pickle
import signal
from datetime import datetime, timezone
from typing import Any, Dict, List, TypedDict

import aiohttp
import aiocron
from aiohttp import ClientSession
from bs4 import BeautifulSoup
from playwright.async_api import Browser, Playwright, async_playwright
from telegram import Bot
from telegram.constants import ParseMode

# ───────────────────────────  CONFIG  ───────────────────────────── #

CRON_SCHEDULE = "*/2 * * * *"    # every two minutes
MIN_ROOMS = 2.5
MIN_SQM = 62
MAX_RENT_INBERLIN = 1600         # €

STATE_FILE = os.getenv("STATE_FILE", "./notified.pkl")

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_USER_ID")
if not TG_TOKEN or not TG_CHAT:
    raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_USER_ID env vars")

HEADERS = {
    "User-Agent":
        "Mozilla/5.0 (X11; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0"
}

GEWOBAG_TIMEOUT = 15_000  # ms

# ───────────────────────────  LOGGING  ──────────────────────────── #

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)
log.info("Scanner booting (state file: %s)", STATE_FILE)

# ────────────────────────────  STATE  ───────────────────────────── #

def load_state() -> set[str]:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "rb") as f:
                return pickle.load(f)
        except OSError as exc:
            log.warning("Cannot read state – starting fresh (%s)", exc)
    return set()

def save_state(s: set[str]) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
        with open(STATE_FILE, "wb") as f:
            pickle.dump(s, f)
        log.info("State saved (%d IDs)", len(s))
    except OSError as exc:
        log.warning("State NOT saved (%s)", exc)

notified: set[str] = load_state()

# ─────────────────────  GLOBAL SINGLETONS  ───────────────────────── #

_PLAYWRIGHT: Playwright | None = None
_BROWSER: Browser | None = None
_SESSION: ClientSession | None = None
INIT_LOCK = asyncio.Lock()
JOB_LOCK = asyncio.Lock()

async def ensure_browser() -> Browser:
    global _PLAYWRIGHT, _BROWSER
    async with INIT_LOCK:
        if _BROWSER is None:
            _PLAYWRIGHT = await async_playwright().start()
            _BROWSER = await _PLAYWRIGHT.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-setuid-sandbox"],
            )
            log.info("Chromium launched (singleton)")
    return _BROWSER

async def ensure_session() -> ClientSession:
    global _SESSION
    if _SESSION is None:
        _SESSION = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=20, ttl_dns_cache=300)
        )
    return _SESSION

async def shutdown(*_):
    log.info("Graceful shutdown …")
    if _SESSION and not _SESSION.closed:
        await _SESSION.close()
    if _BROWSER and _BROWSER.is_connected():
        await _BROWSER.close()
    if _PLAYWRIGHT:
        await _PLAYWRIGHT.stop()
    asyncio.get_running_loop().stop()

for sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(sig, lambda *_: asyncio.create_task(shutdown()))

# ───────────────────────────  HELPERS  ──────────────────────────── #

async def fetch(url: str, *, params: Dict[str, Any] | None = None, timeout: int = 12) -> str:
    session = await ensure_session()
    try:
        async with session.get(url, params=params, headers=HEADERS, timeout=timeout) as r:
            r.raise_for_status()
            return await r.text()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        log.warning("Fetch error %s → %s", url, exc)
        return ""

class Listing(TypedDict):
    id: str
    rooms: float
    sqm: float
    link: str
    rent: str | None
    title: str | None
    address: str | None
    provider: str

# ──────────────────────────  SCANNERS  ───────────────────────────── #

async def scan_gewobag() -> List[Listing]:
    listings: List[Listing] = []
    log.info("[Gewobag] start")
    try:
        browser = await ensure_browser()
        async with (await browser.new_context()) as ctx:
            page = await ctx.new_page()
            url = ("https://www.gewobag.de/fuer-mietinteressentinnen/mietangebote/?bezirke%5B%5D=friedrichshain-kreuzberg&bezirke%5B%5D=friedrichshain-kreuzberg-friedrichshain&bezirke%5B%5D=friedrichshain-kreuzberg-kreuzberg&bezirke%5B%5D=mitte&bezirke%5B%5D=mitte-gesundbrunnen&bezirke%5B%5D=mitte-moabit&bezirke%5B%5D=mitte-wedding&bezirke%5B%5D=pankow-pankow&bezirke%5B%5D=pankow-prenzlauer-berg&bezirke%5B%5D=reinickendorf-reinickendorf&objekttyp%5B%5D=wohnung&gesamtmiete_von=&gesamtmiete_bis=&gesamtflaeche_von=60&gesamtflaeche_bis=&zimmer_von=3&zimmer_bis=&sort-by=")
            for attempt in range(3):
                try:
                    await page.goto(
                        url,
                        timeout=GEWOBAG_TIMEOUT,
                        wait_until="networkidle",
                    )
                    break
                except Exception as exc:
                    log.warning(
                        "Gewobag navigation failed (%d/3): %s", attempt + 1, exc
                    )
                    if attempt == 2:
                        log.error("Gewobag navigation failed after retries: %s", exc)
                        return []
                    await asyncio.sleep(attempt + 1)
            try:
                await page.wait_for_selector(
                    "a._brlbs-btn-accept-all[data-cookie-accept-all]", timeout=5000
                )
                await page.click("a._brlbs-btn-accept-all[data-cookie-accept-all]")
            except Exception:
                pass
            await page.wait_for_load_state("networkidle")
            soup = BeautifulSoup(await page.content(), "lxml")
            for art in soup.select("article.angebot-big-box"):
                try:
                    lid = art.get("id")
                    if not lid:
                        continue
                    area = art.select_one("tr.angebot-area td").text
                    rooms_txt, sqm_txt = [s.strip() for s in area.split("|")]
                    rooms = float(rooms_txt.split()[0].replace(",", "."))
                    sqm = float(sqm_txt.replace("m²", "").replace(",", "."))
                    if rooms < MIN_ROOMS or sqm < MIN_SQM:
                        continue
                    link = art.select_one("a.read-more-link")["href"]
                    if not link.startswith("http"):
                        from urllib.parse import urljoin
                        link = urljoin("https://www.gewobag.de", link)
                    listings.append(
                        Listing(
                            id=f"gewobag_{lid}",
                            rooms=rooms,
                            sqm=sqm,
                            link=link,
                            rent=None,
                            title=art.select_one("h3.angebot-title").get_text(strip=True),
                            address=art.select_one("address").get_text(strip=True),
                            provider="Gewobag",
                        )
                    )
                except Exception:
                    log.debug("Gewobag parse error", exc_info=True)
    except Exception as exc:
        log.error("Gewobag fatal: %s", exc, exc_info=True)
    log.info("[Gewobag] %d listings", len(listings))
    return listings

async def scan_wbm() -> List[Listing]:
    html = await fetch("https://www.wbm.de/wohnungen-berlin/angebote/")
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    listings: List[Listing] = []
    for div in soup.select("div.row.openimmo-search-list-item"):
        try:
            rooms = float(div.select_one("div.main-property-rooms").text.strip().replace(",", "."))
            sqm = float(div.select_one("div.main-property-size").text
                        .replace("m²", "").replace(",", ".").strip())
            if rooms < MIN_ROOMS or sqm < MIN_SQM:
                continue
            lid = div["data-uid"]
            link = div.find("a", title="Details")["href"]
            if not link.startswith("http"):
                link = "https://www.wbm.de" + link
            listings.append(
                Listing(
                    id=f"wbm_{lid}",
                    rooms=rooms,
                    sqm=sqm,
                    link=link,
                    rent=None,
                    title=None,
                    address=None,
                    provider="WBM",
                )
            )
        except Exception:
            log.debug("WBM parse error", exc_info=True)
    log.info("[WBM] %d listings", len(listings))
    return listings

async def scan_inberlinwohnen() -> List[Listing]:
    html = await fetch("https://inberlinwohnen.de/wohnungsfinder/")
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    ul = soup.find("ul", id="_tb_relevant_results")
    if not ul:
        return []
    listings: List[Listing] = []
    for li in ul.select("li.tb-merkflat"):
        try:
            lid = li["id"]
            st = li.find_all("strong")
            if len(st) < 3:
                continue
            rooms = float(st[0].text.replace(",", "."))
            sqm = float(st[1].text.replace(",", "."))
            rent_val = float(st[2].text.replace("€", "").replace("ab", "")
                             .replace(".", "").replace(",", "."))
            if rooms < 3 or rent_val > MAX_RENT_INBERLIN:
                continue
            link = li.find("a", title=lambda t: t and "detailierte" in t)["href"]
            if not link.startswith("http"):
                link = "https://inberlinwohnen.de" + link
            if "wbm.de" in link or "gewobag" in link:
                continue  # skip WBM and Gewobag entries to avoid duplicates
            listings.append(
                Listing(
                    id=f"inberlinwohnen_{lid}",
                    rooms=rooms,
                    sqm=sqm,
                    link=link,
                    rent=f"{rent_val:.0f}",
                    title=li.find("h3").get_text(strip=True),
                    address=None,
                    provider="inBerlinWohnen",
                )
            )
        except Exception:
            log.debug("inBerlin parse error", exc_info=True)
    log.info("[inberlinwohnen] %d listings", len(listings))
    return listings

# stubs – add real scrapers later
async def scan_gesobau() -> List[Listing]:     return []
async def scan_degewo() -> List[Listing]:      return []
async def scan_howoge() -> List[Listing]:      return []
async def scan_stadtundland() -> List[Listing]:return []

SCANNERS = [
    scan_gewobag,
    scan_wbm,
    scan_inberlinwohnen,
    # scan_gesobau,
    # scan_degewo,
    # scan_howoge,
    # scan_stadtundland,
]

# ─────────────────────────  TELEGRAM  ────────────────────────────── #

bot = Bot(token=TG_TOKEN)

def _format_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _format_rent(rent: str | None) -> str | None:
    if not rent:
        return None
    rent_text = rent.strip()
    if not rent_text:
        return None
    if "€" not in rent_text and "EUR" not in rent_text and not rent_text.endswith("€"):
        rent_text = f"{rent_text} €"
    return rent_text


def build_message(listing: Listing) -> str:
    snippet_src = (
        listing.get("title")
        or listing.get("address")
        or listing["link"].rstrip("/").split("/")[-1]
    )
    snippet = html.escape(snippet_src[:80])
    provider = html.escape(listing["provider"])
    lines = [f"🏠 <b>{provider}</b>: {snippet}"]

    location = listing.get("address")
    if location:
        lines.append(f"📍 {html.escape(location)}")

    rooms = _format_number(listing["rooms"])
    sqm = _format_number(listing["sqm"])
    lines.append(f"🛏 {rooms} rooms – {sqm} m²")

    rent_text = _format_rent(listing.get("rent"))
    if rent_text:
        lines.append(f"💶 {html.escape(rent_text)}")

    link = html.escape(listing["link"], quote=True)
    lines.append(f"🔗 <a href=\"{link}\">Listing</a>")
    return "\n".join(lines)


async def send_notifications(listings: List[Listing]) -> None:
    fresh = [l for l in listings if l["id"] not in notified]
    if not fresh:
        return
    for l in fresh:          # ← sequential loop
        await bot.send_message(
            chat_id=TG_CHAT,
            text=build_message(l),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    notified.update(l["id"] for l in fresh)
    save_state(notified)
    log.info("Sent %d Telegram messages", len(fresh))


# ─────────────────────────  MAIN JOB  ────────────────────────────── #

async def job() -> None:
    if JOB_LOCK.locked():
        log.warning("Previous run still active — skipping")
        return
    async with JOB_LOCK:
        # decide dynamically which scanners to call
        scanners = [
            scan_gewobag,
            scan_wbm,
            scan_inberlinwohnen
        ]

        tasks = [asyncio.create_task(scan()) for scan in scanners]
        flat: List[Listing] = []
        for coro in asyncio.as_completed(tasks):
            listings = await coro
            flat.extend(listings)
            await send_notifications(listings)
        log.info(
            "Run finished at %s (%d listings total)",
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            len(flat),
        )


# schedule cron task
aiocron.crontab(CRON_SCHEDULE, func=lambda: asyncio.create_task(job()), start=True)
log.info("Cron %s registered – entering loop", CRON_SCHEDULE)

if __name__ == "__main__":
    asyncio.get_event_loop().run_forever()

