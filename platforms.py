"""Detect which ticketing platform a festival uses by inspecting its website.

Festivals discovered by the scraper often land with no ticketing platform (the
"Needs review" queue). This module fetches a festival's homepage — and, if
needed, a likely "tickets"/"buy" page — and looks for the fingerprints ticketing
providers leave behind: outbound links, embedded widget scripts, and iframes
pointing at their domains.

Detection is deliberately conservative: it reports a platform only on a real
domain/script match, never a guess. Anything it can't resolve is left blank so
the row stays in review rather than getting bad data.
"""

import asyncio
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


async def detect_platform(client: httpx.AsyncClient, website: str) -> str | None:
    """Fetch a festival site (and up to 3 ticket subpages) and detect platform."""
    if not website:
        return None
    try:
        r = await client.get(website, headers=HEADERS, follow_redirects=True,
                             timeout=20)
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    html = r.text
    found = detect_from_html(html)
    if found:
        return found
    for link in _ticket_links(html, str(r.url)):
        try:
            sub = await client.get(link, headers=HEADERS, follow_redirects=True,
                                   timeout=20)
        except httpx.HTTPError:
            continue
        # A redirect straight to a ticketing domain is itself the signal.
        final = str(sub.url).lower()
        for name, needles in PLATFORM_SIGNATURES:
            if any(n in final for n in needles):
                return name
        if sub.status_code == 200:
            found = detect_from_html(sub.text)
            if found:
                return found
        await asyncio.sleep(0.2)
    return None


async def detect_for_festivals(festivals: list) -> dict[int, str]:
    """Given ORM Festival rows, return {id: platform} for those detected.

    Only festivals with a website are attempted. Caller decides how to persist.
    """
    results: dict[int, str] = {}
    async with httpx.AsyncClient() as client:
        for f in festivals:
            platform = await detect_platform(client, f.website)
            if platform:
                results[f.id] = platform
            await asyncio.sleep(0.3)
    return results
