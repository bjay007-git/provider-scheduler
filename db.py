"""
db.py — Data layer for the TiaMD Scheduler POC.

Mirrors (in miniature) the Practice / Location / Provider structure that
already exists in TiaMD. Uses SQLite for the POC; swap get_conn() for a
Postgres connection later without touching the calling code.
"""

import sqlite3
from datetime import date, timedelta
from contextlib import contextmanager
from dataclasses import dataclass

from scheduling import FULL_DAY, group_assignments

DB_PATH = "tiamd_scheduler.db"


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
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

            -- Durable submission receipts; written in the schedule transaction.
            CREATE TABLE IF NOT EXISTS approved_batches (
                batch_id TEXT PRIMARY KEY,
                payload_hash TEXT NOT NULL,
                approved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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

@dataclass(frozen=True)
class WriteResult:
    entry_date: str
    status: str  # added | conflict | invalid | error
    message: str
    provider_id: int | None = None
    session: str | None = None


def invalid_result(message, entry_date="", provider_id=None, session=None):
    return WriteResult(str(entry_date), "invalid", message, provider_id, session)


def _real_date(value):
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() == value:
                return parsed
        except ValueError:
            pass
    raise ValueError("Use a real date in YYYY-MM-DD format.")


def _validate_assignment(conn, provider_id, location_id, entry_date, session):
    day = _real_date(entry_date)
    if session not in ("AM", "PM", FULL_DAY):
        raise ValueError("Session must be AM, PM, or Full day.")
    if type(provider_id) is not int or type(location_id) is not int:
        raise ValueError("Provider and location IDs must be integers.")
    provider = conn.execute("SELECT practice_id FROM providers WHERE id=?", (provider_id,)).fetchone()
    location = conn.execute("SELECT practice_id FROM locations WHERE id=?", (location_id,)).fetchone()
    if provider is None:
        raise ValueError("Provider does not exist.")
    if location is None:
        raise ValueError("Location does not exist.")
    if provider["practice_id"] != location["practice_id"]:
        raise ValueError("Provider and location must belong to the same practice.")
    if not conn.execute(
        "SELECT 1 FROM provider_location_map WHERE provider_id=? AND location_id=?",
        (provider_id, location_id),
    ).fetchone():
        raise ValueError("Provider is not allowed at this location.")
    return day


def _sessions(session):
    return ("AM", "PM") if session == FULL_DAY else (session,)


def _booking_conflict(conn, provider_id, entry_date, session):
    bookings = {row["half_day"]: row["location_id"] for row in conn.execute(
        "SELECT half_day, location_id FROM schedule_entries WHERE provider_id=? AND entry_date=?",
        (provider_id, entry_date),
    )}
    conflicts = [half for half in _sessions(session) if half in bookings]
    if not conflicts:
        return None
    if set(bookings) == {"AM", "PM"}:
        booked_session = "Full day (AM and PM)" if bookings["AM"] == bookings["PM"] else "AM and PM"
    else:
        booked_session = conflicts[0]
    return f"Provider already booked for {booked_session}."


def _insert_assignment(conn, provider_id, location_id, entry_date, session, source):
    """Shared insert primitive. Validation always runs in the caller's transaction."""
    day = _validate_assignment(conn, provider_id, location_id, entry_date, session)
    conn.executemany(
        "INSERT INTO schedule_entries (provider_id, location_id, entry_date, half_day, source) "
        "VALUES (?,?,?,?,?)",
        [(provider_id, location_id, day.isoformat(), half, source) for half in _sessions(session)],
    )


def add_entry(provider_id, location_id, entry_date, half_day, source="voice"):
    """Write one logical assignment. A full day commits both sessions or neither.

    Session is explicit here; only the ADD parser defaults omitted sessions.
    Every write path, including copies and recurrence, calls this function.
    """
    try:
        with get_conn() as conn:
            # Lock before validation and conflict checking; keep the UNIQUE constraint
            # as the final safeguard. A raised exception rolls back on close.
            conn.execute("BEGIN IMMEDIATE")
            day = _validate_assignment(conn, provider_id, location_id, entry_date, half_day)
            conflict = _booking_conflict(conn, provider_id, day.isoformat(), half_day)
            if conflict:
                return WriteResult(day.isoformat(), "conflict", conflict + " Assignment skipped.",
                                   provider_id, half_day)
            _insert_assignment(conn, provider_id, location_id, day, half_day, source)
        return WriteResult(day.isoformat(), "added", "Assignment added.", provider_id, half_day)
    except ValueError as exc:
        return invalid_result(str(exc), entry_date, provider_id, half_day)
    except sqlite3.Error as exc:
        # Constraint failures other than the known booking key are database errors.
        return WriteResult(str(entry_date), "error", f"Database error: {exc}", provider_id, half_day)


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

def _source_assignments(source_date=None, entry_id=None):
    with get_conn() as conn:
        if entry_id is not None:
            row = conn.execute("SELECT entry_date FROM schedule_entries WHERE id=?", (entry_id,)).fetchone()
            if row is None:
                raise ValueError("Source assignment no longer exists.")
            source_date = row["entry_date"]
        day = _real_date(source_date)
        rows = conn.execute("SELECT * FROM schedule_entries WHERE entry_date=?", (day.isoformat(),)).fetchall()
        assignments = group_assignments([dict(row) for row in rows])
        if entry_id is not None:
            assignments = [a for a in assignments if entry_id in a["entry_ids"]]
        return assignments


def _copy(assignments, target_dates):
    return [add_entry(a["provider_id"], a["location_id"], target, a["half_day"], source="copy")
            for target in target_dates for a in assignments]


def copy_day(source_date, target_dates):
    """Copy logical assignments; matching AM+PM rows stay atomic."""
    try:
        return _copy(_source_assignments(source_date=source_date), target_dates)
    except ValueError as exc:
        return [invalid_result(str(exc))]
    except sqlite3.Error as exc:
        return [WriteResult("", "error", f"Database error: {exc}")]


def copy_entry(entry_id, target_dates):
    """An ID from either half of a displayed full day copies both sessions."""
    try:
        return _copy(_source_assignments(entry_id=entry_id), target_dates)
    except ValueError as exc:
        return [invalid_result(str(exc))]
    except sqlite3.Error as exc:
        return [WriteResult("", "error", f"Database error: {exc}")]


def _recurrence_dates(weekdays, start_date, end_date):
    start, end = _real_date(start_date), _real_date(end_date)
    if start > end:
        raise ValueError("Recurrence start must be on or before its end.")
    try:
        days = list(weekdays)
    except TypeError:
        raise ValueError("Weekdays must be a nonempty collection of integers from 0 to 6.") from None
    if not days or any(type(d) is not int or not 0 <= d <= 6 for d in days):
        raise ValueError("Weekdays must be a nonempty collection of integers from 0 to 6.")
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1)
                 if (start + timedelta(days=offset)).weekday() in days)


def add_recurring(provider_id, location_id, half_day, weekdays, start_date, end_date):
    """Stage 1 API: expand an inclusive range, returning each occurrence's result."""
    try:
        dates = _recurrence_dates(weekdays, start_date, end_date)
        with get_conn() as conn:
            _validate_assignment(conn, provider_id, location_id, start_date, half_day)
        return [add_entry(provider_id, location_id, day, half_day, source="recurring") for day in dates]
    except ValueError as exc:
        return [invalid_result(str(exc), start_date, provider_id, half_day)]
    except sqlite3.Error as exc:
        return [WriteResult(str(start_date), "error", f"Database error: {exc}", provider_id, half_day)]
