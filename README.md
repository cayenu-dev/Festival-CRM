# Festival CRM

A sales CRM focused on U.S. music festivals. FastAPI + SQLite backend, single-page
minimalist frontend.

## What it does

- **Festivals tab** — sortable database of U.S. music festivals: name, website,
  dates, ticket prices, estimated revenue, contacts, ticketing platform, platform
  tenure, and an inline **In Salesforce?** yes/no dropdown. Filter chips for
  Qualified ($2M+), Needs review, and Salesforce status. Click any row to edit
  everything (including a manual revenue override, which always wins over the
  estimate). Ships pre-seeded with curated U.S. festivals; the seed list tops
  itself up on deploy (matched by name, never overwriting your edits).
  Festivals ticketed by the majors (AXS, Ticketmaster, Front Gate) are
  excluded as non-prospects — a one-time boot cleanup also removes any that
  were previously seeded. Festivals you tag with those platforms afterwards
  are left alone.
- **Prospecting tab** — accounts you're actively working. Add manually or hit
  **+ Prospect** on any festival row. Inline-editable stage (Researching →
  Outreach → Meeting → Negotiating → Closed), priority, next step, and notes.
- **Detect ticketing platforms** — in the **Needs review** filter, a button
  scans each festival's own website (and its tickets/buy pages) for the
  fingerprints of ticketing providers (Tixr, See Tickets, DICE, Eventbrite,
  Etix, ShowClix, and more) and fills in the platform column. Conservative:
  it only writes a platform on a real match, leaving unknowns blank.
- **Export CSV** — the header button exports the current tab to CSV. On the
  Festivals tab it respects the active filter and search (e.g. export just the
  "Needs review" set, or everything "In Salesforce"); on Prospecting it exports
  your pipeline. Opens straight in Excel/Google Sheets.
- **Daily scraper** — runs automatically once a day (and on demand via the
  status bar). Discovers new festivals from Music Festival Wizard's US guide,
  Wikipedia's list of U.S. music festivals, and Tixr's public sitemap (US
  festival events, auto-tagged `ticketing_platform = Tixr`; existing festivals
  with no platform get tagged when found there). It dedupes against the
  database, estimates revenue from attendance where available, and:
  - adds revenue-qualified finds (est. ≥ $2M) to the main list flagged **review**
  - queues the rest in the **Needs review** filter (capped per run; Tixr finds
    are exempt from the cap)

## Data honesty

Seeded revenue/attendance figures are estimates assembled from public reporting —
good enough for territory planning, verify before quoting. Ticketing platform
entries marked "verify" in notes are informed guesses. Scraped revenue estimates
use `attendance × ~$200 avg pass` when prices are unknown and are always flagged
for review.

## Deploy on Railway

1. New Project → **Deploy from GitHub repo** → pick this repo.
2. Add a **Volume** to the service, mounted at `/data` (SQLite must live on a
   volume or your data is wiped on every deploy).
3. Set variables:
   - `DB_PATH=/data/festival_crm.db`
   - `APP_PASSWORD=<pick a password>` — required for a work database on a public
     URL. Without it the site is open to anyone who finds the link.
   - optional `SCRAPE_HOUR_UTC` (default `13` ≈ 6am PT)
4. Generate a domain under Settings → Networking. That URL works from any
   machine, including your work laptop — just enter the password.

## Run locally

```bash
pip install -r requirements.txt
uvicorn main:app --reload
# open http://localhost:8000
```

## API

`/api/festivals` (GET/POST), `/api/festivals/{id}` (PATCH/DELETE),
`/api/prospects` (same shape), `/api/scrape/run` (POST),
`/api/scrape/logs` (GET), `/api/login` (POST), `/api/auth` (GET).
