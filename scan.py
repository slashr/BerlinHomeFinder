import asyncio
import aiohttp
from aiohttp import ClientSession
from bs4 import BeautifulSoup
from telegram import Bot
from telegram.constants import ParseMode
import aiocron
import os
import pickle
import logging

# Telegram Bot Token and Chat ID from environment variables
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_USER_ID = os.getenv('TELEGRAM_USER_ID')

# Custom headers to mimic a regular browser
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:107.0) Gecko/20100101 Firefox/107.0'
}


bot = Bot(token=TELEGRAM_BOT_TOKEN)

# State management
STATE_FILE = 'notified_listings.pkl'

# Configure logging
logging.basicConfig(
    level=logging.INFO,  # Set to logging.DEBUG for more verbose output
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("apartment_scanner.log"),  # Log to a file
        logging.StreamHandler()  # Also log to console
    ]
)

logging.info("Script has started running.")

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'rb') as f:
            return pickle.load(f)
    else:
        return set()

def save_state(notified_listings):
    with open(STATE_FILE, 'wb') as f:
        pickle.dump(notified_listings, f)

notified_listings = load_state()

# Fetch function with error handling
async def fetch(session, url, params=None):
    try:
        async with session.get(url, params=params, headers=HEADERS, timeout=10) as response:
            return await response.text()
    except asyncio.TimeoutError:
        logging.info(f"Timeout while fetching {url}")
        return ''
    except Exception as e:
        logging.error(f"Error fetching {url}: {e}")
        return ''

# Scanning functions as defined above...
async def scan_gesobau(session):
    url = "https://www.gesobau.de/mieten/wohnungssuche/"
    params = {
        "size": 10,
        "page": 1,
        "property_type_id": 1,
        "categories[]": 1,
        "rooms_from": 2.5,
        "qm_from": 62,
    }
    html = await fetch(session, url, params=params)
    soup = BeautifulSoup(html, "lxml")
    headers = HEADERS

    listings = []
    for listing in soup.find_all("div", class_="col-xs-12 search-list-entry"):
        try:
            rooms = float(listing.find("div", class_="rooms").text.strip().split()[0].replace(',', '.'))
            sqm = float(listing.find("div", class_="area").text.strip().split()[0].replace(',', '.'))
            link = listing.find("a", class_="detail-link")['href']
            listing_id = link.split('/')[-1]
            if rooms >= 2.5 and sqm >= 62:
                listings.append({
                    "id": f"gesobau_{listing_id}",
                    "rooms": rooms,
                    "sqm": sqm,
                    "link": f"https://immosuche.gesobau.de{link}",
                })
        except Exception as e:
            logging.error(f"Error parsing Gesobau listing: {e}")
    logging.info(f"Found {len(listings)} listings on Gesobau")
    return listings

async def scan_degewo(session):
    url = "https://immosuche.degewo.de/de/search"
    params = {
        "size": 10,
        "page": 1,
        "property_type_id": 1,
        "categories[]": 1,
        "rooms_from": 2.5,
        "qm_from": 62,
    }
    html = await fetch(session, url, params=params)
    soup = BeautifulSoup(html, "lxml")

    listings = []
    for listing in soup.find_all("div", class_="col-xs-12 search-list-entry"):
        try:
            rooms = float(listing.find("div", class_="rooms").text.strip().split()[0].replace(',', '.'))
            sqm = float(listing.find("div", class_="area").text.strip().split()[0].replace(',', '.'))
            link = listing.find("a", class_="detail-link")['href']
            listing_id = link.split('/')[-1]
            if rooms >= 2.5 and sqm >= 62:
                listings.append({
                    "id": f"degewo_{listing_id}",
                    "rooms": rooms,
                    "sqm": sqm,
                    "link": f"https://immosuche.degewo.de{link}",
                })
        except Exception as e:
            logging.error(f"Error parsing Degewo listing: {e}")
    logging.info(f"Found {len(listings)} listings on Degewo")
    return listings

async def scan_howoge(session):
    url = "https://www.howoge.de/wohnungen-gewerbe/wohnungssuche.html"
    params = {
        "rooms_from": 2.5,
        "sqm_from": 62,
    }
    html = await fetch(session, url, params=params)
    soup = BeautifulSoup(html, "lxml")
    headers = HEADERS

    listings = []
    for listing in soup.find_all("div", class_="result-item"):
        try:
            rooms = float(listing.find("div", class_="rooms").text.strip().split()[0].replace(',', '.'))
            sqm = float(listing.find("div", class_="area").text.strip().split()[0].replace(',', '.'))
            link = listing.find("a", class_="details-button")['href']
            listing_id = link.split('/')[-1]
            if rooms >= 2.5 and sqm >= 62:
                listings.append({
                    "id": f"howoge_{listing_id}",
                    "rooms": rooms,
                    "sqm": sqm,
                    "link": f"https://www.howoge.de{link}",
                })
        except Exception as e:
            logging.error(f"Error parsing HOWOGE listing: {e}")
    logging.info(f"Found {len(listings)} listings on Howoge")
    return listings

async def scan_gewobag(session):
    url = "https://www.gewobag.de/fuer-mieter-und-mietinteressenten/mietangebote/?objekttyp%5B%5D=wohnung"
    params = {
        "gesamtmiete_bis": 1500,
        "zimmer_von": 2.5,
        "gesamtflaeche_von": 62,
    }
    html = await fetch(session, url, params=params)
    soup = BeautifulSoup(html, "lxml")
    headers = HEADERS

    listings = []
    for listing in soup.find_all("div", class_="col-xs-12 search-list-entry"):
        try:
            rooms = float(listing.find("div", class_="rooms").text.strip().split()[0].replace(',', '.'))
            sqm = float(listing.find("div", class_="area").text.strip().split()[0].replace(',', '.'))
            link = listing.find("a", class_="detail-link")['href']
            listing_id = link.split('/')[-1]
            if rooms >= 2.5 and sqm >= 62:
                listings.append({
                    "id": f"gewobag_{listing_id}",
                    "rooms": rooms,
                    "sqm": sqm,
                    "link": f"https://immosuche.gewobag.de{link}",
                })
        except Exception as e:
            logging.error(f"Error parsing Gewobag listing: {e}")
    logging.info(f"Found {len(listings)} listings on Gewobag")
    return listings

async def scan_wbm(session):
    url = "https://www.wbm.de/wohnungen-berlin/angebote/"
    html = await fetch(session, url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")

    listings = []
    for listing in soup.find_all("div", class_="row openimmo-search-list-item"):
        try:
            # Extract the listing ID
            listing_id = listing.get('data-uid')
            if not listing_id:
                continue

            # Extract number of rooms
            rooms_div = listing.find("div", class_="main-property-value main-property-rooms")
            rooms_text = rooms_div.get_text(strip=True)
            rooms = float(rooms_text.replace(',', '.'))

            # Extract size in sqm
            sqm_div = listing.find("div", class_="main-property-value main-property-size")
            sqm_text = sqm_div.get_text(strip=True)
            sqm = float(sqm_text.replace(',', '.').replace('m²', '').strip())

            # Extract the link
            details_link = listing.find("a", title="Details")
            if not details_link:
                continue
            link = details_link['href']
            if not link.startswith('http'):
                link = "https://www.wbm.de" + link

            # Check criteria
            if rooms >= 2 and sqm >= 62:
                listings.append({
                    "id": f"wbm_{listing_id}",
                    "rooms": rooms,
                    "sqm": sqm,
                    "link": link,
                })
        except Exception as e:
            logging.error(f"Error parsing WBM listing: {e}", exc_info=True)
    logging.info(f"Found {len(listings)} listings on WBM")
    return listings

async def scan_stadtundland(session):
    url = "https://immosuche.stadtundland.de/de/search"
    params = {
        "size": 10,
        "page": 1,
        "property_type_id": 1,
        "categories[]": 1,
        "rooms_from": 2.5,
        "qm_from": 62,
    }
    html = await fetch(session, url, params=params)
    soup = BeautifulSoup(html, "lxml")
    headers = HEADERS

    listings = []
    for listing in soup.find_all("div", class_="col-xs-12 search-list-entry"):
        try:
            rooms = float(listing.find("div", class_="rooms").text.strip().split()[0].replace(',', '.'))
            sqm = float(listing.find("div", class_="area").text.strip().split()[0].replace(',', '.'))
            link = listing.find("a", class_="detail-link")['href']
            listing_id = link.split('/')[-1]
            if rooms >= 2.5 and sqm >= 62:
                listings.append({
                    "id": f"stadtundland_{listing_id}",
                    "rooms": rooms,
                    "sqm": sqm,
                    "link": f"https://immosuche.stadtundland.de{link}",
                })
        except Exception as e:
            logging.error(f"Error parsing Stadt und Land listing: {e}")
    logging.info(f"Found {len(listings)} listings on StadtUndLand")
    return listings


# Send notifications
async def send_notifications(listings):
    new_notifications = False
    for listing in listings:
        if listing['id'] not in notified_listings:
            message = (
                f"🏠 *New Apartment Available!*\n"
                f"🛏 Rooms: {listing['rooms']}\n"
                f"📏 Size: {listing['sqm']} m²\n"
                f"🔗 [View Listing]({listing['link']})"
            )
            try:
                await bot.send_message(
                    chat_id=TELEGRAM_USER_ID,
                    text=message,
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True
                )
                logging.info(f"Notification sent for listing {listing['id']}")
            except Exception as e:
                logging.error(f"Error sending notification: {e}")
            notified_listings.add(listing['id'])
            new_notifications = True
    if new_notifications:
        save_state(notified_listings)

# Job function
async def job():
    async with ClientSession() as session:
        all_listings = []
        tasks = [
            #scan_degewo(session),
            #scan_gesobau(session),
            #scan_howoge(session),
            #scan_gewobag(session),
            scan_wbm(session),
            #scan_stadtundland(session),
        ]
        results = await asyncio.gather(*tasks)
        for listings in results:
            all_listings.extend(listings)

        # Send notifications
        await send_notifications(all_listings)

# Schedule the job to run every minute using aiocron
aiocron.crontab('*/1 * * * *', func=job, start=True)

# Run the event loop
asyncio.get_event_loop().run_forever()

