"""Immutable proposed actions, read-only previews, and atomic approval.

All entry points (including future parsers) produce the same change set. Only
ADD is currently supported; no command interpretation can execute a write.
"""

from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
import sqlite3
from uuid import uuid4

import db
import command_parser
from scheduling import FULL_DAY, group_assignments


def input_signature(inputs):
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()


@dataclass(frozen=True)
class Assignment:
    action: str
    provider_id: int | None
    location_id: int | None
    entry_date: str
    session: str | None
    source: str = "voice"


@dataclass(frozen=True)
class PreviewRow:
    assignment: Assignment
    provider_name: str
    location_name: str
    status: str
    reason: str
    context: tuple = ()


@dataclass(frozen=True)
class ChangeSet:
    batch_id: str
    operation: str
    input_signature: str
    rows: tuple[PreviewRow, ...]
    issues: tuple[str, ...] = ()
    source_kind: str = ""
    source_value: str | int = ""
    source_snapshot: tuple = ()

    @property
    def can_approve(self):
        return bool(self.rows) and not self.issues and all(row.status == "valid" for row in self.rows)

    @property
    def payload_hash(self):
        payload = asdict(self)
        payload.pop("batch_id")
        return input_signature(payload)


@dataclass(frozen=True)
class ApprovalResult:
    status: str  # approved | rejected | already_applied | error
    message: str
    results: tuple[db.WriteResult, ...] = ()


def _context(conn, assignment):
    provider = conn.execute("SELECT full_name, practice_id, initials FROM providers WHERE id=?",
                            (assignment.provider_id,)).fetchone()
    location = conn.execute("SELECT name, practice_id, acronym FROM locations WHERE id=?",
                            (assignment.location_id,)).fetchone()
    return (tuple(provider) if provider else (), tuple(location) if location else ())


def _review(conn, assignments, error=""):
    rows, seen = [], set()
    for assignment in assignments:
        context = _context(conn, assignment)
        status, reason = "valid", "Ready for approval."
        try:
            if error:
                raise ValueError(error)
            if assignment.action != "ADD":
                raise ValueError("Only ADD actions are supported in this stage.")
            day = db._validate_assignment(conn, assignment.provider_id, assignment.location_id,
                                          assignment.entry_date, assignment.session)
            keys = {(assignment.provider_id, day.isoformat(), half) for half in db._sessions(assignment.session)}
            conflict = db._booking_conflict(conn, assignment.provider_id, day.isoformat(), assignment.session)
            if conflict or keys & seen:
                status, reason = "conflict", conflict or "Overlapping assignments within this proposed batch."
            seen.update(keys)
        except ValueError as exc:
            status, reason = "invalid", str(exc)
        rows.append(PreviewRow(assignment, context[0][0] if context[0] else "Unrecognized provider",
                               context[1][0] if context[1] else "Unrecognized location", status, reason, context))
    return tuple(rows)


def preview_assignments(assignments, operation, inputs, error=""):
    """Common adapter for current and future command interpreters; no writes."""
    try:
        assignments = tuple(assignments)
        limit = command_parser.assignment_limit()
        if len(assignments) > limit:
            raise ValueError(command_parser.limit_message(limit))
        with db.get_conn() as conn:
            conn.execute("BEGIN")
            rows = _review(conn, tuple(assignments), error)
        return ChangeSet(str(uuid4()), operation, input_signature(inputs), rows,
                         () if rows else (error or "No assignments were proposed.",))
    except (sqlite3.Error, ValueError) as exc:
        message = f"Database error: {exc}" if isinstance(exc, sqlite3.Error) else str(exc)
        return ChangeSet(str(uuid4()), operation, input_signature(inputs), (), (message,))


def preview_add(transcript, providers, locations, inputs, today=None):
    today = today or date.today()
    parsed = command_parser.parse_command(transcript, providers, locations, reference_date=today)
    if parsed.error:
        return preview_assignments((), "Add schedule", inputs, parsed.error)
    source = "recurring" if parsed.recurring else "voice"
    assignments = tuple(Assignment("ADD", provider_id, parsed.location_id, day.isoformat(), parsed.session, source)
                        for provider_id in parsed.provider_ids for day in parsed.dates)
    return preview_assignments(assignments, "Add schedule", inputs)


def preview_recurrence(provider_id, location_id, session, weekdays, start, end, inputs):
    """Structured adapter using the same bounds validation as the Stage 1 API."""
    try:
        dates = db._recurrence_dates(weekdays, start, end)
        assignments = [Assignment("ADD", provider_id, location_id, day.isoformat(), session, "recurring")
                       for day in dates]
        return preview_assignments(assignments, "Add schedule", inputs)
    except ValueError as exc:
        return preview_assignments([], "Add schedule", inputs, str(exc))


def _copy_source(conn, kind, value):
    if kind == "entry":
        row = conn.execute("SELECT entry_date FROM schedule_entries WHERE id=?", (value,)).fetchone()
        if row is None:
            raise ValueError("Source assignment no longer exists.")
        day = row["entry_date"]
    elif kind == "day":
        day = db._real_date(value).isoformat()
    else:
        raise ValueError("Unsupported copy source.")
    rows = [dict(row) for row in conn.execute(
        "SELECT * FROM schedule_entries WHERE entry_date=? ORDER BY id", (day,))]
    assignments = group_assignments(rows)
    if kind == "entry":
        assignments = [a for a in assignments if value in a["entry_ids"]]
        ids = {i for a in assignments for i in a["entry_ids"]}
        rows = [row for row in rows if row["id"] in ids]
    snapshot = tuple(tuple(row[key] for key in ("id", "provider_id", "location_id", "entry_date", "half_day", "source"))
                     for row in rows)
    return assignments, snapshot


def preview_copy(kind, value, targets, inputs):
    operation = "Copy day" if kind == "day" else "Copy assignment"
    value = value.isoformat() if type(value) is date else value
    try:
        limit = command_parser.assignment_limit()
        with db.get_conn() as conn:
            conn.execute("BEGIN")
            source, snapshot = _copy_source(conn, kind, value)
            assignments = []
            for target in targets:
                for a in source:
                    assignments.append(Assignment("ADD", a["provider_id"], a["location_id"],
                                                  target.isoformat() if type(target) is date else target, a["half_day"], "copy"))
                    if len(assignments) > limit:
                        raise ValueError(command_parser.limit_message(limit))
            rows = _review(conn, assignments)
        return ChangeSet(str(uuid4()), operation, input_signature(inputs), rows,
                         () if rows else ("No source assignments or target dates to copy.",), kind, value, snapshot)
    except (ValueError, sqlite3.Error) as exc:
        return ChangeSet(str(uuid4()), operation, input_signature(inputs), (), (str(exc),))


def preview_table(change_set):
    return [{"Action": row.assignment.action, "Provider": row.provider_name, "Location": row.location_name,
             "Date": row.assignment.entry_date or "Unrecognized date",
             "Session": "Full day" if row.assignment.session == FULL_DAY else (row.assignment.session or "Unrecognized session"),
             "Validation status": f"{row.status.capitalize()}: {row.reason}"} for row in change_set.rows]


def _blocked_results(rows):
    return tuple(db.WriteResult(row.assignment.entry_date,
                                row.status if row.status in ("invalid", "conflict") else "invalid",
                                row.reason if row.status != "valid" else "Batch not saved because another assignment failed review.",
                                row.assignment.provider_id, row.assignment.session) for row in rows)


def approve(change_set, current_inputs):
    """Revalidate and insert the exact reviewed batch under a single write lock."""
    if change_set.input_signature != input_signature(current_inputs):
        return ApprovalResult("rejected", "Inputs changed. Generate and review a new preview.")
    if not change_set.can_approve:
        return ApprovalResult("rejected", "Resolve all preview issues and generate a new preview.",
                              _blocked_results(change_set.rows))
    try:
        with db.get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            receipt = conn.execute("SELECT payload_hash FROM approved_batches WHERE batch_id=?",
                                   (change_set.batch_id,)).fetchone()
            if receipt:
                if receipt["payload_hash"] == change_set.payload_hash:
                    return ApprovalResult("already_applied", "This batch was already approved; nothing was added again.")
                return ApprovalResult("rejected", "Batch identity changed. Generate a new preview.")
            limit = command_parser.assignment_limit()
            if len(change_set.rows) > limit:
                return ApprovalResult("rejected", command_parser.limit_message(limit) + " Generate a new preview.")
            if change_set.source_kind:
                try:
                    _, current_source = _copy_source(conn, change_set.source_kind, change_set.source_value)
                except ValueError:
                    current_source = None
                if current_source != change_set.source_snapshot:
                    return ApprovalResult("rejected", "Copy source changed. Nothing saved; generate and review a new preview.")
            assignments = tuple(row.assignment for row in change_set.rows)
            rows = _review(conn, assignments)
            if any(row.status != "valid" for row in rows):
                return ApprovalResult("rejected", "Validation changed. Nothing saved; generate and review a new preview.",
                                      _blocked_results(rows))
            if any(old.context != new.context for old, new in zip(change_set.rows, rows)):
                return ApprovalResult("rejected", "Provider or location details changed. Nothing saved; review again.")
            for assignment in assignments:
                db._insert_assignment(conn, assignment.provider_id, assignment.location_id,
                                      assignment.entry_date, assignment.session, assignment.source)
            conn.execute("INSERT INTO approved_batches (batch_id, payload_hash) VALUES (?,?)",
                         (change_set.batch_id, change_set.payload_hash))
        return ApprovalResult("approved", "Approved batch saved.", tuple(
            db.WriteResult(a.entry_date, "added", "Assignment added.", a.provider_id, a.session) for a in assignments))
    except sqlite3.Error as exc:
        return ApprovalResult("error", "Nothing saved. Generate and review a new preview.", tuple(
            db.WriteResult(row.assignment.entry_date, "error", f"Database error: {exc}",
                           row.assignment.provider_id, row.assignment.session) for row in change_set.rows))
    except ValueError as exc:
        return ApprovalResult("rejected", f"Nothing saved: {exc}. Generate and review a new preview.")
