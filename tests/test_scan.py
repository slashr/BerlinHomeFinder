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
    expected_id = scan.build_wbm_listing_id("https://www.wbm.de/d1", 3.0, 70.0)
    assert listings == [
        {
            "id": expected_id,
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


def test_scan_inberlinwohnen_keeps_legacy_room_filter(monkeypatch):
    html = """
    <ul id='_tb_relevant_results'>
        <li id='b3' class='tb-merkflat'>
            <a title='detailierte Ansicht' href='/b3detail'>Link</a>
            <h3>Compact three-room flat</h3>
            <strong>3</strong>
            <strong>55</strong>
            <strong>ab 1200 €</strong>
        </li>
    </ul>
    """

    async def fake_fetch(url, *, params=None, timeout=12):
        return html

    monkeypatch.setattr(scan, "fetch", fake_fetch)
    listings = asyncio.run(scan.scan_inberlinwohnen())

    assert listings and listings[0]["id"] == "inberlinwohnen_b3"


def test_parse_number_handles_german_formats():
    assert scan._parse_number("1.179,98 €") == 1179.98
    assert scan._parse_number("1.234 €") == 1234.0
    assert scan._parse_number("1.234.567 €") == 1234567.0
    assert scan._parse_number("2,5 Zimmer") == 2.5
    assert scan._parse_number("70.5 m²") == 70.5
    assert scan._parse_number(None) is None
    assert scan._passes_rent_filter("1.600,00 €")
    assert not scan._passes_rent_filter("1.600,01 €")
    assert scan._rent_text("1.179,98 €") == "1.179,98"


def test_scan_gesobau(monkeypatch):
    detail_fetches = []
    payload = [
        {
            "uid": 1,
            "detail": "/detail/large",
            "title": "Gerichtstraße 13",
            "raw": {
                "objekt_nr_extern_stringS": "obj-1",
                "url": "/detail/large",
                "title": "Große Wohnung",
                "adresse_stringS": "Gerichtstraße 13",
                "plz_stringS": "13347",
                "ort_stringS": "Berlin",
                "wohnflaeche_floatS": "70,5",
                "warmmiete_floatS": "1.179,98",
            },
        },
        {
            "uid": 2,
            "detail": "/detail/small",
            "title": "Kleine Wohnung",
            "raw": {
                "objekt_nr_extern_stringS": "obj-2",
                "zimmer_intS": 2,
                "wohnflaeche_floatS": 80,
            },
        },
        {
            "uid": 3,
            "detail": "/detail/missing-size",
            "title": "No size",
            "raw": {
                "objekt_nr_extern_stringS": "obj-3",
                "url": "/detail/missing-size",
                "warmmiete_floatS": 900,
            },
        },
    ]

    async def fake_fetch_json(url, *, data=None, params=None, timeout=12):
        return payload

    async def fake_fetch(url, *, params=None, timeout=12):
        detail_fetches.append(url)
        return "<p>2,5 Zimmer</p>"

    monkeypatch.setattr(scan, "fetch_json", fake_fetch_json)
    monkeypatch.setattr(scan, "fetch", fake_fetch)

    listings = asyncio.run(scan.scan_gesobau())

    assert listings == [
        {
            "id": "gesobau_obj-1",
            "rooms": 2.5,
            "sqm": 70.5,
            "link": "https://www.gesobau.de/detail/large",
            "rent": "1.179,98",
            "title": "Große Wohnung",
            "address": "Gerichtstraße 13, 13347, Berlin",
            "provider": "GESOBAU",
        }
    ]
    assert detail_fetches == ["https://www.gesobau.de/detail/large"]


def test_fetch_gesobau_detail_rooms_uses_hero_metadata(monkeypatch):
    html = """
    <nav>Wohnungen ab 1 Zimmer</nav>
    <ul class='immoHero__metaData'>
        <li>2,5 Zimmer</li>
    </ul>
    """

    async def fake_fetch(url, *, params=None, timeout=12):
        return html

    monkeypatch.setattr(scan, "fetch", fake_fetch)

    assert asyncio.run(scan._fetch_gesobau_detail_rooms("https://example.com")) == 2.5


def test_scan_degewo(monkeypatch):
    first_page = """
    <div class='c-teaser c-teaser--apartment'>
        <button data-openimmo-bookmark-item-uid='W-small'></button>
        <h3><a href='/immosuche/details/small'>Klein</a></h3>
        <p>Adresse 1 | Kiez</p>
        <div class='c-definition-list__item'><dt>524,12 €</dt><dd>Warmmiete</dd></div>
        <div class='c-definition-list__item'><dt>1</dt><dd>Zimmer</dd></div>
        <div class='c-definition-list__item'><dt>39,67</dt><dd>m²</dd></div>
    </div>
    <a href='/immosuche?tx_openimmo_immobilie%5Bpage%5D=2&tx_openimmo_immobilie%5Bsearch%5D=paginate#immo-teaser-list'>2</a>
    """
    second_page = """
    <div class='c-teaser c-teaser--apartment'>
        <button data-openimmo-bookmark-item-uid='W-large'></button>
        <h3><a href='/immosuche/details/large'>Große Wohnung</a></h3>
        <p>Adresse 2 | Kiez</p>
        <div class='c-definition-list__item'><dt>1.250,50 €</dt><dd>Warmmiete</dd></div>
        <div class='c-definition-list__item'><dt>3</dt><dd>Zimmer</dd></div>
        <div class='c-definition-list__item'><dt>72,5</dt><dd>m²</dd></div>
    </div>
    """

    async def fake_fetch(url, *, params=None, timeout=12):
        if "page%5D=2" in url:
            return second_page
        return first_page

    monkeypatch.setattr(scan, "fetch", fake_fetch)

    listings = asyncio.run(scan.scan_degewo())

    assert listings == [
        {
            "id": "degewo_W-large",
            "rooms": 3.0,
            "sqm": 72.5,
            "link": "https://www.degewo.de/immosuche/details/large",
            "rent": "1.250,50",
            "title": "Große Wohnung",
            "address": "Adresse 2 | Kiez",
            "provider": "degewo",
        }
    ]


def test_scan_howoge(monkeypatch):
    payload = {
        "immoobjects": [
            {
                "uid": 7094,
                "title": "Streitstraße 5, 13587 Berlin",
                "district": "Hakenfelde",
                "rent": 803,
                "area": 73,
                "rooms": 3,
                "link": "/immobiliensuche/wohnungssuche/detail/1771-14536-9997.html",
                "notice": " 3-Zimmer-Wohnung (WBS 100-140)",
            },
            {
                "uid": 7095,
                "title": "Kleine Wohnung",
                "rent": 500,
                "area": 40,
                "rooms": 1,
                "link": "/small.html",
            },
        ]
    }

    async def fake_fetch_json(url, *, data=None, params=None, timeout=12):
        return payload

    monkeypatch.setattr(scan, "fetch_json", fake_fetch_json)

    listings = asyncio.run(scan.scan_howoge())

    assert listings == [
        {
            "id": "howoge_7094",
            "rooms": 3.0,
            "sqm": 73.0,
            "link": "https://www.howoge.de/immobiliensuche/wohnungssuche/detail/1771-14536-9997.html",
            "rent": "803",
            "title": "3-Zimmer-Wohnung (WBS 100-140)",
            "address": "Streitstraße 5, 13587 Berlin",
            "provider": "HOWOGE",
        }
    ]


def test_scan_stadtundland_stub():
    assert asyncio.run(scan.scan_stadtundland()) == []


def test_build_wbm_listing_id_stable():
    link = "https://www.wbm.de/wohnungen-berlin/angebote/details/foo-bar/"
    first = scan.build_wbm_listing_id(link, 3.0, 70.0)
    second = scan.build_wbm_listing_id(link, 3.00, 70.00)
    assert first == second


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


def test_send_notifications_persists_successes_after_partial_failure(monkeypatch):
    sent_links = []
    saved_states = []

    class FakeBot:
        async def send_message(self, *, chat_id, text, parse_mode, disable_web_page_preview):
            if "example.com/b" in text:
                raise RuntimeError("telegram unavailable")
            sent_links.append(text)

    listings = [
        {
            "id": "a",
            "rooms": 3.0,
            "sqm": 70.0,
            "link": "https://example.com/a",
            "rent": None,
            "title": "A",
            "address": None,
            "provider": "DemoProvider",
        },
        {
            "id": "b",
            "rooms": 3.0,
            "sqm": 71.0,
            "link": "https://example.com/b",
            "rent": None,
            "title": "B",
            "address": None,
            "provider": "DemoProvider",
        },
        {
            "id": "c",
            "rooms": 3.0,
            "sqm": 72.0,
            "link": "https://example.com/c",
            "rent": None,
            "title": "C",
            "address": None,
            "provider": "DemoProvider",
        },
    ]

    def fake_save_state(state):
        saved_states.append(set(state))

    monkeypatch.setattr(scan, "bot", FakeBot())
    monkeypatch.setattr(scan, "notified", set())
    monkeypatch.setattr(scan, "save_state", fake_save_state)

    asyncio.run(scan.send_notifications(listings))

    assert len(sent_links) == 2
    assert scan.notified == {"a", "c"}
    assert saved_states == [{"a"}, {"a", "c"}]
