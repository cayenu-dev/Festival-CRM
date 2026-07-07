"""Daily discovery scrape for new U.S. music festivals.

Sources:
  1. Music Festival Wizard's US festival guide (names, locations, dates)
  2. Wikipedia's "List of music festivals in the United States" (names), with a
     follow-up fetch of each new festival's article to pull attendance/website
     from the infobox so revenue can be estimated.

Rules:
  - Festivals already in the DB (matched on normalized name) are skipped.
  - If attendance is found, revenue is estimated (attendance x avg pass price,
    defaulting to a conservative $200/pass when prices are unknown). Estimates
    >= $2M land in the main list, flagged needs_review until confirmed.
  - Candidates with no revenue signal go to the review queue (needs_review,
    no revenue), capped per run so a first scrape doesn't flood the table.
"""

import asyncio
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

        # -- Insert --------------------------------------------------------
        added = review_added = 0
        for key, c in candidates.items():
            attendance = c.get("attendance")
            est = _estimate_revenue(attendance, None, None)
            qualified = est is not None and est >= QUALIFYING_REVENUE
            if not qualified:
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
                est_attendance=attendance,
                est_revenue=est,
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
                       f"({added - review_added} revenue-qualified, {review_added} to review queue)")
    except Exception as e:  # keep the log row honest rather than crashing the app
        db.rollback()
        log.status = "error"
        log.message = f"{type(e).__name__}: {e}"
    finally:
        log.finished_at = datetime.utcnow()
        db.commit()
        db.close()
    return log
