import os
import re
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker

_db_path = os.environ.get("DB_PATH", "./festival_crm.db")
DATABASE_URL = f"sqlite:///{_db_path}"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

QUALIFYING_REVENUE = 2_000_000


def normalize_name(name: str) -> str:
    """Key used to dedupe festivals across sources: lowercase, alphanumeric only,
    with noise words (festival/fest/music/the) stripped so 'Bonnaroo Music & Arts
    Festival' and 'Bonnaroo' collide."""
    n = re.sub(r"[^a-z0-9 ]", "", (name or "").lower())
    n = re.sub(r"\b(music|arts|and|the|festival|fest)\b", "", n)
    return re.sub(r"\s+", "", n)


class Festival(Base):
    __tablename__ = "festivals"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    name_key = Column(String, index=True)
    website = Column(String)
    city = Column(String)
    state = Column(String)
    dates = Column(String)          # human-readable, e.g. "Apr 10–12 & 17–19, 2026"
    start_month = Column(Integer)   # 1-12, used for date sorting
    days = Column(Integer)
    ticket_price_min = Column(Float)
    ticket_price_max = Column(Float)
    est_attendance = Column(Integer)   # approx unique attendees / passes sold per year
    est_revenue = Column(Float)        # computed estimate (attendance x avg pass price)
    revenue_override = Column(Float)   # manual entry; always wins over est_revenue
    contacts = Column(Text)
    ticketing_platform = Column(String)
    platform_since_year = Column(Integer)
    in_salesforce = Column(Boolean, default=False)
    source = Column(String, default="manual")  # seed | scraped | manual
    needs_review = Column(Boolean, default=False)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def effective_revenue(self):
        return self.revenue_override if self.revenue_override is not None else self.est_revenue

    @property
    def qualified(self):
        rev = self.effective_revenue
        return rev is not None and rev >= QUALIFYING_REVENUE


class Prospect(Base):
    __tablename__ = "prospects"
    id = Column(Integer, primary_key=True, index=True)
    festival_id = Column(Integer)   # optional link back to a festival row
    name = Column(String, nullable=False)
    website = Column(String)
    stage = Column(String, default="researching")
    # researching | outreach | meeting | negotiating | closed_won | closed_lost
    priority = Column(String, default="medium")  # high | medium | low
    ticketing_platform = Column(String)  # copied from the festival, editable
    next_step = Column(String)
    next_step_date = Column(String)
    contacts = Column(Text)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AppMeta(Base):
    """Marker rows for one-time data migrations."""
    __tablename__ = "app_meta"
    key = Column(String, primary_key=True)
    value = Column(Text)


class ScrapeLog(Base):
    __tablename__ = "scrape_logs"
    id = Column(Integer, primary_key=True, index=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime)
    status = Column(String, default="running")  # running | ok | error
    found = Column(Integer, default=0)
    added = Column(Integer, default=0)
    message = Column(Text)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
    _migrate_columns()


def _migrate_columns():
    """Add columns introduced after a database was first created. SQLAlchemy's
    create_all only makes missing tables, not missing columns, so new fields on
    existing tables (e.g. a redeploy onto an existing volume) need a light
    ALTER. Safe to run every boot — each add is guarded by a column check."""
    from sqlalchemy import inspect, text
    wanted = {
        "prospects": [("ticketing_platform", "VARCHAR")],
    }
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, cols in wanted.items():
            if not inspector.has_table(table):
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, coltype in cols:
                if name not in existing:
                    conn.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN {name} {coltype}"))


def apply_cleanups(db) -> int:
    """One-time removal of festivals on major (Live Nation/AEG) ticketing
    platforms — AXS, Ticketmaster, Front Gate Tickets — which aren't sales
    prospects. Runs once per database (tracked in app_meta), so festivals a
    user later tags with these platforms are left alone."""
    marker = "remove_axs_ticketmaster_frontgate_v1"
    if db.get(AppMeta, marker):
        return 0
    removed = 0
    for f in db.query(Festival).all():
        pl = re.sub(r"[^a-z]", "", (f.ticketing_platform or "").lower())
        if pl == "axs" or "ticketmaster" in pl or "frontgate" in pl:
            db.delete(f)
            removed += 1
    db.add(AppMeta(key=marker, value=f"removed {removed}"))
    db.commit()
    return removed
