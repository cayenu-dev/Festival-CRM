import asyncio
import hashlib
import hmac
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import (Festival, Prospect, ScrapeLog, apply_cleanups, get_db,
                      init_db, normalize_name)
from platforms import detect_for_festivals
from scraper import run_scrape
from seed_data import seed_festivals

load_dotenv()

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
SCRAPE_HOUR_UTC = int(os.environ.get("SCRAPE_HOUR_UTC", "13"))  # 13 UTC ≈ 6am PT

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    db = next(get_db())
    seed_festivals(db)
    apply_cleanups(db)
    db.close()
    scheduler.add_job(run_scrape, CronTrigger(hour=SCRAPE_HOUR_UTC, minute=0),
                      id="daily_scrape", replace_existing=True)
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Festival CRM", lifespan=lifespan)


# ── Auth (single shared password via APP_PASSWORD env; open if unset) ─────────

def _session_token() -> str:
    return hmac.new(APP_PASSWORD.encode(), b"festival-crm-session",
                    hashlib.sha256).hexdigest()


def _authed(request: Request) -> bool:
    if not APP_PASSWORD:
        return True
    return hmac.compare_digest(
        request.cookies.get("fcrm_session", ""), _session_token())


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if (path.startswith("/api") and path not in ("/api/login", "/api/auth")
            and not _authed(request)):
        return JSONResponse({"detail": "auth required"}, status_code=401)
    return await call_next(request)


class LoginBody(BaseModel):
    password: str


@app.get("/api/auth")
def auth_state(request: Request):
    return {"password_required": bool(APP_PASSWORD), "authed": _authed(request)}


@app.post("/api/login")
def login(body: LoginBody, response: Response):
    if not APP_PASSWORD:
        return {"ok": True}
    if not hmac.compare_digest(body.password, APP_PASSWORD):
        raise HTTPException(401, "Wrong password")
    response.set_cookie("fcrm_session", _session_token(), httponly=True,
                        samesite="lax", max_age=60 * 60 * 24 * 30)
    return {"ok": True}


# ── Festivals ──────────────────────────────────────────────────────────────────

class FestivalBody(BaseModel):
    name: Optional[str] = None
    website: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    dates: Optional[str] = None
    start_month: Optional[int] = None
    days: Optional[int] = None
    ticket_price_min: Optional[float] = None
    ticket_price_max: Optional[float] = None
    est_attendance: Optional[int] = None
    est_revenue: Optional[float] = None
    revenue_override: Optional[float] = None
    contacts: Optional[str] = None
    ticketing_platform: Optional[str] = None
    platform_since_year: Optional[int] = None
    in_salesforce: Optional[bool] = None
    needs_review: Optional[bool] = None
    notes: Optional[str] = None


def _festival_dict(f: Festival) -> dict:
    return {
        "id": f.id, "name": f.name, "website": f.website,
        "city": f.city, "state": f.state, "dates": f.dates,
        "start_month": f.start_month, "days": f.days,
        "ticket_price_min": f.ticket_price_min,
        "ticket_price_max": f.ticket_price_max,
        "est_attendance": f.est_attendance,
        "est_revenue": f.est_revenue,
        "revenue_override": f.revenue_override,
        "effective_revenue": f.effective_revenue,
        "qualified": f.qualified,
        "contacts": f.contacts,
        "ticketing_platform": f.ticketing_platform,
        "platform_since_year": f.platform_since_year,
        "in_salesforce": bool(f.in_salesforce),
        "source": f.source, "needs_review": bool(f.needs_review),
        "notes": f.notes,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


def _maybe_compute_revenue(f: Festival):
    """If no explicit estimate/override, derive one from attendance x price."""
    if f.est_revenue is None and f.revenue_override is None and f.est_attendance:
        pmin, pmax = f.ticket_price_min, f.ticket_price_max
        if pmin and pmax:
            price = 0.8 * pmin + 0.2 * pmax
        else:
            price = pmin or pmax
        if price:
            f.est_revenue = round(f.est_attendance * price)


@app.get("/api/festivals")
def list_festivals(db: Session = Depends(get_db)):
    rows = db.query(Festival).all()
    return [_festival_dict(f) for f in rows]


@app.post("/api/festivals")
def create_festival(body: FestivalBody, db: Session = Depends(get_db)):
    if not body.name or not body.name.strip():
        raise HTTPException(422, "Name is required")
    f = Festival(source="manual")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(f, k, v)
    f.name_key = normalize_name(f.name)
    _maybe_compute_revenue(f)
    db.add(f)
    db.commit()
    db.refresh(f)
    return _festival_dict(f)


@app.patch("/api/festivals/{fid}")
def update_festival(fid: int, body: FestivalBody, db: Session = Depends(get_db)):
    f = db.get(Festival, fid)
    if not f:
        raise HTTPException(404, "Festival not found")
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(f, k, v)
    if "name" in data and f.name:
        f.name_key = normalize_name(f.name)
    _maybe_compute_revenue(f)
    db.commit()
    db.refresh(f)
    return _festival_dict(f)


@app.delete("/api/festivals/{fid}")
def delete_festival(fid: int, db: Session = Depends(get_db)):
    f = db.get(Festival, fid)
    if not f:
        raise HTTPException(404, "Festival not found")
    db.delete(f)
    db.commit()
    return {"ok": True}


# ── Prospects ──────────────────────────────────────────────────────────────────

class ProspectBody(BaseModel):
    festival_id: Optional[int] = None
    name: Optional[str] = None
    website: Optional[str] = None
    stage: Optional[str] = None
    priority: Optional[str] = None
    next_step: Optional[str] = None
    next_step_date: Optional[str] = None
    contacts: Optional[str] = None
    notes: Optional[str] = None


def _prospect_dict(p: Prospect) -> dict:
    return {
        "id": p.id, "festival_id": p.festival_id, "name": p.name,
        "website": p.website, "stage": p.stage, "priority": p.priority,
        "next_step": p.next_step, "next_step_date": p.next_step_date,
        "contacts": p.contacts, "notes": p.notes,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


@app.get("/api/prospects")
def list_prospects(db: Session = Depends(get_db)):
    return [_prospect_dict(p) for p in db.query(Prospect).all()]


@app.post("/api/prospects")
def create_prospect(body: ProspectBody, db: Session = Depends(get_db)):
    data = body.model_dump(exclude_unset=True)
    if body.festival_id and not body.name:
        f = db.get(Festival, body.festival_id)
        if not f:
            raise HTTPException(404, "Festival not found")
        existing = db.query(Prospect).filter(
            Prospect.festival_id == body.festival_id).first()
        if existing:
            return _prospect_dict(existing)
        data.setdefault("name", f.name)
        data.setdefault("website", f.website)
        data.setdefault("contacts", f.contacts)
    if not data.get("name"):
        raise HTTPException(422, "Name is required")
    p = Prospect(**data)
    db.add(p)
    db.commit()
    db.refresh(p)
    return _prospect_dict(p)


@app.patch("/api/prospects/{pid}")
def update_prospect(pid: int, body: ProspectBody, db: Session = Depends(get_db)):
    p = db.get(Prospect, pid)
    if not p:
        raise HTTPException(404, "Prospect not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return _prospect_dict(p)


@app.delete("/api/prospects/{pid}")
def delete_prospect(pid: int, db: Session = Depends(get_db)):
    p = db.get(Prospect, pid)
    if not p:
        raise HTTPException(404, "Prospect not found")
    db.delete(p)
    db.commit()
    return {"ok": True}


# ── Scraper ────────────────────────────────────────────────────────────────────

_scrape_task: Optional[asyncio.Task] = None


@app.post("/api/scrape/run")
async def trigger_scrape():
    global _scrape_task
    if _scrape_task and not _scrape_task.done():
        return {"status": "already_running"}
    _scrape_task = asyncio.create_task(run_scrape())
    return {"status": "started"}


_detect_task: Optional[asyncio.Task] = None
_detect_state = {"running": False, "checked": 0, "found": 0, "message": ""}


async def _run_detect(only_missing: bool):
    """Detect ticketing platforms for review-queue festivals, persist results."""
    db = next(get_db())
    try:
        q = db.query(Festival).filter(Festival.needs_review == True)  # noqa: E712
        if only_missing:
            q = q.filter((Festival.ticketing_platform == None)         # noqa: E711
                         | (Festival.ticketing_platform == ""))
        targets = [f for f in q.all() if f.website]
        _detect_state.update(running=True, checked=0, found=0,
                             message=f"Checking {len(targets)} festivals…")
        results = await detect_for_festivals(targets)
        for fid, platform in results.items():
            f = db.get(Festival, fid)
            if f and (not only_missing or not f.ticketing_platform):
                f.ticketing_platform = platform
                note = f"Platform detected from website: {platform}"
                f.notes = ((f.notes + "; ") if f.notes else "") + note
        db.commit()
        _detect_state.update(
            running=False, checked=len(targets), found=len(results),
            message=f"Detected platform for {len(results)} of {len(targets)} festivals.")
    except Exception as e:
        db.rollback()
        _detect_state.update(running=False,
                             message=f"Detection failed: {type(e).__name__}: {e}")
    finally:
        db.close()


@app.post("/api/festivals/detect-platforms")
async def detect_platforms(only_missing: bool = True):
    global _detect_task
    if _detect_task and not _detect_task.done():
        return {"status": "already_running"}
    _detect_task = asyncio.create_task(_run_detect(only_missing))
    return {"status": "started"}


@app.get("/api/festivals/detect-platforms/status")
def detect_platforms_status():
    return _detect_state


@app.get("/api/scrape/logs")
def scrape_logs(db: Session = Depends(get_db)):
    logs = (db.query(ScrapeLog).order_by(ScrapeLog.started_at.desc())
            .limit(10).all())
    return [{
        "id": l.id,
        "started_at": l.started_at.isoformat() if l.started_at else None,
        "finished_at": l.finished_at.isoformat() if l.finished_at else None,
        "status": l.status, "found": l.found, "added": l.added,
        "message": l.message,
    } for l in logs]


# ── Static ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=FileResponse)
def serve():
    return FileResponse("static/index.html")
