"""
nlu_parser.py — Turns a confirmed transcript into structured fields.

Deliberately rule-based (fuzzy string matching + keyword lookup + a date
parser) rather than an LLM call: it's free, deterministic, and fast enough
to run on every keystroke of a correction if needed.
"""

import re
from datetime import date

import dateparser.search
from rapidfuzz import fuzz, process

from scheduling import FULL_DAY

# Match plurals and punctuation without letting "am" match inside a name.
SESSION_PATTERNS = {
    "AM": r"\b(?:mornings?|first half|a\.?m\.?)(?!\w)",
    "PM": r"\b(?:afternoons?|second half|p\.?m\.?)(?!\w)",
    FULL_DAY: r"\b(?:full[ -]days?|all[ -]days?|whole days?)\b",
}
UNSUPPORTED_SESSION = (
    r"\b(?:evenings?|nights?|overnight|noon|midday|midnight|lunchtime|"
    r"half[ -]days?|halves|sessions?|shifts?|hours?|early|late|lunch|full[ -]time)\b"
    r"|\b\d{1,2}:\d{2}\b|(?<![\d-])\b\d{1,2}\s*(?:a\.?m\.?|p\.?m\.?|o['\u2019]?clock)\b"
    r"|\bat\s+\d{1,2}(?![\d-])\b"
    r"|\b(?:from|between)\s+\d{1,2}\s*(?:to|and|-)\s*\d{1,2}\b"
    r"|\b\d{1,2}\s+(?:to|until|through)\s+\d{1,2}\b"
    r"|(?<![\d-])\b\d{1,2}\s*[-\u2013]\s*\d{1,2}\b(?![\d-])"
)

WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

RECURRENCE_TRIGGERS = ("every", "each", "weekdays", "weekday")


def _extract_surname(full_name):
    """'Dr. Sarah Khan' -> 'Khan'. People say surnames, not full titles, when speaking fast."""
    cleaned = re.sub(r"^(dr\.?|doctor)\s+", "", full_name.strip(), flags=re.IGNORECASE)
    parts = cleaned.split()
    return parts[-1] if parts else full_name


def _provider_aliases(p):
    return [p["full_name"], _extract_surname(p["full_name"])]


def _location_aliases(l):
    return [l["name"], l["acronym"]]


def _best_match(text, entities, alias_fn, score_cutoff=55):
    """Two-stage match against a list of {id, ...} entities:
    1) exact whole-word alias match (surname, acronym) — handles the very
       common case of a short spoken fragment ('Khan', 'CGH') against a
       long formal name, where length-normalized fuzzy scorers like
       partial_ratio score too low despite being a perfect match.
    2) fuzzy partial_ratio fallback against the full name — handles STT
       misspellings ('Sara Kahn' for 'Sarah Khan').
    Returns (id, matched_alias, score) or (None, None, 0)."""
    lower_text = text.lower()

    for e in entities:
        for alias in alias_fn(e):
            if alias and len(alias) >= 3 and re.search(rf"\b{re.escape(alias.lower())}\b", lower_text):
                return e["id"], alias, 100.0

    choices = {e["id"]: alias_fn(e)[0] for e in entities}  # primary alias = full name
    result = process.extractOne(text, choices, scorer=fuzz.partial_ratio, score_cutoff=score_cutoff)
    if result:
        matched_name, score, entity_id = result
        return entity_id, matched_name, score
    return None, None, 0


def match_provider(text, providers, score_cutoff=55):
    """providers: list of {id, full_name, initials}. Returns (id, matched_alias, score)."""
    if not providers:
        return None, None, 0
    return _best_match(text, providers, _provider_aliases, score_cutoff)


def match_location(text, locations, score_cutoff=55):
    """locations: list of {id, name, acronym}. Returns (id, matched_alias, score)."""
    if not locations:
        return None, None, 0
    return _best_match(text, locations, _location_aliases, score_cutoff)


def parse_session(text):
    """Return explicit session, omission, or a validation error; never guess."""
    if re.search(UNSUPPORTED_SESSION, text, re.IGNORECASE):
        return None, "Unsupported session wording. Use morning, afternoon, or Full day."
    mentions = sorted((match.start(), match.end(), session)
                      for session, pattern in SESSION_PATTERNS.items()
                      for match in re.finditer(pattern, text, re.IGNORECASE))
    found = {session for _, _, session in mentions}
    if re.search(r"\b(?:not|except|excluding|neither|without)\b", text, re.IGNORECASE):
        return None, "Excluded sessions are unsupported. Use morning, afternoon, or Full day."
    if found == {"AM", "PM"} and len(mentions) == 2:
        connector = text[mentions[0][1]:mentions[1][0]]
        if re.fullmatch(r"\s*(?:and|&)\s*(?:the\s+)?", connector, re.IGNORECASE):
            return FULL_DAY, None
    if len(found) > 1:
        return None, "Ambiguous or conflicting session wording. Use morning, afternoon, or Full day."
    return next(iter(found), None), None


def match_half_day(text):
    session, _ = parse_session(text)
    return session


def _add_command_error(text):
    # Only ADD is implemented. Do not reinterpret future command types as ADD.
    if re.search(r"\b(?:delete|remove|cancel|modify|edit|change|update|move|reschedule|unschedule|swap|replace|copy|duplicate)\b",
                 text, re.IGNORECASE):
        return "Only ADD commands are supported in this stage."
    return None


def detect_recurrence(text):
    """Returns (is_recurring, weekday_list) — weekday_list uses Monday=0..Sunday=6."""
    lower = text.lower()
    if not any(trigger in lower for trigger in RECURRENCE_TRIGGERS):
        return False, []

    weekdays_found = sorted({v for k, v in WEEKDAYS.items() if re.search(rf"\b{k}\b", lower)})
    if not weekdays_found and re.search(r"\bweekdays?\b", lower):
        weekdays_found = [0, 1, 2, 3, 4]

    if not weekdays_found:
        return False, []
    return True, weekdays_found


def parse_single_date(text, reference_date=None):
    """dateparser.parse() tries to read the *whole* string as a date and
    fails on real sentences ('next Monday morning' -> None, oddly).
    search_dates() is built to pull a date phrase out of free text, which
    is what we actually need here."""
    # Keep this legacy adapter consistent with the active command parser.
    # Never allow dateparser to swap an invalid month/day or repair leap days.
    numeric_dates = re.findall(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\b", text)
    if numeric_dates:
        parsed = []
        for value in numeric_dates:
            try:
                if '/' in value:
                    month, day, year = map(int, value.split('/'))
                    parsed.append(date(year, month, day))
                else:
                    parsed.append(date.fromisoformat(value))
            except ValueError:
                return None
        return parsed[0]
    if re.search(r"\b\d+[/-]\d+", text):
        return None
    settings = {"PREFER_DATES_FROM": "future", "DATE_ORDER": "MDY", "PREFER_LOCALE_DATE_ORDER": False}
    if reference_date:
        settings["RELATIVE_BASE"] = _as_datetime(reference_date)
    found = dateparser.search.search_dates(text, settings=settings)
    if not found:
        return None
    _, parsed_dt = found[0]
    return parsed_dt.date()


def _as_datetime(d):
    import datetime as _dt
    return _dt.datetime(d.year, d.month, d.day)


def parse_transcript(text, providers, locations, today=None):
    """
    Main entry point.

    Returns a dict:
      raw_text, provider_id/name/score, location_id/name/score, half_day,
      is_recurring, weekdays (if recurring), entry_date (if not recurring)
    None fields mean "not recognized" — the caller should let the admin
    fill/correct them manually rather than fail silently.
    """
    today = today or date.today()

    provider_id, provider_name, provider_score = match_provider(text, providers)
    location_id, location_name, location_score = match_location(text, locations)
    half_day, session_error = parse_session(text)
    command_error = _add_command_error(text)
    if half_day is None and not session_error and not command_error:
        half_day = FULL_DAY
    is_recurring, weekdays = detect_recurrence(text)

    entry_date = None
    if not is_recurring:
        entry_date = parse_single_date(text, reference_date=today)

    return {
        "raw_text": text,
        "provider_id": provider_id,
        "provider_name": provider_name,
        "provider_score": provider_score,
        "location_id": location_id,
        "location_name": location_name,
        "location_score": location_score,
        "half_day": half_day,
        "error": command_error or session_error,
        "is_recurring": is_recurring,
        "weekdays": weekdays,
        "entry_date": entry_date,
    }
