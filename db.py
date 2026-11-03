"""
db.py — Data layer for the TiaMD Scheduler POC.

Mirrors (in miniature) the Practice / Location / Provider structure that
already exists in TiaMD. Uses SQLite for the POC; swap get_conn() for a
Postgres connection later without touching the calling code.
"""

import sqlite3
from datetime import date, timedelta
from contextlib import contextmanager

DB_PATH = "tiamd_scheduler.db"


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS practices (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS providers (
                id INTEGER PRIMARY KEY,
                practice_id INTEGER NOT NULL REFERENCES practices(id),
                full_name TEXT NOT NULL,
                initials TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS locations (
                id INTEGER PRIMARY KEY,
                practice_id INTEGER NOT NULL REFERENCES practices(id),
                name TEXT NOT NULL,
                acronym TEXT NOT NULL
            );

            -- which providers are allowed to round at which locations
            CREATE TABLE IF NOT EXISTS provider_location_map (
                provider_id INTEGER NOT NULL REFERENCES providers(id),
                location_id INTEGER NOT NULL REFERENCES locations(id),
                PRIMARY KEY (provider_id, location_id)
            );

            -- one row = one provider, one half-day, one location, one date
            -- UNIQUE constraint on (provider, date, half_day) is what
            -- prevents double-booking the same provider twice in one half-day
            CREATE TABLE IF NOT EXISTS schedule_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id INTEGER NOT NULL REFERENCES providers(id),
                location_id INTEGER NOT NULL REFERENCES locations(id),
                entry_date TEXT NOT NULL,          -- 'YYYY-MM-DD'
                half_day TEXT NOT NULL CHECK(half_day IN ('AM', 'PM')),
                source TEXT DEFAULT 'voice',        -- voice | copy | recurring
                UNIQUE(provider_id, entry_date, half_day)
            );
            """
        )


def seed_mock_data():
    """Seeds one mock practice so the POC runs standalone. Safe to call
    repeatedly — it's a no-op if data already exists."""
    with get_conn() as conn:
        if conn.execute("SELECT COUNT(*) FROM practices").fetchone()[0] > 0:
            return

        conn.execute("INSERT INTO practices (id, name) VALUES (1, 'Riverside Medical Group')")

        providers = [
            (1, 1, "Dr. Sarah Khan", "SK"),
            (2, 1, "Dr. Michael Rao", "MR"),
            (3, 1, "Dr. Emily Chen", "EC"),
            (4, 1, "Dr. James Ortiz", "JO"),
        ]
        conn.executemany(
            "INSERT INTO providers (id, practice_id, full_name, initials) VALUES (?,?,?,?)",
            providers,
        )

        locations = [
            (1, 1, "City General Hospital", "CGH"),
            (2, 1, "Riverside Clinic", "RVC"),
            (3, 1, "Lakeside Hospital", "LKH"),
        ]
        conn.executemany(
            "INSERT INTO locations (id, practice_id, name, acronym) VALUES (?,?,?,?)",
            locations,
        )

        mapping = [
            (1, 1), (1, 2),
            (2, 1), (2, 3),
            (3, 2), (3, 3),
            (4, 1), (4, 2), (4, 3),
        ]
        conn.executemany(
            "INSERT INTO provider_location_map (provider_id, location_id) VALUES (?,?)",
            mapping,
        )


# ---------- lookups ----------

def get_providers(practice_id=1):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, full_name, initials FROM providers WHERE practice_id=? ORDER BY full_name",
            (practice_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_locations(practice_id=1):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, acronym FROM locations WHERE practice_id=? ORDER BY name",
            (practice_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def is_mapped(provider_id, location_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM provider_location_map WHERE provider_id=? AND location_id=?",
            (provider_id, location_id),
        ).fetchone()
        return row is not None


# ---------- schedule entries ----------

def add_entry(provider_id, location_id, entry_date, half_day, source="voice"):
    """entry_date must be an ISO string 'YYYY-MM-DD'. Returns (success, message)."""
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO schedule_entries (provider_id, location_id, entry_date, half_day, source) "
                "VALUES (?,?,?,?,?)",
                (provider_id, location_id, entry_date, half_day, source),
            )
            return True, "added"
        except sqlite3.IntegrityError:
            return False, "conflict: provider already booked that date/half"


def get_month_entries(year, month):
    start = f"{year:04d}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1:04d}-01-01"
    else:
        end = f"{year:04d}-{month + 1:02d}-01"
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT se.id, se.entry_date, se.half_day, se.provider_id, se.location_id,
                   p.initials AS provider_initials, p.full_name AS provider_name,
                   l.acronym AS location_acronym, l.name AS location_name
            FROM schedule_entries se
            JOIN providers p ON p.id = se.provider_id
            JOIN locations l ON l.id = se.location_id
            WHERE se.entry_date >= ? AND se.entry_date < ?
            ORDER BY se.entry_date, se.half_day
            """,
            (start, end),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- speed-up features: copy day, copy single entry, recurring rule ----------

def copy_day(source_date, target_dates):
    """Copies every entry on source_date onto each date in target_dates.
    Returns a list of (target_date, provider_id, half_day, success, message)."""
    with get_conn() as conn:
        source_entries = conn.execute(
            "SELECT provider_id, location_id, half_day FROM schedule_entries WHERE entry_date=?",
            (source_date,),
        ).fetchall()
        results = []
        for t in target_dates:
            for e in source_entries:
                try:
                    conn.execute(
                        "INSERT INTO schedule_entries (provider_id, location_id, entry_date, half_day, source) "
                        "VALUES (?,?,?,?, 'copy')",
                        (e["provider_id"], e["location_id"], t, e["half_day"]),
                    )
                    results.append((t, e["provider_id"], e["half_day"], True, "copied"))
                except sqlite3.IntegrityError:
                    results.append((t, e["provider_id"], e["half_day"], False, "conflict - skipped"))
        return results


def copy_entry(entry_id, target_dates):
    """Copies a single existing schedule entry onto each date in target_dates.
    Returns a list of (target_date, success, message)."""
    with get_conn() as conn:
        e = conn.execute(
            "SELECT provider_id, location_id, half_day FROM schedule_entries WHERE id=?",
            (entry_id,),
        ).fetchone()
        if e is None:
            return []
        results = []
        for t in target_dates:
            try:
                conn.execute(
                    "INSERT INTO schedule_entries (provider_id, location_id, entry_date, half_day, source) "
                    "VALUES (?,?,?,?, 'copy')",
                    (e["provider_id"], e["location_id"], t, e["half_day"]),
                )
                results.append((t, True, "copied"))
            except sqlite3.IntegrityError:
                results.append((t, False, "conflict - skipped"))
        return results


def add_recurring(provider_id, location_id, half_day, weekdays, start_date, end_date):
    """weekdays: iterable of ints, Monday=0 .. Sunday=6 (matches date.weekday()).
    start_date/end_date: date objects, inclusive.
    Returns a list of (date_iso, success, message)."""
    results = []
    weekdays = set(weekdays)
    with get_conn() as conn:
        d = start_date
        while d <= end_date:
            if d.weekday() in weekdays:
                try:
                    conn.execute(
                        "INSERT INTO schedule_entries (provider_id, location_id, entry_date, half_day, source) "
                        "VALUES (?,?,?,?, 'recurring')",
                        (provider_id, location_id, d.isoformat(), half_day),
                    )
                    results.append((d.isoformat(), True, "added"))
                except sqlite3.IntegrityError:
                    results.append((d.isoformat(), False, "conflict - skipped"))
            d += timedelta(days=1)
    return results
