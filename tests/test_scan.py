import os
import sys
import asyncio

# Ensure project root is on path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Set dummy environment variables so the scan module can import
os.environ.setdefault('TELEGRAM_BOT_TOKEN', 'dummy')
os.environ.setdefault('TELEGRAM_USER_ID', 'dummy')

import scan


def test_scan_gewobag(monkeypatch):
    html = """
    <article id='a1' class='angebot-big-box'>
        <h3 class='angebot-title'>Top Wohnung</h3>
        <address>Berlin</address>
        <table><tr class='angebot-area'><td>3 Zimmer | 65,0 m²</td></tr></table>
        <a class='read-more-link' href='/flat1'>Mehr</a>
    </article>
    """

    class DummyPage:
        async def goto(self, url):
            pass
        async def wait_for_selector(self, selector, timeout=5000):
            pass
        async def click(self, selector):
            pass
        async def wait_for_load_state(self, state):
            pass
        async def content(self):
            return html

    class DummyContext:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            pass
        async def new_page(self):
            return DummyPage()

    class DummyBrowser:
        async def new_context(self):
            return DummyContext()

    async def fake_ensure_browser():
        return DummyBrowser()

    monkeypatch.setattr(scan, "ensure_browser", fake_ensure_browser)
    listings = asyncio.run(scan.scan_gewobag())
    assert listings == [
        {
            "id": "gewobag_a1",
            "rooms": 3.0,
            "sqm": 65.0,
            "link": "https://www.gewobag.de/flat1",
            "rent": None,
            "title": "Top Wohnung",
            "address": "Berlin",
            "provider": "Gewobag",
        }
    ]


def test_scan_gewobag_relative(monkeypatch):
    html = """
    <article id='b1' class='angebot-big-box'>
        <h3 class='angebot-title'>Noch eine</h3>
        <address>Berlin</address>
        <table><tr class='angebot-area'><td>3 Zimmer | 66 m²</td></tr></table>
        <a class='read-more-link' href='../flat2'>Mehr</a>
    </article>
    """

    class DummyPage:
        async def goto(self, url):
            pass
        async def wait_for_selector(self, selector, timeout=5000):
            pass
        async def click(self, selector):
            pass
        async def wait_for_load_state(self, state):
            pass
        async def content(self):
            return html

    class DummyContext:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            pass
        async def new_page(self):
            return DummyPage()

    class DummyBrowser:
        async def new_context(self):
            return DummyContext()

    async def fake_ensure_browser():
        return DummyBrowser()

    monkeypatch.setattr(scan, "ensure_browser", fake_ensure_browser)
    listings = asyncio.run(scan.scan_gewobag())
    assert listings[0]["link"] == "https://www.gewobag.de/flat2"


def test_scan_wbm(monkeypatch):
    html = """
    <div class='row openimmo-search-list-item' data-uid='u1'>
        <div class='main-property-rooms'>3,0</div>
        <div class='main-property-size'>70 m²</div>
        <a title='Details' href='/d1'>Details</a>
    </div>
    """

    async def fake_fetch(url, *, params=None, timeout=12):
        return html

    monkeypatch.setattr(scan, "fetch", fake_fetch)
    listings = asyncio.run(scan.scan_wbm())
    assert listings == [
        {
            "id": "wbm_u1",
            "rooms": 3.0,
            "sqm": 70.0,
            "link": "https://www.wbm.de/d1",
            "rent": None,
            "title": None,
            "address": None,
            "provider": "WBM",
        }
    ]


def test_scan_inberlinwohnen(monkeypatch):
    html = """
    <ul id='_tb_relevant_results'>
        <li id='b1' class='tb-merkflat'>
            <a title='detailierte Ansicht' href='/b1detail'>Link</a>
            <h3>Feine Wohnung</h3>
            <strong>3</strong>
            <strong>70</strong>
            <strong>ab 1200 €</strong>
        </li>
    </ul>
    """

    async def fake_fetch(url, *, params=None, timeout=12):
        return html

    monkeypatch.setattr(scan, "fetch", fake_fetch)
    listings = asyncio.run(scan.scan_inberlinwohnen())
    assert listings == [
        {
            "id": "inberlinwohnen_b1",
            "rooms": 3.0,
            "sqm": 70.0,
            "link": "https://inberlinwohnen.de/b1detail",
            "rent": "1200",
            "title": "Feine Wohnung",
            "address": None,
            "provider": "inBerlinWohnen",
        }
    ]


def test_scan_stubs():
    assert asyncio.run(scan.scan_gesobau()) == []
    assert asyncio.run(scan.scan_degewo()) == []
    assert asyncio.run(scan.scan_howoge()) == []
    assert asyncio.run(scan.scan_stadtundland()) == []
