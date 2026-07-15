"""Enrich review-queue festivals by inspecting their own websites.

Festivals discovered by the scraper often land in the "Needs review" queue with
no ticketing platform, prices, or revenue. For each one this module fetches the
homepage — and, if needed, likely "tickets"/"buy" pages — and pulls three things:

  * ticketing platform — from the fingerprints providers leave behind (links,
    widget scripts, iframes, redirect destinations)
  * ticket prices — from schema.org offer data, then visible "$NNN" prices on
    tickets pages as a fallback
  * attendance — only when the site itself states it ("50,000 attendees",
    "capacity of 30,000"), which then drives an estimated-revenue calculation
    (avg price x attendance, the same methodology used elsewhere)

Everything is conservative: a value is written only on a real match. Unknowns
are left blank so the row stays in review rather than getting fabricated data.
"""

import asyncio
import json
import re

import httpx
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 FestivalCRM/1.0",
    "Accept-Language": "en-US,en;q=0.9",
}

# Ordered: more specific / independent platforms first so that, e.g., a Tixr
# widget on a page that also has a Facebook Ticketmaster link wins for Tixr.
# Each entry: (display name, [substrings that appear in URLs/scripts/text]).
PLATFORM_SIGNATURES = [
    ("Tixr",                ["tixr.com"]),
    ("See Tickets",         ["seetickets.us", "seetickets.com", "wl.seetickets"]),
    ("DICE",                ["dice.fm"]),
    ("Eventbrite",          ["eventbrite.com", "eventbrite.co", "evbuc.com"]),
    ("Etix",                ["etix.com"]),
    ("ShowClix",            ["showclix.com"]),
    ("TicketSpice",         ["ticketspice.com"]),
    ("Universe",            ["universe.com"]),
    ("Eventix",             ["eventix.io"]),
    ("Fever",               ["feverup.com"]),
    ("Ticketleap",          ["ticketleap.com"]),
    ("Bandsintown",         ["bandsintown.com"]),
    ("Front Gate Tickets",  ["frontgatetickets.com"]),
    ("TicketWeb",           ["ticketweb.com"]),
    ("AXS",                 ["axs.com"]),
    ("Ticketmaster",        ["ticketmaster.com", "livenation.com/ticket"]),
]

TICKET_LINK_RE = re.compile(r"\b(tickets?|buy|passes?|register|lineup)\b", re.I)

# Plausible single-ticket / pass price bounds (USD). Anything outside is noise
# (phone numbers, years, "$5 fee", "$10,000 VIP table" outliers).
PRICE_MIN, PRICE_MAX = 20, 3000
PRICE_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})?(?:\.\d{2})?)")
# "50,000 attendees", "draws 30,000", "capacity of 25,000", "80,000 fans"
ATTENDANCE_RE = re.compile(
    r"(\d{2,3}(?:,\d{3}))\s*(?:\+\s*)?(?:people|fans|attendees|guests|"
    r"visitors|festivalgoers|patrons)"
    r"|(?:capacity|attendance|crowd|draws?|welcomed?|hosts?)\D{0,20}?"
    r"(\d{2,3}(?:,\d{3}))",
    re.I)


def _prices_from_offers(html: str) -> list[float]:
    """USD ticket prices from any schema.org offer JSON-LD on the page."""
    soup = BeautifulSoup(html, "html.parser")
    prices = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        nodes = data if isinstance(data, list) else data.get("@graph", [data])
        for node in nodes:
            if not isinstance(node, dict):
                continue
            offers = node.get("offers") or []
            if isinstance(offers, dict):
                offers = [offers]
            for o in offers:
                if not isinstance(o, dict):
                    continue
                if str(o.get("priceCurrency") or "USD").upper() not in ("USD", ""):
                    continue
                for k in ("price", "lowPrice", "highPrice"):
                    try:
                        p = float(str(o.get(k)).replace("$", "").replace(",", ""))
                    except (TypeError, ValueError):
                        continue
                    if PRICE_MIN <= p <= PRICE_MAX:
                        prices.append(p)
    return prices


def _prices_from_text(html: str) -> list[float]:
    """Visible '$NNN' prices in plausible ticket range, as a fallback."""
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    out = []
    for m in PRICE_RE.finditer(text):
        try:
            p = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if PRICE_MIN <= p <= PRICE_MAX:
            out.append(p)
    return out


def _attendance_from_text(html: str) -> int | None:
    """Attendance/capacity if the page states it, else None."""
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    best = None
    for m in ATTENDANCE_RE.finditer(text):
        raw = m.group(1) or m.group(2)
        try:
            n = int(raw.replace(",", ""))
        except (ValueError, AttributeError):
            continue
        if 1000 <= n <= 2_000_000:
            best = max(best or 0, n)
    return best


def detect_from_html(html: str) -> str | None:
    """Return the first ticketing platform whose fingerprint appears, or None."""
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    haystacks = []
    for tag in soup.find_all(["a", "iframe", "script", "link", "form"]):
        for attr in ("href", "src", "action", "data-src", "data-url"):
            v = tag.get(attr)
            if v:
                haystacks.append(v.lower())
    blob = " ".join(haystacks)
    for name, needles in PLATFORM_SIGNATURES:
        if any(n in blob for n in needles):
            return name
    return None


def _ticket_links(html: str, base_url: str) -> list[str]:
    """Same-site links whose text/href suggests a tickets or lineup page."""
    from urllib.parse import urljoin, urlparse
    soup = BeautifulSoup(html, "html.parser")
    base_host = urlparse(base_url).netloc.replace("www.", "")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        href = a["href"]
        if not (TICKET_LINK_RE.search(text) or TICKET_LINK_RE.search(href)):
            continue
        full = urljoin(base_url, href)
        host = urlparse(full).netloc.replace("www.", "")
        if host and host != base_host:      # off-site link is itself the answer
            continue
        if full not in seen:
            seen.add(full)
            out.append(full)
    return out[:3]


def _harvest(html: str, url: str, acc: dict):
    """Fold one page's signals (platform, prices, attendance) into acc."""
    if acc.get("platform") is None:
        acc["platform"] = detect_from_html(html)
    prices = _prices_from_offers(html) or _prices_from_text(html)
    acc["prices"].extend(prices)
    if acc.get("attendance") is None:
        acc["attendance"] = _attendance_from_text(html)


async def enrich_website(client: httpx.AsyncClient, website: str) -> dict:
    """Fetch a festival site (+ up to 3 ticket subpages) and return whatever
    of {platform, price_min, price_max, attendance} could be found."""
    result = {"platform": None, "price_min": None, "price_max": None,
              "attendance": None}
    if not website:
        return result
    try:
        r = await client.get(website, headers=HEADERS, follow_redirects=True,
                             timeout=20)
    except httpx.HTTPError:
        return result
    if r.status_code != 200:
        return result

    acc = {"platform": None, "prices": [], "attendance": None}
    _harvest(r.text, str(r.url), acc)

    # Ticket subpages usually hold the real prices (and sometimes the platform).
    for link in _ticket_links(r.text, str(r.url)):
        try:
            sub = await client.get(link, headers=HEADERS, follow_redirects=True,
                                   timeout=20)
        except httpx.HTTPError:
            continue
        if acc["platform"] is None:
            final = str(sub.url).lower()   # redirect to a ticketing domain = signal
            for name, needles in PLATFORM_SIGNATURES:
                if any(n in final for n in needles):
                    acc["platform"] = name
                    break
        if sub.status_code == 200:
            _harvest(sub.text, str(sub.url), acc)
        await asyncio.sleep(0.2)

    result["platform"] = acc["platform"]
    if acc["prices"]:
        result["price_min"] = min(acc["prices"])
        hi = max(acc["prices"])
        result["price_max"] = hi if hi > result["price_min"] else None
    result["attendance"] = acc["attendance"]
    return result


async def enrich_for_festivals(festivals: list) -> dict[int, dict]:
    """Given ORM Festival rows, return {id: {platform, price_min, price_max,
    attendance}} for rows where at least one field was found. Only festivals
    with a website are attempted; caller decides how to persist."""
    results: dict[int, dict] = {}
    async with httpx.AsyncClient() as client:
        for f in festivals:
            data = await enrich_website(client, f.website)
            if any(v is not None for v in data.values()):
                results[f.id] = data
            await asyncio.sleep(0.3)
    return results
