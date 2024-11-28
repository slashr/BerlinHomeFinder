import asyncio
import aiohttp
from playwright.async_api import async_playwright
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
    return ("under construction")

async def scan_degewo(session):
    return ("under construction")

async def scan_howoge(session):
    return ("under construction")

async def scan_gewobag():
    listings = []
    logging.info("Starting Gewobag scan with direct URL")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # For debugging
        context = await browser.new_context()
        
        # Set the consent cookie before navigating
        cookie = {
            'name': 'borlabs-cookie',
            'value': 'essential',
            'domain': 'www.gewobag.de',
            'path': '/',
            'httpOnly': False,
            'secure': True,
            'sameSite': 'Lax',
        }
        await context.add_cookies([cookie])

        page = await context.new_page()

        # Construct the URL with the desired filters
        url = (
            "https://www.gewobag.de/fuer-mieter-und-mietinteressenten/mietangebote/"
            "?objekttyp%5B%5D=wohnung"
            "&gesamtflaeche_von=62"
            "&zimmer_von=2.5"
        )

        # Navigate to the URL
        await page.goto(url)

        # Handle the cookie consent overlay
        try:
            # Wait for the accept button to appear
            await page.wait_for_selector('a._brlbs-btn-accept-all[data-cookie-accept-all]', timeout=5000)
            # Click the accept button
            await page.click('a._brlbs-btn-accept-all[data-cookie-accept-all]')
            logging.info("Clicked on cookie consent accept button.")
            # Wait for the overlay to disappear
            await page.wait_for_selector('div._brlbs-cookie-banner', state='detached', timeout=5000)
        except Exception as e:
            logging.info(f"Cookie consent accept button not found or already accepted: {e}")
            
        # Wait for the results to load
        try:
            await page.wait_for_selector('div.rental-item', state='visible', timeout=10000)
            logging.info("Rental items loaded.")
        except Exception as e:
            logging.error(f"Error waiting for rental items: {e}")
            # Take a screenshot for debugging
            await page.screenshot(path='gewobag_error.png')
            await browser.close()
            return listings

        # Proceed with listing extraction as before
        # ...

        await browser.close()

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
    return ("under construction")

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
            scan_gewobag(),
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

