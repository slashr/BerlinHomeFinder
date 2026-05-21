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
import hashlib
import html
import logging
import math
import os
import pickle
import re
import signal
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, TypedDict
from urllib.parse import parse_qs, urljoin, urlparse

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
MAX_RENT = 1600                  # €

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
DEGEWO_MAX_PAGES = 10

GESOBAU_URL = (
    "https://www.gesobau.de/mieten/wohnungssuche/"
    "?resultsPerPage=10000"
    "&resultsPage=0"
    "&resultAsJSON=1"
    "&befilter%5B0%5D=nutzungsart_stringS%3AWOHNEN"
    "&befilter%5B1%5D=kanal_stringM%3A%28%22Service%22+OR+%22Senioren+Kachel%22+"
    "OR+%22Bestand%22+OR+%22Studierende%22+OR+%22Neubau+Kachel%22%29"
)
HOWOGE_URL = "https://www.howoge.de/?type=999&tx_howrealestate_json_list%5Baction%5D=immoList"
DEGEWO_URL = "https://www.degewo.de/immosuche"
INBERLIN_DUPLICATE_DOMAINS = ("wbm.de", "degewo.de", "gesobau.de", "howoge.de")
LOCATION_DISTANCE_KM = 2.0
PREFERRED_LOCATION_TERMS = {
    "wedding",
    "mitte",
    "pankow",
    "prenzlauer berg",
    "gesundbrunnen",
    "moabit",
    "weissensee",
    "niederschonhausen",
    "heinersdorf",
    "wilhelmsruh",
    "franzosisch buchholz",
}
PREFERRED_ZIPS = {
    "10115", "10117", "10119", "10178", "10179",
    "10405", "10407", "10409", "10435", "10437", "10439",
    "10551", "10553", "10555", "10557", "10559",
    "13086", "13088", "13089",
    "13125", "13127", "13129", "13156", "13158", "13159", "13187", "13189",
    "13347", "13349", "13351", "13353", "13355", "13357", "13359",
}
LOCATION_REFERENCE_POINTS = (
    (52.5426, 13.3662),  # Wedding station
    (52.5486, 13.3889),  # Gesundbrunnen / Humboldthain edge
    (52.5200, 13.4050),  # Mitte / Alexanderplatz
    (52.5385, 13.4244),  # Prenzlauer Berg
    (52.5667, 13.4127),  # Pankow
)

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


async def fetch_json(
    url: str,
    *,
    data: Dict[str, Any] | None = None,
    params: Dict[str, Any] | None = None,
    timeout: int = 12,
) -> Any | None:
    session = await ensure_session()
    headers = {**HEADERS, "X-Requested-With": "XMLHttpRequest"}
    try:
        if data is None:
            async with session.get(
                url, params=params, headers=headers, timeout=timeout
            ) as r:
                r.raise_for_status()
                return await r.json(content_type=None)
        async with session.post(url, data=data, headers=headers, timeout=timeout) as r:
            r.raise_for_status()
            return await r.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        log.warning("Fetch JSON error %s → %s", url, exc)
        return None


class Listing(TypedDict):
    id: str
    rooms: float
    sqm: float
    link: str
    rent: str | None
    title: str | None
    address: str | None
    provider: str


def _parse_number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return None

    text = html.unescape(str(value)).replace("\xa0", " ").replace("\u202f", " ")
    match = re.search(r"\d+(?:[.,]\d+)*", text)
    if not match:
        return None

    number = match.group(0)
    if "," in number and "." in number:
        number = number.replace(".", "").replace(",", ".")
    elif "," in number:
        number = number.replace(",", ".")
    elif "." in number:
        parts = number.split(".")
        if len(parts) > 1 and all(len(part) == 3 for part in parts[1:]):
            number = "".join(parts)
    try:
        return float(number)
    except ValueError:
        return None


def _passes_size_filter(rooms: float | None, sqm: float | None) -> bool:
    return rooms is not None and sqm is not None and rooms >= MIN_ROOMS and sqm >= MIN_SQM


def _passes_rent_filter(value: Any) -> bool:
    # Missing rent should not hide otherwise valid listings.
    rent = _parse_number(value)
    return rent is None or rent <= MAX_RENT


def _rent_text(value: Any) -> str | None:
    rent = _parse_number(value)
    if rent is None:
        return None
    if rent.is_integer():
        return f"{int(rent):,}".replace(",", ".")
    euros, cents = f"{rent:,.2f}".split(".")
    return f"{euros.replace(',', '.')},{cents}"


def _text_or_none(node: Any) -> str | None:
    if not node:
        return None
    text = node.get_text(" ", strip=True)
    return text or None


def _normalize_location_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", html.unescape(str(value or "")))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.casefold()


def _contains_location_term(text: str, term: str) -> bool:
    return re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", text) is not None


def _extract_zip(text: str) -> str | None:
    match = re.search(r"\b1\d{4}\b", text)
    return match.group(0) if match else None


def _parse_coords(lat: Any, lng: Any) -> tuple[float, float] | None:
    parsed_lat = _parse_number(lat)
    parsed_lng = _parse_number(lng)
    if parsed_lat is None or parsed_lng is None:
        return None
    return parsed_lat, parsed_lng


def _distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1 = math.radians(a[0]), math.radians(a[1])
    lat2, lng2 = math.radians(b[0]), math.radians(b[1])
    d_lat = lat2 - lat1
    d_lng = lng2 - lng1
    h = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(d_lng / 2) ** 2
    )
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def _location_matches(
    texts: List[Any],
    coords: tuple[float, float] | None = None,
) -> bool:
    location_text = " ".join(_normalize_location_text(text) for text in texts if text)
    if location_text:
        if any(_contains_location_term(location_text, term) for term in PREFERRED_LOCATION_TERMS):
            return True
        zip_code = _extract_zip(location_text)
        if zip_code in PREFERRED_ZIPS:
            return True
    if coords is not None:
        return min(_distance_km(coords, point) for point in LOCATION_REFERENCE_POINTS) <= LOCATION_DISTANCE_KM
    return False

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
                    if not _passes_size_filter(rooms, sqm):
                        continue
                    location_texts = [
                        art.select_one("address").get_text(" ", strip=True),
                        art.select_one("h3.angebot-title").get_text(" ", strip=True),
                    ]
                    if not _location_matches(location_texts):
                        continue
                    link = art.select_one("a.read-more-link")["href"]
                    if not link.startswith("http"):
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
            area = _text_or_none(div.select_one(".area"))
            address = _text_or_none(div.select_one(".address"))
            title = _text_or_none(div.select_one("h2.imageTitle"))
            if not _location_matches([area, address, title]):
                continue
            rooms = float(div.select_one("div.main-property-rooms").text.strip().replace(",", "."))
            sqm = float(div.select_one("div.main-property-size").text
                        .replace("m²", "").replace(",", ".").strip())
            if not _passes_size_filter(rooms, sqm):
                continue
            link = div.find("a", title="Details")["href"]
            if not link.startswith("http"):
                link = "https://www.wbm.de" + link
            lid = build_wbm_listing_id(link, rooms, sqm)
            listings.append(
                Listing(
                    id=lid,
                    rooms=rooms,
                    sqm=sqm,
                    link=link,
                    rent=None,
                    title=title,
                    address=address,
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
            if rooms < 3 or rent_val > MAX_RENT:
                continue
            title = li.find("h3").get_text(strip=True)
            if not _location_matches([title]):
                continue
            link = li.find("a", title=lambda t: t and "detailierte" in t)["href"]
            if not link.startswith("http"):
                link = "https://inberlinwohnen.de" + link
            if any(domain in link for domain in INBERLIN_DUPLICATE_DOMAINS):
                continue  # skip entries covered by direct provider scanners
            listings.append(
                Listing(
                    id=f"inberlinwohnen_{lid}",
                    rooms=rooms,
                    sqm=sqm,
                    link=link,
                    rent=f"{rent_val:.0f}",
                    title=title,
                    address=None,
                    provider="inBerlinWohnen",
                )
            )
        except Exception:
            log.debug("inBerlin parse error", exc_info=True)
    log.info("[inberlinwohnen] %d listings", len(listings))
    return listings


async def scan_gesobau() -> List[Listing]:
    data = await fetch_json(GESOBAU_URL)
    if not isinstance(data, list):
        return []

    listings: List[Listing] = []
    for item in data:
        try:
            raw = item.get("raw") or {}
            location_texts = [
                item.get("title"),
                raw.get("adresse_stringS"),
                raw.get("plz_stringS"),
                raw.get("ort_stringS"),
                " ".join(raw.get("region_stringM") or []),
                " ".join(raw.get("location_stringM") or []),
            ]
            if not _location_matches(location_texts, _parse_coords(item.get("lat"), item.get("lng"))):
                continue
            rooms = _parse_number(raw.get("zimmer_intS"))
            sqm = _parse_number(raw.get("wohnflaeche_floatS"))
            if not _passes_rent_filter(raw.get("warmmiete_floatS")):
                continue
            if sqm is None or sqm < MIN_SQM:
                continue

            detail = raw.get("url") or item.get("detail") or ""
            link = urljoin("https://www.gesobau.de", detail)
            if rooms is None and link:
                rooms = await _fetch_gesobau_detail_rooms(link)
            if not _passes_size_filter(rooms, sqm):
                continue

            address_parts = [
                raw.get("adresse_stringS") or item.get("title"),
                raw.get("plz_stringS"),
                raw.get("ort_stringS"),
            ]
            address = ", ".join(str(part) for part in address_parts if part)
            title = raw.get("title") or item.get("title")
            lid = raw.get("objekt_nr_extern_stringS") or item.get("uid")
            listings.append(
                Listing(
                    id=f"gesobau_{lid}",
                    rooms=rooms,
                    sqm=sqm,
                    link=link,
                    rent=_rent_text(raw.get("warmmiete_floatS")),
                    title=title,
                    address=address or None,
                    provider="GESOBAU",
                )
            )
        except Exception:
            log.debug("GESOBAU parse error", exc_info=True)
    log.info("[GESOBAU] %d listings", len(listings))
    return listings


async def _fetch_gesobau_detail_rooms(link: str) -> float | None:
    html_text = await fetch(link)
    if not html_text:
        return None
    soup = BeautifulSoup(html_text, "lxml")
    meta = soup.select_one(".immoHero__metaData") or soup
    match = re.search(
        r"(\d+(?:[,.]\d+)?)\s*Zimmer",
        meta.get_text(" ", strip=True),
        re.IGNORECASE,
    )
    if not match:
        return None
    return _parse_number(match.group(1))


async def scan_degewo() -> List[Listing]:
    listings: List[Listing] = []
    seen_ids: set[str] = set()
    next_url: str | None = DEGEWO_URL
    seen_urls: set[str] = set()

    for _ in range(DEGEWO_MAX_PAGES):
        if not next_url or next_url in seen_urls:
            break
        seen_urls.add(next_url)
        html_text = await fetch(next_url)
        if not html_text:
            break

        soup = BeautifulSoup(html_text, "lxml")
        for listing in _parse_degewo_page(soup):
            if listing["id"] in seen_ids:
                continue
            seen_ids.add(listing["id"])
            listings.append(listing)

        current_page = _degewo_page_number(next_url)
        next_url = _next_degewo_page_url(soup, current_page)

    log.info("[degewo] %d listings", len(listings))
    return listings


def _parse_degewo_page(soup: BeautifulSoup) -> List[Listing]:
    listings: List[Listing] = []
    for card in soup.select("div.c-teaser.c-teaser--apartment"):
        try:
            facts: Dict[str, str] = {}
            for item in card.select(".c-definition-list__item"):
                # degewo renders the fact value in dt and its label in dd.
                value = _text_or_none(item.select_one("dt"))
                label = _text_or_none(item.select_one("dd"))
                if value and label:
                    facts[label] = value

            rooms = _parse_number(facts.get("Zimmer"))
            sqm = _parse_number(facts.get("m²"))
            if not _passes_size_filter(rooms, sqm):
                continue
            if not _passes_rent_filter(facts.get("Warmmiete")):
                continue

            link_node = card.select_one("h3 a[href]")
            if not link_node:
                continue
            title = _text_or_none(link_node)
            address = _text_or_none(card.select_one("h3 + p"))
            if not _location_matches([title, address]):
                continue
            link = urljoin("https://www.degewo.de", link_node["href"])
            bookmark = card.select_one("[data-openimmo-bookmark-item-uid]")
            lid = (
                bookmark.get("data-openimmo-bookmark-item-uid")
                if bookmark
                else link.rstrip("/").split("/")[-1]
            )
            listings.append(
                Listing(
                    id=f"degewo_{lid}",
                    rooms=rooms,
                    sqm=sqm,
                    link=link,
                    rent=_rent_text(facts.get("Warmmiete")),
                    title=title,
                    address=address,
                    provider="degewo",
                )
            )
        except Exception:
            log.debug("degewo parse error", exc_info=True)
    return listings


def _degewo_page_number(url: str) -> int:
    query = parse_qs(urlparse(url).query)
    value = query.get("tx_openimmo_immobilie[page]")
    if not value:
        return 1
    try:
        return int(value[0])
    except ValueError:
        return 1


def _next_degewo_page_url(soup: BeautifulSoup, current_page: int) -> str | None:
    target = current_page + 1
    for link in soup.select("a[href]"):
        url = urljoin("https://www.degewo.de", link["href"])
        if _degewo_page_number(url) == target:
            return url
    return None


async def scan_howoge() -> List[Listing]:
    data = await fetch_json(
        HOWOGE_URL,
        data={
            "tx_howrealestate_json_list[page]": 1,
            "tx_howrealestate_json_list[limit]": 500,
            "tx_howrealestate_json_list[lang]": "",
        },
    )
    if not isinstance(data, dict):
        return []

    listings: List[Listing] = []
    for item in data.get("immoobjects", []):
        try:
            coords = item.get("coordinates") or {}
            location_texts = [
                item.get("title"),
                item.get("district"),
                item.get("notice"),
            ]
            if not _location_matches(location_texts, _parse_coords(coords.get("lat"), coords.get("lng"))):
                continue
            rooms = _parse_number(item.get("rooms"))
            sqm = _parse_number(item.get("area"))
            if not _passes_size_filter(rooms, sqm):
                continue
            if not _passes_rent_filter(item.get("rent")):
                continue

            link = urljoin("https://www.howoge.de", item.get("link") or "")
            title = str(item.get("notice") or "").strip() or None
            # HOWOGE's current list payload uses title for the street address.
            address = item.get("title") or item.get("district")
            listings.append(
                Listing(
                    id=f"howoge_{item['uid']}",
                    rooms=rooms,
                    sqm=sqm,
                    link=link,
                    rent=_rent_text(item.get("rent")),
                    title=title or item.get("title"),
                    address=address,
                    provider="HOWOGE",
                )
            )
        except Exception:
            log.debug("HOWOGE parse error", exc_info=True)
    log.info("[HOWOGE] %d listings", len(listings))
    return listings


async def scan_stadtundland() -> List[Listing]:
    return []

SCANNERS = [
    scan_gewobag,
    scan_wbm,
    scan_inberlinwohnen,
    scan_gesobau,
    scan_degewo,
    scan_howoge,
    # scan_stadtundland,
]

# ─────────────────────────  TELEGRAM  ────────────────────────────── #

bot = Bot(token=TG_TOKEN)


def build_wbm_listing_id(link: str, rooms: float, sqm: float) -> str:
    """Return a stable WBM identifier independent of their rotating IDs."""
    slug = link.rstrip("/").split("/")[-1] or "listing"

    def _fmt(value: float) -> str:
        text = f"{value:.2f}".rstrip("0").rstrip(".")
        return text or "0"

    digest_source = f"{slug}|{_fmt(rooms)}|{_fmt(sqm)}"
    digest = hashlib.sha1(digest_source.encode("utf-8")).hexdigest()[:10]
    return f"wbm_{slug}_{digest}"


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
    sent = 0
    for l in fresh:          # ← sequential loop
        log.debug("Sending listing %s (%s)", l["id"], l["link"])
        try:
            await bot.send_message(
                chat_id=TG_CHAT,
                text=build_message(l),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception as exc:
            log.error(
                "Failed to send Telegram message for %s: %s",
                l["id"],
                exc,
                exc_info=True,
            )
            continue
        notified.add(l["id"])
        save_state(notified)
        sent += 1
    log.info("Sent %d Telegram messages", sent)


# ─────────────────────────  MAIN JOB  ────────────────────────────── #

async def job() -> None:
    if JOB_LOCK.locked():
        log.warning("Previous run still active — skipping")
        return
    async with JOB_LOCK:
        tasks = [asyncio.create_task(scan()) for scan in SCANNERS]
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


def main() -> None:
    aiocron.crontab(CRON_SCHEDULE, func=lambda: asyncio.create_task(job()), start=True)
    log.info("Cron %s registered – entering loop", CRON_SCHEDULE)
    asyncio.get_event_loop().run_forever()


if __name__ == "__main__":
    main()
