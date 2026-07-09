"""Daily discovery scrape for new U.S. music festivals.

Sources:
  1. Music Festival Wizard's US festival guide (names, locations, dates)
  2. Wikipedia's "List of music festivals in the United States" (names), with a
     follow-up fetch of each new festival's article to pull attendance/website
     from the infobox so revenue can be estimated.
  3. Tixr's public sitemap: event pages whose slug looks festival-like are
     fetched and their schema.org Event JSON-LD parsed. US events are added
     with ticketing_platform="Tixr", and existing festivals that lack a
     platform get tagged "Tixr" when found there.

Rules:
  - Festivals already in the DB (matched on normalized name) are skipped
    (Tixr may still enrich their platform field).
  - If attendance is found, revenue is estimated (attendance x avg pass price,
    defaulting to a conservative $200/pass when prices are unknown). Estimates
    >= $2M land in the main list, flagged needs_review until confirmed.
  - Candidates with no revenue signal go to the review queue (needs_review,
    no revenue), capped per run so a first scrape doesn't flood the table.
    Tixr finds are exempt from the cap — knowing a festival sells on Tixr is
    the point, so they all land in the review queue.
"""

import asyncio
import json
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from database import (
    QUALIFYING_REVENUE,
    Festival,
    ScrapeLog,
    SessionLocal,
    normalize_name,
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 FestivalCRM/1.0",
    "Accept-Language": "en-US,en;q=0.9",
}

MFW_URL = "https://www.musicfestivalwizard.com/festival-guide/us-festivals/page/{page}/"
MFW_MAX_PAGES = 10
WIKI_LIST_URL = "https://en.wikipedia.org/wiki/List_of_music_festivals_in_the_United_States"
WIKI_ARTICLE_FETCH_LIMIT = 60   # max festival articles fetched per run
REVIEW_QUEUE_CAP = 40           # max no-revenue candidates added per run
DEFAULT_PASS_PRICE = 200        # conservative avg pass price when unknown

TIXR_BASE = "https://www.tixr.com"
TIXR_SITEMAP_LIMIT = 40         # max sitemap files walked per run
TIXR_EVENT_FETCH_LIMIT = 150    # max event pages fetched per run
FESTIVAL_SLUG_RE = re.compile(r"fest", re.I)

MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}


async def _get(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        r = await client.get(url, headers=HEADERS, follow_redirects=True, timeout=30)
        if r.status_code == 200:
            return r.text
    except httpx.HTTPError:
        pass
    return None


def _parse_mfw_page(html: str) -> list[dict]:
    """One MFW guide page -> [{name, city, state, dates, source_url}]."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.select("h2 a[href], h3 a[href], .entry-title a[href]"):
        href = a.get("href", "")
        name = a.get_text(strip=True)
        if not name or "/festivals/" not in href:
            continue
        cand = {"name": name, "city": None, "state": None, "dates": None,
                "source_url": href}
        container = a.find_parent(["article", "li", "div"])
        if container:
            loc_el = container.select_one(
                ".festival-location, .festivallocation, [class*=location]")
            date_el = container.select_one(
                ".festival-date, .festivaldate, [class*=date]")
            loc = loc_el.get_text(" ", strip=True) if loc_el else None
            if not loc or not date_el:
                # fallback: "City, ST" and a month-day pattern anywhere in the card
                txt = container.get_text(" ", strip=True)
                m = re.search(r"([A-Za-z .'-]+),\s*([A-Z]{2})\b", txt)
                if m and m.group(2) in US_STATES:
                    loc = f"{m.group(1).strip()}, {m.group(2)}"
                d = re.search(
                    r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}[^|<]{0,30}\d{4})",
                    txt)
                if d and not date_el:
                    cand["dates"] = d.group(1).strip()
            if date_el:
                cand["dates"] = date_el.get_text(" ", strip=True)
            if loc:
                parts = [p.strip() for p in loc.split(",")]
                if len(parts) >= 2 and parts[-1].upper() in US_STATES:
                    cand["city"], cand["state"] = ", ".join(parts[:-1]), parts[-1].upper()
                else:
                    cand["city"] = loc
        out.append(cand)
    return out


def _parse_wiki_list(html: str) -> list[dict]:
    """Wikipedia list page -> [{name, article_url}] for linked festival articles."""
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one("#mw-content-text") or soup
    out, seen = [], set()
    for a in content.select("li > a[href^='/wiki/']"):
        href = a.get("href", "")
        name = a.get_text(strip=True)
        if (not name or len(name) < 4 or ":" in href
                or href.startswith("/wiki/List_of")
                or re.match(r"^\[?\d", name)):
            continue
        key = normalize_name(name)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({"name": name,
                    "article_url": f"https://en.wikipedia.org{href}"})
    return out


def _parse_wiki_article(html: str) -> dict:
    """Festival article -> {attendance, website, city, state, dates} from infobox."""
    soup = BeautifulSoup(html, "html.parser")
    info = {}
    box = soup.select_one("table.infobox")
    if not box:
        return info
    for row in box.select("tr"):
        th, td = row.find("th"), row.find("td")
        if not th or not td:
            continue
        label = th.get_text(" ", strip=True).lower()
        value = td.get_text(" ", strip=True)
        if "attendance" in label:
            nums = [int(n.replace(",", "")) for n in
                    re.findall(r"\d{1,3}(?:,\d{3})+|\d{4,}", value)]
            if nums:
                info["attendance"] = max(nums)
        elif "website" in label:
            link = td.find("a", href=True)
            if link and link["href"].startswith("http"):
                info["website"] = link["href"]
        elif "location" in label:
            m = re.search(r"([A-Za-z .'-]+),\s*([A-Za-z ]+)$", value)
            if m:
                info["city"] = m.group(1).strip()
                st = m.group(2).strip()
                info["state"] = st if st.upper() in US_STATES else st
        elif "dates" in label or label == "date(s)":
            info["dates"] = value[:80]
    return info


def _iter_jsonld_events(html: str):
    """Yield schema.org Event-ish dicts from a page's JSON-LD blocks."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        nodes = data if isinstance(data, list) else data.get("@graph", [data])
        for node in nodes:
            if not isinstance(node, dict):
                continue
            t = node.get("@type", "")
            types = t if isinstance(t, list) else [t]
            if any("Event" in str(x) or "Festival" in str(x) for x in types):
                yield node


def _parse_offers(ev: dict) -> tuple[float | None, float | None]:
    """Pull (min, max) USD ticket prices from a JSON-LD event's offers."""
    offers = ev.get("offers") or []
    if isinstance(offers, dict):
        offers = [offers]
    prices = []
    for o in offers:
        if not isinstance(o, dict):
            continue
        currency = str(o.get("priceCurrency") or "USD").upper()
        if currency not in ("USD", ""):
            continue
        for k in ("price", "lowPrice", "highPrice"):
            try:
                p = float(str(o.get(k)).replace("$", "").replace(",", ""))
            except (TypeError, ValueError):
                continue
            if p > 0:
                prices.append(p)
    if not prices:
        return None, None
    lo, hi = min(prices), max(prices)
    return lo, (hi if hi > lo else None)


def _parse_tixr_event(html: str, url: str) -> dict | None:
    """Event page -> candidate dict, or None if it isn't a US event."""
    for ev in _iter_jsonld_events(html):
        name = (ev.get("name") or "").strip()
        if not name:
            continue
        loc = ev.get("location") or {}
        if isinstance(loc, list):
            loc = loc[0] if loc else {}
        addr = loc.get("address") or {} if isinstance(loc, dict) else {}
        if isinstance(addr, str):
            addr = {"streetAddress": addr}
        country = str(addr.get("addressCountry") or "").upper()
        state = str(addr.get("addressRegion") or "").strip()
        city = str(addr.get("addressLocality") or "").strip() or None
        is_us = (country in ("US", "USA", "UNITED STATES")
                 or (not country and state.upper() in US_STATES))
        if not is_us:
            continue
        dates = None
        start_month = None
        start = str(ev.get("startDate") or "")
        end = str(ev.get("endDate") or "")
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", start)
        if m:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            start_month = month
            dates = f"{MONTHS[month]} {day}"
            m2 = re.match(r"(\d{4})-(\d{2})-(\d{2})", end)
            if m2 and m2.group(0) != m.group(0):
                em, ed = int(m2.group(2)), int(m2.group(3))
                dates += f"–{ed}" if em == month else f" – {MONTHS[em]} {ed}"
            dates += f", {year}"
        price_min, price_max = _parse_offers(ev)
        return {
            "name": name,
            "city": city,
            "state": state.upper() if state.upper() in US_STATES else (state or None),
            "dates": dates,
            "start_month": start_month,
            "price_min": price_min,
            "price_max": price_max,
            "website": url,
            "origin": "tixr",
            "source_url": url,
            "platform": "Tixr",
        }
    return None


async def _tixr_event_urls(client: httpx.AsyncClient) -> list[str]:
    """Walk Tixr's sitemap(s) and return event URLs with festival-like slugs."""
    robots = await _get(client, f"{TIXR_BASE}/robots.txt")
    queue = re.findall(r"(?im)^sitemap:\s*(\S+)", robots or "")
    if not queue:
        queue = [f"{TIXR_BASE}/sitemap.xml"]
    seen_maps, urls = set(), set()
    while queue and len(seen_maps) < TIXR_SITEMAP_LIMIT:
        sm = queue.pop(0)
        if sm in seen_maps:
            continue
        seen_maps.add(sm)
        xml = await _get(client, sm)
        if not xml:
            continue
        for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml):
            last = loc.rstrip("/").rsplit("/", 1)[-1]
            if last.endswith(".xml") or "sitemap" in last.lower():
                queue.append(loc)
            elif "/events/" in loc or "/e/" in loc:
                urls.add(loc)
        await asyncio.sleep(0.3)
    return sorted(u for u in urls if FESTIVAL_SLUG_RE.search(u.rsplit("/", 1)[-1]))


async def _collect_tixr(client: httpx.AsyncClient, db, existing: set) -> tuple[list, int, int]:
    """Fetch festival-looking Tixr events.

    Returns (new_candidates, pages_scanned, existing_rows_tagged). Existing
    festivals with an empty ticketing_platform found on Tixr are tagged in
    place as an enrichment.
    """
    candidates, scanned, enriched = [], 0, 0
    seen_keys = set()
    for url in (await _tixr_event_urls(client))[:TIXR_EVENT_FETCH_LIMIT]:
        html = await _get(client, url)
        scanned += 1
        if html:
            c = _parse_tixr_event(html, url)
            if c:
                key = normalize_name(c["name"])
                if not key or key in seen_keys:
                    pass
                elif key in existing:
                    row = (db.query(Festival)
                           .filter(Festival.name_key == key).first())
                    changed = False
                    if row and not row.ticketing_platform:
                        row.ticketing_platform = "Tixr"
                        row.notes = ((row.notes + "; ") if row.notes else "") + \
                            f"Found selling on Tixr ({url})"
                        changed = True
                    if (row and row.ticket_price_min is None
                            and row.ticket_price_max is None
                            and c.get("price_min") is not None):
                        row.ticket_price_min = c["price_min"]
                        row.ticket_price_max = c["price_max"]
                        changed = True
                    if changed:
                        enriched += 1
                else:
                    seen_keys.add(key)
                    candidates.append(c)
        await asyncio.sleep(0.4)  # be polite
    return candidates, scanned, enriched


def _estimate_revenue(attendance, price_min, price_max):
    if not attendance:
        return None
    if price_min and price_max:
        price = 0.8 * price_min + 0.2 * price_max  # rough GA/VIP mix
    else:
        price = price_min or price_max or DEFAULT_PASS_PRICE
    return round(attendance * price)


async def run_scrape() -> ScrapeLog:
    """One full discovery pass. Returns the persisted ScrapeLog row."""
    db = SessionLocal()
    log = ScrapeLog(started_at=datetime.utcnow(), status="running")
    db.add(log)
    db.commit()

    try:
        existing = {k for (k,) in db.query(Festival.name_key).all() if k}
        candidates: dict[str, dict] = {}

        async with httpx.AsyncClient() as client:
            # -- Music Festival Wizard ------------------------------------
            for page in range(1, MFW_MAX_PAGES + 1):
                html = await _get(client, MFW_URL.format(page=page))
                if not html:
                    break
                page_items = _parse_mfw_page(html)
                if not page_items:
                    break
                for c in page_items:
                    key = normalize_name(c["name"])
                    if key and key not in existing and key not in candidates:
                        c["origin"] = "mfw"
                        candidates[key] = c
                await asyncio.sleep(1)  # be polite

            # -- Wikipedia list + article infoboxes ------------------------
            html = await _get(client, WIKI_LIST_URL)
            wiki_new = []
            if html:
                for c in _parse_wiki_list(html):
                    key = normalize_name(c["name"])
                    if key and key not in existing and key not in candidates:
                        wiki_new.append((key, c))
            for key, c in wiki_new[:WIKI_ARTICLE_FETCH_LIMIT]:
                art = await _get(client, c["article_url"])
                detail = _parse_wiki_article(art) if art else {}
                candidates[key] = {
                    "name": c["name"],
                    "city": detail.get("city"),
                    "state": detail.get("state"),
                    "dates": detail.get("dates"),
                    "website": detail.get("website"),
                    "attendance": detail.get("attendance"),
                    "origin": "wikipedia",
                    "source_url": c["article_url"],
                }
                await asyncio.sleep(0.5)

            # -- Tixr sitemap crawl ----------------------------------------
            tixr_scanned = tixr_enriched = 0
            try:
                tixr_new, tixr_scanned, tixr_enriched = await _collect_tixr(
                    client, db, existing)
                for c in tixr_new:
                    key = normalize_name(c["name"])
                    if key and key not in candidates:
                        candidates[key] = c
                if tixr_enriched:
                    db.commit()
            except Exception as e:
                tixr_note = f"Tixr source failed: {type(e).__name__}: {e}"
            else:
                tixr_note = (f"Tixr: {tixr_scanned} event pages scanned, "
                             f"{tixr_enriched} existing festivals tagged")

        # -- Insert --------------------------------------------------------
        added = review_added = 0
        for key, c in candidates.items():
            attendance = c.get("attendance")
            est = _estimate_revenue(attendance, None, None)
            qualified = est is not None and est >= QUALIFYING_REVENUE
            if not qualified:
                # Tixr finds are exempt from the cap: platform intel is the point
                if c.get("origin") != "tixr":
                    if review_added >= REVIEW_QUEUE_CAP:
                        continue
                review_added += 1
            note_bits = [f"Auto-scraped from {c.get('origin')} ({c.get('source_url', '')})"]
            if qualified:
                note_bits.append(
                    f"Revenue estimated from attendance x ${DEFAULT_PASS_PRICE} avg pass — verify")
            db.add(Festival(
                name=c["name"],
                name_key=key,
                website=c.get("website"),
                city=c.get("city"),
                state=c.get("state"),
                dates=c.get("dates"),
                start_month=c.get("start_month"),
                ticket_price_min=c.get("price_min"),
                ticket_price_max=c.get("price_max"),
                est_attendance=attendance,
                est_revenue=est,
                ticketing_platform=c.get("platform"),
                source="scraped",
                needs_review=True,
                notes="; ".join(note_bits),
            ))
            added += 1

        db.commit()
        log.status = "ok"
        log.found = len(candidates)
        log.added = added
        log.message = (f"{len(candidates)} new candidates; {added} added "
                       f"({added - review_added} revenue-qualified, "
                       f"{review_added} to review queue); {tixr_note}")
    except Exception as e:  # keep the log row honest rather than crashing the app
        db.rollback()
        log.status = "error"
        log.message = f"{type(e).__name__}: {e}"
    finally:
        log.finished_at = datetime.utcnow()
        db.commit()
        db.close()
    return log
