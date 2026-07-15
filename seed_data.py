"""Curated starter list of U.S. music festivals (est. annual revenue > $2M).

Festivals ticketed by the major platforms (AXS, Ticketmaster, Front Gate
Tickets) are intentionally excluded — they're locked into Live Nation/AEG-owned
ticketing and aren't prospects. A one-time boot cleanup (database.apply_cleanups)
also removes any previously seeded rows on those platforms.

Figures are best-effort estimates assembled from public reporting (attendance,
published pass prices). They are meant as a working starting point for sales
research — verify before quoting externally. Ticketing platform entries marked
"verify" in notes are informed guesses; everything is editable in the UI.
"""

from database import Festival, normalize_name

# (name, website, city, state, dates, start_month, days,
#  price_min, price_max, attendance, est_revenue, platform, since_year, notes)
SEED_FESTIVALS = [
    ("Ultra Music Festival", "https://ultramusicfestival.com", "Miami", "FL",
     "Late March", 3, 3, 449, 1650, 55000, 28_000_000,
     None, None, "Ticketing platform: verify"),
    ("Outside Lands", "https://www.sfoutsidelands.com", "San Francisco", "CA",
     "Early Aug", 8, 3, 469, 1690, 80000, 40_000_000,
     "Eventbrite", 2016, "Platform: best guess — verify"),
    ("Lost Lands", "https://www.lostlandsfestival.com", "Thornville", "OH",
     "Late Sept", 9, 3, 330, 900, 45000, 16_000_000,
     None, None, "Ticketing platform: verify"),
    ("BottleRock Napa Valley", "https://www.bottlerocknapavalley.com", "Napa", "CA",
     "Late May", 5, 3, 459, 1900, 40000, 25_000_000,
     None, None, "Ticketing platform: verify"),
    ("Lightning in a Bottle", "https://libfestival.org", "Buena Vista Lake", "CA",
     "Late May (Memorial Day wknd)", 5, 4, 389, 800, 25000, 10_000_000,
     None, None, "Ticketing platform: verify"),
    ("Movement", "https://www.movementfestival.com", "Detroit", "MI",
     "Late May (Memorial Day wknd)", 5, 3, 249, 600, 35000, 9_000_000,
     None, None, "Ticketing platform: verify"),
    ("iii Points", "https://www.iiipoints.com", "Miami", "FL",
     "Mid-Oct", 10, 2, 259, 500, 30000, 8_000_000,
     None, None, "Ticketing platform: verify"),
    ("North Coast", "https://www.northcoastfestival.com", "Chicago", "IL",
     "Labor Day weekend (early Sept)", 9, 3, 229, 500, 30000, 7_000_000,
     None, None, "Ticketing platform: verify"),

    # ── Electronic ────────────────────────────────────────────────────────
    ("Bass Canyon", "https://www.basscanyon.com", "George", "WA",
     "Late Aug", 8, 3, 330, 700, 25000, 9_000_000,
     None, None, "Excision-run; ticketing platform: verify"),
    ("CRSSD Festival", "https://www.crssdfest.com", "San Diego", "CA",
     "Early March & late Sept (2 editions)", 3, 2, 165, 250, 30000, 6_000_000,
     "See Tickets", 2015, "Platform: best guess — verify"),
    ("ARC Music Festival", "https://arcmusicfestival.com", "Chicago", "IL",
     "Labor Day weekend (early Sept)", 9, 3, 300, 700, 40000, 14_000_000,
     None, None, "Ticketing platform: verify"),
    ("Elements Music & Arts Festival", "https://www.elementsfest.us", "Long Pond", "PA",
     "Early Aug", 8, 4, 340, 700, 20000, 8_000_000,
     None, None, "Ticketing platform: verify"),

    # ── Hip-hop / R&B ─────────────────────────────────────────────────────
    ("Dreamville Festival", "https://www.dreamvillefest.com", "Raleigh", "NC",
     "Early April", 4, 2, 200, 600, 50000, 12_000_000,
     None, None, "Ticketing platform: verify; confirm future editions"),
    ("Broccoli City", "https://www.broccolicity.com", "Washington", "DC",
     "Late July", 7, 2, 180, 450, 40000, 9_000_000,
     None, None, "Ticketing platform: verify"),
    ("ONE Musicfest", "https://www.onemusicfest.com", "Atlanta", "GA",
     "Late Oct", 10, 2, 200, 500, 50000, 12_000_000,
     None, None, "Ticketing platform: verify"),

    # ── Rock ──────────────────────────────────────────────────────────────
    ("Riot Fest", "https://riotfest.org", "Chicago", "IL",
     "Mid-Sept", 9, 3, 270, 600, 60000, 18_000_000,
     None, None, "Ticketing platform: verify"),

    # ── Country ───────────────────────────────────────────────────────────
    ("Country Thunder Arizona", "https://www.countrythunder.com", "Florence", "AZ",
     "Mid-April", 4, 4, 200, 700, 30000, 7_000_000,
     None, None, "Ticketing platform: verify"),
    ("Country Thunder Wisconsin", "https://www.countrythunder.com", "Twin Lakes", "WI",
     "Mid-July", 7, 4, 190, 650, 25000, 5_500_000,
     None, None, "Ticketing platform: verify"),
    ("Country Fest", "https://www.countryfest.com", "Cadott", "WI",
     "Late June", 6, 3, 180, 600, 30000, 6_000_000,
     None, None, "Ticketing platform: verify"),
    ("Rock the South", "https://www.rockthesouth.com", "Cullman", "AL",
     "Mid-July", 7, 3, 150, 500, 60000, 10_000_000,
     None, None, "Ticketing platform: verify"),
    ("Carolina Country Music Fest", "https://carolinacountrymusicfest.com", "Myrtle Beach", "SC",
     "Early June", 6, 4, 260, 800, 35000, 10_000_000,
     None, None, "Ticketing platform: verify"),
    ("Barefoot Country Music Fest", "https://www.barefootcountrymusicfest.com", "Wildwood", "NJ",
     "Mid-June", 6, 4, 250, 800, 35000, 10_000_000,
     None, None, "Ticketing platform: verify"),
    ("Windy City Smokeout", "https://www.windycitysmokeout.com", "Chicago", "IL",
     "Mid-July", 7, 4, 160, 450, 40000, 7_500_000,
     None, None, "Ticketing platform: verify"),
    ("Under the Big Sky", "https://underthebigskyfest.com", "Whitefish", "MT",
     "Mid-July", 7, 3, 300, 700, 25000, 8_500_000,
     None, None, "Ticketing platform: verify"),
    ("Gulf Coast Jam", "https://www.gulfcoastjam.com", "Panama City Beach", "FL",
     "Late May – early June", 6, 4, 200, 600, 30000, 7_000_000,
     None, None, "Ticketing platform: verify"),

    # ── Multi-genre / roots / legacy ──────────────────────────────────────
    ("Newport Folk Festival", "https://newportfolk.org", "Newport", "RI",
     "Late July", 7, 3, 165, 500, 30000, 5_000_000,
     None, None, "Sells out instantly; ticketing platform: verify"),
    ("Telluride Bluegrass Festival", "https://bluegrass.com/telluride", "Telluride", "CO",
     "Mid-June", 6, 4, 120, 400, 40000, 4_500_000,
     None, None, "Planet Bluegrass direct sales; verify"),
    ("Pilgrimage Music & Cultural Festival", "https://pilgrimagefestival.com", "Franklin", "TN",
     "Late Sept", 9, 2, 200, 500, 50000, 11_000_000,
     None, None, "Ticketing platform: verify"),
    ("Moon River", "https://moonriverfestival.com", "Chattanooga", "TN",
     "Early Sept", 9, 2, 200, 450, 30000, 6_500_000,
     None, None, "Ticketing platform: verify"),
    ("Mempho Music Festival", "https://memphofest.com", "Memphis", "TN",
     "Early Oct", 10, 3, 175, 450, 30000, 6_000_000,
     None, None, "Ticketing platform: verify"),
    ("Hinterland", "https://hinterlandiowa.com", "Saint Charles", "IA",
     "Early Aug", 8, 3, 200, 450, 45000, 10_000_000,
     None, None, "Ticketing platform: verify"),
    ("Wonderfront", "https://www.wonderfront.com", "San Diego", "CA",
     "Mid-Nov", 11, 3, 200, 550, 40000, 9_000_000,
     None, None, "Ticketing platform: verify"),
    ("California Roots", "https://californiarootsfestival.com", "Monterey", "CA",
     "Late May (Memorial Day wknd)", 5, 3, 230, 550, 35000, 8_500_000,
     None, None, "Ticketing platform: verify"),
    ("Reggae Rise Up Florida", "https://floridareggaeriseup.com", "St. Petersburg", "FL",
     "Mid-March", 3, 3, 190, 450, 30000, 6_500_000,
     None, None, "Ticketing platform: verify"),
    ("Camp Bisco", "https://www.campbisco.com", "Scranton", "PA",
     "Mid-July", 7, 3, 220, 500, 25000, 6_000_000,
     None, None, "Ticketing platform: verify"),
    ("SunFest", "https://www.sunfest.com", "West Palm Beach", "FL",
     "Early May", 5, 3, 90, 250, 60000, 6_000_000,
     None, None, "Ticketing platform: verify"),
    ("Hulaween", "https://www.suwanneehulaween.com", "Live Oak", "FL",
     "Late Oct", 10, 3, 250, 600, 25000, 7_000_000,
     None, None, "Ticketing platform: verify"),
    ("All Things Go", "https://www.allthingsgofestival.com", "Columbia", "MD",
     "Late Sept", 9, 2, 230, 500, 40000, 9_500_000,
     None, None, "Ticketing platform: verify (Merriweather Post Pavilion)"),
    ("Bumbershoot", "https://www.bumbershoot.com", "Seattle", "WA",
     "Labor Day weekend (early Sept)", 9, 2, 150, 350, 40000, 6_500_000,
     None, None, "Ticketing platform: verify"),
    ("Kilby Block Party", "https://www.kilbyblockparty.com", "Salt Lake City", "UT",
     "Mid-May", 5, 3, 240, 500, 40000, 10_000_000,
     None, None, "Ticketing platform: verify"),
    ("BeachLife Festival", "https://beachlifefestival.com", "Redondo Beach", "CA",
     "Early May", 5, 3, 200, 600, 30000, 7_000_000,
     None, None, "Ticketing platform: verify"),
    ("Monterey Jazz Festival", "https://montereyjazzfestival.org", "Monterey", "CA",
     "Late Sept", 9, 3, 150, 450, 25000, 4_000_000,
     None, None, "Ticketing platform: verify"),
    ("Lovin' Life Music Fest", "https://lovinlifemusicfest.com", "Charlotte", "NC",
     "Early May", 5, 3, 250, 600, 35000, 9_000_000,
     None, None, "Ticketing platform: verify"),
]


def seed_festivals(db):
    """Top up the database with any curated festivals it doesn't have yet.

    Runs on every boot and matches on normalized name, so it never duplicates,
    never overwrites user edits, and new seeds added in later releases appear
    automatically after a redeploy.
    """
    existing = {k for (k,) in db.query(Festival.name_key).all() if k}
    added = 0
    for (name, website, city, state, dates, start_month, days, price_min,
         price_max, attendance, est_revenue, platform, since, notes) in SEED_FESTIVALS:
        if normalize_name(name) in existing:
            continue
        db.add(Festival(
            name=name,
            name_key=normalize_name(name),
            website=website,
            city=city,
            state=state,
            dates=dates,
            start_month=start_month,
            days=days,
            ticket_price_min=price_min,
            ticket_price_max=price_max,
            est_attendance=attendance,
            est_revenue=est_revenue,
            ticketing_platform=platform,
            platform_since_year=since,
            in_salesforce=False,
            source="seed",
            needs_review=False,
            notes=notes,
        ))
        existing.add(normalize_name(name))
        added += 1
    db.commit()
    return added
