#!/usr/bin/env python3
"""
Apartment-scanner with single-run lock to avoid Playwright fork storms.
"""

import asyncio
import os
import pickle
import logging
from contextlib import asynccontextmanager
from datetime import datetime

import aiocron
import aiohttp
from aiohttp import ClientSession
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from telegram import Bot
from telegram.constants import ParseMode


# ──────────────────────────────  CONFIG  ────────────────────────────── #

CRON_SCHEDULE = "*/1 * * * *"          # every minute
MIN_ROOMS     = 2.5                    # Gewobag filter already uses 2.5 rooms ≥62 m²
STATE_FILE    = "/state/notified_listings.pkl"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_USER_ID   = os.getenv("TELEGRAM_USER_ID")

HEADERS = {
    "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0"
}

# ────────────────────────────  LOGGING  ─────────────────────────────── #

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("apartment_scanner.log"), logging.StreamHandler()],
)

logging.info("Apartment scanner starting…")

# ────────────────────────────  STATE  ──────────────────────────────── #

def load_state() -> set[str]:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "rb") as f:
            return pickle.load(f)
    return set()

def save_state(s: set[str]) -> None:
    with open(STATE_FILE, "wb") as f:
        pickle.dump(s, f)

notified_listings: set[str] = load_state()

# ───────────────────────  UTILITIES / HELPERS  ─────────────────────── #

bot = Bot(token=TELEGRAM_BOT_TOKEN)
JOB_LOCK = asyncio.Lock()            # <── prevents overlapping runs


async def fetch(session: ClientSession, url: str, params: dict | None = None) -> str:
    try:
        async with session.get(url, params=params, headers=HEADERS, timeout=10) as r:
            return await r.text()
    except asyncio.TimeoutError:
        logging.warning("Timeout while fetching %s", url)
    except Exception as exc:
        logging.error("Error fetching %s: %s", url, exc)
    return ""


@asynccontextmanager
async def launch_browser(playwright):
    """Thin wrapper that always cleans up the browser process."""
    browser = await playwright.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    try:
        yield browser
    finally:
        await browser.close()


# ──────────────────────  SCANNERS (ONE PER SITE)  ───────────────────── #

async def scan_gewobag() -> list[dict]:
    listings: list[dict] = []
    logging.info("[Gewobag] scan start")

    try:
        async with async_playwright() as p:
            async with launch_browser(p) as browser:
                context = await browser.new_context()
                page    = await context.new_page()

                page.on("console", lambda msg: logging.debug("[Gewobag console] %s", msg.text))

                url = (
                    "https://www.gewobag.de/fuer-mieter-und-mietinteressenten/mietangebote/"
                    "?objekttyp%5B%5D=wohnung"
                    "&gesamtflaeche_von=62"
                    "&zimmer_von=2.5"
                )
                await page.goto(url)

                # Accept cookies if banner visible
                try:
                    await page.wait_for_selector(
                        "a._brlbs-btn-accept-all[data-cookie-accept-all]",
                        timeout=10_000,
                    )
                    await page.click("a._brlbs-btn-accept-all[data-cookie-accept-all]")
                    await page.wait_for_selector("#BorlabsCookieBox", state="detached")
                except Exception:
                    pass

                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(1_500)

                content = await page.content()
                soup    = BeautifulSoup(content, "html.parser")
                for item in soup.select("article.angebot-big-box"):
                    try:
                        listing_id = item.get("id")
                        if not listing_id:
                            continue

                        title   = item.select_one("h3.angebot-title").get_text(strip=True)
                        link    = item.select_one("a.read-more-link")["href"]
                        if not link.startswith("http"):
                            link = "https://www.gewobag.de" + link
                        address = item.select_one("address").get_text(strip=True)

                        area_tr   = item.select_one("tr.angebot-area td").get_text(strip=True)
                        rooms_txt, sqm_txt = [x.strip() for x in area_tr.split("|")]
                        rooms = float(rooms_txt.split(" ")[0].replace(",", "."))
                        sqm   = float(sqm_txt.replace("m²", "").replace(",", "."))

                        rent_txt = (
                            item.select_one("tr.angebot-kosten td")
                            .get_text(strip=True)
                            .replace("ab", "")
                            .replace("€", "")
                            .strip()
                        )

                        listings.append(
                            {
                                "id": f"gewobag_{listing_id}",
                                "rooms": rooms,
                                "sqm": sqm,
                                "rent": rent_txt,
                                "title": title,
                                "address": address,
                                "link": link,
                            }
                        )
                    except Exception as exc:
                        logging.debug("Gewobag parse error: %s", exc, exc_info=True)
                await context.close()
    except Exception as exc:
        logging.error("[Gewobag] fatal error: %s", exc, exc_info=True)

    logging.info("[Gewobag] %d listings found", len(listings))
    return listings


async def scan_wbm(session: ClientSession) -> list[dict]:
    url = "https://www.wbm.de/wohnungen-berlin/angebote/"
    html = await fetch(session, url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    listings: list[dict] = []
    for div in soup.select("div.row.openimmo-search-list-item"):
        try:
            listing_id = div["data-uid"]
            rooms = float(
                div.select_one("div.main-property-rooms").get_text(strip=True).replace(",", ".")
            )
            sqm = float(
                div.select_one("div.main-property-size")
                .get_text(strip=True)
                .replace(",", ".")
                .replace("m²", "")
            )
            if rooms < MIN_ROOMS or sqm < 62:
                continue
            link = div.find("a", title="Details")["href"]
            if not link.startswith("http"):
                link = "https://www.wbm.de" + link
            listings.append(
                {
                    "id": f"wbm_{listing_id}",
                    "rooms": rooms,
                    "sqm": sqm,
                    "link": link,
                }
            )
        except Exception as exc:
            logging.debug("WBM parse error: %s", exc, exc_info=True)
    logging.info("[WBM] %d listings found", len(listings))
    return listings


async def scan_inberlinwohnen(session: ClientSession) -> list[dict]:
    url   = "https://inberlinwohnen.de/wohnungsfinder/"
    html  = await fetch(session, url)
    if not html:
        return []
    soup  = BeautifulSoup(html, "html.parser")
    ul    = soup.find("ul", id="_tb_relevant_results")
    if not ul:
        return []

    listings: list[dict] = []
    for li in ul.select("li.tb-merkflat"):
        try:
            listing_id = li["id"]
            strongs = li.find_all("strong")
            if len(strongs) < 3:
                continue
            rooms = float(strongs[0].text.replace(",", "."))
            sqm   = float(strongs[1].text.replace(",", "."))
            rent_txt = strongs[2].text
            rent_val = float(
                rent_txt.replace("€", "").replace("ab", "").replace(".", "").replace(",", ".")
            )
            if rooms < 3 or rent_val > 1400:
                continue

            link = li.find("a", title=lambda t: t and "detailierte" in t)["href"]
            if not link.startswith("http"):
                link = "https://inberlinwohnen.de" + link

            listings.append(
                {
                    "id": f"inberlinwohnen_{listing_id}",
                    "rooms": rooms,
                    "sqm": sqm,
                    "rent": rent_txt,
                    "title": li.find("h3").get_text(strip=True),
                    "address": link,  # address not critical here
                    "link": link,
                }
            )
        except Exception:
            logging.debug("inberlinwohnen parse error", exc_info=True)
    logging.info("[inberlinwohnen] %d listings found", len(listings))
    return listings


# Place-holders for unimplemented sources
async def scan_gesobau(*_):    return []
async def scan_degewo(*_):     return []
async def scan_howoge(*_):     return []
async def scan_stadtundland(*_): return []


# ────────────────────────  NOTIFICATIONS  ─────────────────────────── #

async def send_notifications(listings: list[dict]) -> None:
    global notified_listings
    sent_any = False

    for listing in listings:
        if listing["id"] in notified_listings:
            continue
        msg = (
            "🏠 *New Apartment Available!*  \n"
            f"🛏 {listing['rooms']} rooms — {listing['sqm']} m²  \n"
            f"🔗 [Open listing]({listing['link']})"
        )
        try:
            await bot.send_message(
                chat_id=TELEGRAM_USER_ID,
                text=msg,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
            notified_listings.add(listing["id"])
            sent_any = True
        except Exception as exc:
            logging.error("Telegram error: %s", exc)

    if sent_any:
        save_state(notified_listings)
        logging.info("State saved with %d notified listings", len(notified_listings))


# ─────────────────────────  MAIN JOB  ─────────────────────────────── #

async def job() -> None:
    async with ClientSession() as session:
        tasks = [
            scan_gewobag(),                  # Playwright
            scan_wbm(session),               # pure HTTP
            scan_inberlinwohnen(session),    # pure HTTP
            # scan_gesobau(session),
            # scan_degewo(session),
            # scan_howoge(session),
            # scan_stadtundland(session),
        ]
        all_results: list[list[dict]] = await asyncio.gather(*tasks)
        flat: list[dict] = [item for sub in all_results for item in sub]
        await send_notifications(flat)
        logging.info("Job finished at %s with %d total listings",
                     datetime.utcnow().isoformat(timespec='seconds'), len(flat))


# Wrapper skips a run if the previous one is still active
async def job_wrapper():
    if JOB_LOCK.locked():
        logging.warning("Previous scan still running → skip this tick.")
        return
    async with JOB_LOCK:
        await job()


# ─────────────────────────   SCHEDULER   ──────────────────────────── #

aiocron.crontab(CRON_SCHEDULE, func=job_wrapper, start=True)
logging.info("Cron %s registered, entering event loop", CRON_SCHEDULE)
asyncio.get_event_loop().run_forever()

