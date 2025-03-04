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
STATE_FILE = '/state/notified_listings.pkl'

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
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        
        page = await context.new_page()

        # Optional: Log console messages
        page.on('console', lambda msg: logging.info(f"Console: {msg.text}"))

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
            await page.wait_for_selector('a._brlbs-btn-accept-all[data-cookie-accept-all]', timeout=10000)
            # Click the accept button
            await page.click('a._brlbs-btn-accept-all[data-cookie-accept-all]')
            logging.info("Clicked on cookie consent accept button.")
            # Wait for the overlay to disappear
            await page.wait_for_selector('div#BorlabsCookieBox', state='detached', timeout=10000)
            logging.info("Cookie consent overlay has been closed.")
        except Exception as e:
            logging.info(f"Cookie consent accept button not found or already accepted: {e}")

        # Wait for the page to load
        await page.wait_for_load_state('networkidle')
        await page.wait_for_timeout(2000)  # Wait for 2 seconds

        # Wait for the results to load
        try:
            await page.wait_for_selector('article.angebot-big-box', state='attached', timeout=20000)
            logging.info("Rental items are present in the DOM.")
        except Exception as e:
            logging.error(f"Error waiting for rental items: {e}")
            # Optionally, get the page content
            content = await page.content()
            with open('gewobag_page.html', 'w', encoding='utf-8') as f:
                f.write(content)
            await browser.close()
            return listings

        # Proceed with listing extraction
        # Extract listings using BeautifulSoup
        content = await page.content()
        soup = BeautifulSoup(content, 'html.parser')

        rental_items = soup.find_all('article', class_='angebot-big-box')
        if not rental_items:
            logging.info("No rental items found in the page content.")
            await browser.close()
            return listings

        for item in rental_items:
            try:
                # Extract the listing ID
                listing_id = item.get('id')
                if not listing_id:
                    continue

                # Extract link
                link_tag = item.find('a', class_='read-more-link')
                link = link_tag['href']
                if not link.startswith('http'):
                    link = 'https://www.gewobag.de' + link

                # Extract title
                title_tag = item.find('h3', class_='angebot-title')
                title = title_tag.get_text(strip=True)

                # Extract address
                address_tag = item.find('address')
                address = address_tag.get_text(strip=True)

                # Extract rooms and size
                area_tr = item.find('tr', class_='angebot-area')
                area_td = area_tr.find('td')
                area_text = area_td.get_text(strip=True)
                rooms_text, sqm_text = area_text.split('|')
                rooms = float(rooms_text.strip().split(' ')[0].replace(',', '.'))
                sqm = float(sqm_text.strip().replace('m²', '').replace(',', '.'))

                # Extract rent
                kosten_tr = item.find('tr', class_='angebot-kosten')
                kosten_td = kosten_tr.find('td')
                rent_text = kosten_td.get_text(strip=True)
                rent = rent_text.replace('ab', '').replace('€', '').strip()

                # Add the listing to the list
                listings.append({
                    "id": f"gewobag_{listing_id}",
                    "rooms": rooms,
                    "sqm": sqm,
                    "link": link,
                    "rent": rent,
                    "title": title,
                    "address": address,
                })

            except Exception as e:
                logging.error(f"Error parsing Gewobag listing: {e}", exc_info=True)

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

async def scan_inberlinwohnen(session):
    """
    Scans the inberlinwohnen.de Wohnungsfinder page for listings.
    Extracts details from each listing (using the <li> elements within
    the <ul id="_tb_relevant_results"> container) such as:
      - Listing ID (prefixed with "inberlinwohnen_")
      - Number of rooms (from the first <strong> in the headline)
      - Area in m² (from the second <strong>)
      - Cold rent (from the third <strong>)
      - Details link (from the anchor with title containing "detailierte Wohnungsanzeige")
      - Title and address (extracted from the headline text split by "|")
    Only listings with 3 or more rooms and a cold rent up to 1,400 € are returned.
    """
    url = "https://inberlinwohnen.de/wohnungsfinder/"
    html = await fetch(session, url)
    if not html:
        logging.info("No HTML retrieved from inberlinwohnen page.")
        return []
    soup = BeautifulSoup(html, "html.parser")
    listings = []
    
    # Locate the container with the relevant results
    results_ul = soup.find("ul", id="_tb_relevant_results", class_="remember-list")
    if not results_ul:
        logging.info("No results container found on inberlinwohnen page.")
        return listings

    # Each listing is in an <li> with class "tb-merkflat ipg"
    listing_items = results_ul.find_all("li", class_="tb-merkflat ipg")
    for item in listing_items:
        try:
            listing_id = item.get("id")
            if not listing_id:
                continue
            full_id = f"inberlinwohnen_{listing_id}"
            
            # Extract the headline <h3> element
            h3 = item.find("h3")
            if not h3:
                continue
            # The headline typically contains details like:
            # "3 Zimmer, 71,82 m², 628,86 € | Freienwalder Str. 22, Alt-Hohenschönhausen"
            headline_text = h3.get_text(separator=" ", strip=True)
            parts = headline_text.split("|")
            if len(parts) >= 2:
                left_part = parts[0].strip()
                address = parts[1].strip()
            else:
                left_part = headline_text
                address_tag = item.find("a", title="Auf Karte anzeigen")
                address = address_tag.get_text(strip=True) if address_tag else ""
            
            # Extract numbers from the <strong> tags (rooms, area, rent)
            strong_tags = h3.find_all("strong")
            if len(strong_tags) < 3:
                continue
            try:
                rooms = float(strong_tags[0].get_text().replace(',', '.'))
                area = float(strong_tags[1].get_text().replace(',', '.'))
                rent_text = strong_tags[2].get_text(strip=True)
            except Exception as e:
                logging.error(f"Error converting numbers in listing {full_id}: {e}")
                continue

            # Filter out listings with fewer than 3 rooms
            if rooms < 3:
                continue

            # Normalize and check rent; remove currency symbols and extra text.
            rent_normalized = rent_text.replace('€', '').replace('ab', '').strip()
            # Remove thousand separators and convert decimal comma to dot.
            rent_normalized = rent_normalized.replace('.', '').replace(',', '.')
            try:
                rent_val = float(rent_normalized)
            except Exception as e:
                logging.error(f"Error converting rent in listing {full_id}: {e}")
                continue
            # Filter out if rent is above 1400 euros.
            if rent_val > 1400:
                continue

            # Extract the details link (anchor with title containing "detailierte Wohnungsanzeige")
            details_link_tag = item.find("a", title=lambda t: t and "detailierte Wohnungsanzeige" in t)
            if not details_link_tag:
                continue
            link = details_link_tag.get("href")
            if not link.startswith("http"):
                link = "https://inberlinwohnen.de" + link

            listings.append({
                "id": full_id,
                "rooms": rooms,
                "sqm": area,
                "rent": rent_text,
                "link": link,
                "title": headline_text,
                "address": address,
            })
        except Exception as e:
            logging.error(f"Error parsing inberlinwohnen listing: {e}", exc_info=True)

    logging.info(f"Found {len(listings)} listings on inberlinwohnen meeting criteria")
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
            scan_gewobag(),
            scan_wbm(session),
            scan_inberlinwohnen(session),
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

