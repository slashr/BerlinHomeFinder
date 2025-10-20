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
        async def goto(self, url, **kwargs):
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
        async def goto(self, url, **kwargs):
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


def test_scan_gewobag_retry_success(monkeypatch):
    html = """
    <article id='c1' class='angebot-big-box'>
        <h3 class='angebot-title'>Retry ok</h3>
        <address>Berlin</address>
        <table><tr class='angebot-area'><td>3 Zimmer | 65 m²</td></tr></table>
        <a class='read-more-link' href='/flat3'>Mehr</a>
    </article>
    """

    attempts = {"count": 0}

    class DummyPage:
        async def goto(self, url, **kwargs):
            attempts["count"] += 1
            if attempts["count"] < 2:
                raise RuntimeError("fail")
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

    async def fake_sleep(_):
        pass

    monkeypatch.setattr(scan, "ensure_browser", fake_ensure_browser)
    monkeypatch.setattr(scan.asyncio, "sleep", fake_sleep)
    listings = asyncio.run(scan.scan_gewobag())
    assert attempts["count"] == 2
    assert listings and listings[0]["id"] == "gewobag_c1"


def test_scan_gewobag_retry_fail(monkeypatch):
    attempts = {"count": 0}
    error_calls = []

    class DummyPage:
        async def goto(self, url, **kwargs):
            attempts["count"] += 1
            raise RuntimeError("fail")
        async def wait_for_selector(self, selector, timeout=5000):
            pass
        async def click(self, selector):
            pass
        async def wait_for_load_state(self, state):
            pass
        async def content(self):
            return ""

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

    async def fake_sleep(_):
        pass

    def fake_error(msg, *args, **kwargs):
        error_calls.append(kwargs.get("exc_info"))

    monkeypatch.setattr(scan, "ensure_browser", fake_ensure_browser)
    monkeypatch.setattr(scan.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(scan.log, "error", fake_error)

    listings = asyncio.run(scan.scan_gewobag())
    assert attempts["count"] == 3
    assert listings == []
    assert error_calls == [None]


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


def test_scan_inberlinwohnen_skip_wbm(monkeypatch):
    html = """
    <ul id='_tb_relevant_results'>
        <li id='b2' class='tb-merkflat'>
            <a title='detailierte Ansicht' href='https://www.wbm.de/foo'>Link</a>
            <h3>WBM</h3>
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
    assert listings == []


def test_scan_inberlinwohnen_skip_gewobag(monkeypatch):
    html = """
    <ul id='_tb_relevant_results'>
        <li id='b3' class='tb-merkflat'>
            <a title='detailierte Ansicht' href='https://www.gewobag.de/foo'>Link</a>
            <h3>Gewobag</h3>
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
    assert listings == []


def test_scan_stubs():
    assert asyncio.run(scan.scan_gesobau()) == []
    assert asyncio.run(scan.scan_degewo()) == []
    assert asyncio.run(scan.scan_howoge()) == []
    assert asyncio.run(scan.scan_stadtundland()) == []


def test_build_message_with_location_and_rent():
    listing = {
        "id": "demo_1",
        "rooms": 3.0,
        "sqm": 72.0,
        "link": "https://example.com/listing",
        "rent": "1450",
        "title": "Helle Wohnung",
        "address": "Prenzlauer Berg",
        "provider": "DemoProvider",
    }

    message = scan.build_message(listing)

    assert "Prenzlauer Berg" in message
    assert "1450 €" in message
    assert "<b>DemoProvider</b>" in message


def test_build_message_without_location_or_rent():
    listing = {
        "id": "demo_2",
        "rooms": 2.5,
        "sqm": 65.0,
        "link": "https://example.com/listing2",
        "rent": None,
        "title": "Schöne Wohnung",
        "address": None,
        "provider": "DemoProvider",
    }

    message = scan.build_message(listing)

    assert "📍" not in message
    assert "💶" not in message
    assert "Listing</a>" in message
