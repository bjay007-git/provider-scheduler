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

HALF_DAY_KEYWORDS = {
    "first half": "AM",
    "second half": "PM",
    "morning": "AM",
    "afternoon": "PM",
    "evening": "PM",
    "a.m.": "AM",
    "p.m.": "PM",
    "am": "AM",
    "pm": "PM",
}

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


def match_half_day(text):
    lower = text.lower()
    # check multi-word phrases before single words so "first half" wins over a stray "am"
    for kw in sorted(HALF_DAY_KEYWORDS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(kw)}\b", lower):
            return HALF_DAY_KEYWORDS[kw]
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
    settings = {"PREFER_DATES_FROM": "future"}
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
    half_day = match_half_day(text)
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
        "is_recurring": is_recurring,
        "weekdays": weekdays,
        "entry_date": entry_date,
    }
