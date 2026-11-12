"""Conservative ADD grammar: shared providers/location with dated session clauses.

No writes and no dateparser guesses. Every non-filler token must be consumed.
The explicit reference date controls all relative and yearless dates.
"""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
import os
import re

from rapidfuzz import fuzz

import nlu_parser as legacy
from scheduling import FULL_DAY

DEFAULT_MAX_ASSIGNMENTS = 500


def assignment_limit():
    raw = os.environ.get("SCHEDULER_MAX_ASSIGNMENTS", str(DEFAULT_MAX_ASSIGNMENTS))
    try:
        limit = int(raw)
        if limit > 0:
            return limit
    except (ValueError, TypeError):
        pass
    raise ValueError("SCHEDULER_MAX_ASSIGNMENTS must be a positive integer.")


def limit_message(limit):
    return f"Request exceeds the configured limit of {limit} logical assignments. Narrow the dates or providers."


@dataclass(frozen=True)
class Command:
    provider_ids: tuple[int, ...] = ()
    location_id: int | None = None
    dates: tuple[date, ...] = ()
    session: str | None = None
    recurring: bool = False
    error: str = ""
    # Authoritative expansion; session is only a summary for uniform patterns.
    date_sessions: tuple[tuple[date, str], ...] = ()


def _normal_name(text):
    text = re.sub(r"^(?:doctor\b|dr(?:\.|\b))\s*", "", text.strip(), flags=re.I)
    return re.sub(r"\s+", " ", text.strip(" ,.:;")).casefold()


def _provider_aliases(provider):
    return {_normal_name(provider["full_name"]), _normal_name(legacy._extract_surname(provider["full_name"])),
            _normal_name(provider.get("initials", ""))} - {""}


def _location_aliases(location):
    name = _normal_name(location["name"])
    short = re.sub(r"\s+(?:hospital|clinic|medical cent(?:er|re))$", "", name)
    return {name, short, _normal_name(location["acronym"])} - {""}


def _resolve(text, entities, aliases, name_key, kind):
    normalized = _normal_name(text)
    exact = [entity for entity in entities if normalized in aliases(entity)]
    if len(exact) == 1:
        return exact[0]["id"]
    if len(exact) > 1:
        names = ", ".join(entity[name_key] for entity in exact)
        raise ValueError(f"Ambiguous {kind} '{text.strip()}': {names}. Use the full official name.")
    scores = sorted(((max((fuzz.ratio(normalized, alias) for alias in aliases(entity)), default=0), entity[name_key])
                     for entity in entities), reverse=True)
    suggestions = ", ".join(name for score, name in scores[:2] if score >= 65)
    if suggestions:
        raise ValueError(f"Uncertain {kind} '{text.strip()}'. Possible match: {suggestions}. Use an official name or acronym.")
    raise ValueError(f"Unrecognized {kind} '{text.strip()}'. Use an official name or acronym.")


def _location_mentions(text, locations):
    matches = []
    for location in locations:
        for alias in _location_aliases(location):
            pattern = r"(?<!\w)" + r"\s+".join(re.escape(part) for part in alias.split()) + r"(?!\w)"
            matches.extend((match.start(), match.end(), match.group(), location["id"])
                           for match in re.finditer(pattern, text, re.I))
    return sorted(matches, key=lambda item: (item[0], -(item[1] - item[0]), item[3]))


def _entities(text, providers, locations):
    text = re.sub(r"^\s*(?:please\s+)?(?:(?:add|schedule|book|assign)\s+)?", "", text, flags=re.I)
    at = re.search(r"\bat\b", text, re.I)
    start = at.end() if at else 0
    mentions = _location_mentions(text[start:], locations)
    if not mentions:
        candidate = text[start:].split(",")[0].strip() if at else ""
        if candidate:
            # Limit the suggestion to the name, excluding a temporal suffix.
            candidate = re.split(r"\b(?:on|today|tomorrow|next|every|weekdays?|rest)\b|\d", candidate, maxsplit=1, flags=re.I)[0].strip()
            if candidate:
                _resolve(candidate, locations, _location_aliases, "name", "location")
        raise ValueError("Location not recognized. Use 'providers at official location, date pattern'.")
    first = mentions[0]
    loc_start, loc_end = start + first[0], start + first[1]
    # Prefer the longest official alias over a nested short alias.
    same = [item for item in mentions if item[:2] == first[:2]]
    if len({item[3] for item in same}) > 1:
        _resolve(first[2], locations, _location_aliases, "name", "location")
    if any(item[0] >= first[1] for item in mentions):
        raise ValueError("Multiple location clauses are unsupported. Use one shared location per command.")
    if at and text[at.end():loc_start].strip(" ,:"):
        raise ValueError("Unrecognized text before the location. Use the full official location name.")
    provider_text = text[:at.start() if at else loc_start].strip(" ,:")
    provider_text = re.sub(r"^(?:providers?|doctors?|drs\.?)\s+", "", provider_text, flags=re.I)
    pieces = [piece.strip() for piece in re.split(r"\s*(?:\band\b|&|,|/)\s*", provider_text, flags=re.I) if piece.strip()]
    if not pieces:
        raise ValueError("No provider named. List every provider before the shared location.")
    ids, errors = [], []
    for piece in pieces:
        try:
            provider_id = _resolve(piece, providers, _provider_aliases, "full_name", "provider")
            if provider_id not in ids:
                ids.append(provider_id)
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        raise ValueError(" ".join(errors) + " No partial provider list will be scheduled.")
    return tuple(ids), first[3], text[loc_end:].strip(" ,:")


MONTHS = {name.lower(): index for index in range(1, 13)
          for name in (calendar.month_name[index], calendar.month_abbr[index])}
MONTH_PATTERN = r"(?:" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?"
DAY_PATTERN = r"\d{1,2}(?:st|nd|rd|th)?\b"
JOIN_PATTERN = r"(?:\s*(?:,\s*(?:and\s+)?|and\s+|&\s*|to\s+|through\s+|until\s+|[-\u2013]\s*))"
WRITTEN_PATTERN = re.compile(
    rf"\b(?P<month>{MONTH_PATTERN})\s+(?P<days>{DAY_PATTERN}(?:{JOIN_PATTERN}{DAY_PATTERN})*)"
    r"(?:(?:\s*,\s*|\s+)(?P<year>\d{4})\b)?", re.I)
DAY_FIRST_PATTERN = re.compile(rf"\b(?P<day>{DAY_PATTERN})\s+(?P<month>{MONTH_PATTERN})(?:(?:\s*,\s*|\s+)(?P<year>\d{{4}})\b)?", re.I)
ISO_PATTERN = re.compile(r"(?<![\w-])\d{4}-\d{1,2}-\d{1,2}(?![\w-])")
US_PATTERN = re.compile(r"(?<![\w/])\d{1,2}/\d{1,2}/\d{4}(?![\w/])")
WEEKDAY_PATTERN = re.compile(r"\b(?:" + "|".join(sorted(legacy.WEEKDAYS, key=len, reverse=True)) + r")s?\b", re.I)
RANGE_CONNECTOR = re.compile(r"\s*(?:to|through|until|[-\u2013])\s*", re.I)
DATE_TOKEN_PATTERN = (rf"(?:\d{{4}}-\d{{1,2}}-\d{{1,2}}|\d{{1,2}}/\d{{1,2}}/\d{{4}}|"
                      rf"{MONTH_PATTERN}\s+\d|\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTH_PATTERN}|"
                      r"today\b|tomorrow\b|yesterday\b)")


def _masked(text, spans, barrier=False):
    chars = list(text)
    for start, end in spans:
        chars[start:end] = ("|" if barrier else " ") + " " * (end - start - 1)
    return "".join(chars)


def _number(text):
    return int(re.match(r"\d+", text).group())


def _written_segments(match, reference):
    month = MONTHS[match['month'].lower().rstrip('.')]
    year = int(match['year']) if match['year'] else reference.year
    parts = list(re.finditer(DAY_PATTERN, match['days'], re.I))
    days = [date(year, month, _number(part.group())) for part in parts]
    connectors = [match['days'][parts[i].end():parts[i + 1].start()] for i in range(len(parts) - 1)]
    segments, index = [], 0
    while index < len(days):
        if index < len(connectors) and RANGE_CONNECTOR.fullmatch(connectors[index]):
            if index + 1 < len(connectors) and RANGE_CONNECTOR.fullmatch(connectors[index + 1]):
                raise ValueError("Use one start and end per date range.")
            segments.append((days[index], days[index + 1]))
            index += 2
        else:
            segments.append((days[index], days[index]))
            index += 1
    return segments


def _temporal(text, reference, date_limit, limit):
    spans, regions, scopes = [], [], []
    for match in re.finditer(r"\b(?:rest of (?:the |this )?month|remainder of (?:the |this )?month|next month)\b", text, re.I):
        if match.group().lower() == 'next month':
            year = reference.year + (reference.month == 12)
            month = reference.month % 12 + 1
            bounds = (date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1]))
        else:
            bounds = (reference, date(reference.year, reference.month, calendar.monthrange(reference.year, reference.month)[1]))
        scopes.append(bounds)
        spans.append(match.span())
    if len(scopes) > 1:
        raise ValueError("Use one shared month range per command.")
    working = _masked(text, spans)
    for match in ISO_PATTERN.finditer(working):
        try:
            day = date.fromisoformat(match.group())
            if day.isoformat() != match.group():
                raise ValueError()
        except ValueError:
            raise ValueError(f"Invalid ISO date '{match.group()}'. Use a real date in YYYY-MM-DD format.") from None
        regions.append((match.start(), match.end(), [(day, day)]))
        spans.append(match.span())
    working = _masked(text, spans)
    for match in US_PATTERN.finditer(working):
        try:
            month, day_number, year = map(int, match.group().split('/'))
            day = date(year, month, day_number)
        except ValueError:
            raise ValueError(f"Invalid MM/DD/YYYY date '{match.group()}'. Month must come first; DD/MM/YYYY is not supported.") from None
        regions.append((match.start(), match.end(), [(day, day)]))
        spans.append(match.span())
    for pattern in (WRITTEN_PATTERN, DAY_FIRST_PATTERN):
        working = _masked(text, spans)
        for match in pattern.finditer(working):
            try:
                if pattern is WRITTEN_PATTERN:
                    segments = _written_segments(match, reference)
                else:
                    day = date(int(match['year']) if match['year'] else reference.year,
                               MONTHS[match['month'].lower().rstrip('.')], _number(match['day']))
                    segments = [(day, day)]
            except ValueError as exc:
                raise ValueError(f"Invalid written date or range '{match.group()}': {exc}.") from None
            regions.append((match.start(), match.end(), segments))
            spans.append(match.span())
    working = _masked(text, spans)
    if re.search(r"\b\d{1,4}[/-]\d{1,2}(?:[/-]\d{1,4})?\b", working):
        raise ValueError("Unsupported numeric date. Use MM/DD/YYYY, YYYY-MM-DD or a written month name; DD/MM/YYYY is not supported.")
    relative = re.compile(r"\b(?:today|tomorrow|yesterday|(?:next|this)\s+(?:" +
                          '|'.join(sorted(legacy.WEEKDAYS, key=len, reverse=True)) + r"))\b", re.I)
    for match in relative.finditer(working):
        phrase = match.group().lower()
        if phrase in ('today', 'tomorrow', 'yesterday'):
            day = reference + timedelta(days={'today': 0, 'tomorrow': 1, 'yesterday': -1}[phrase])
        else:
            modifier, name = phrase.split()
            delta = (legacy.WEEKDAYS[name] - reference.weekday()) % 7
            day = reference + timedelta(days=(delta or 7) if modifier == 'next' else delta)
        regions.append((match.start(), match.end(), [(day, day)]))
        spans.append(match.span())
    working = _masked(text, spans)
    weekday_matches = list(WEEKDAY_PATTERN.finditer(working))
    weekdays = set()
    for match in weekday_matches:
        name = match.group().lower()
        weekdays.add(legacy.WEEKDAYS.get(name, legacy.WEEKDAYS.get(name.rstrip('s'))))
        spans.append(match.span())
    for match in re.finditer(r"\bweekdays?\b", working, re.I):
        weekdays.update(range(5))
        spans.append(match.span())
    for match in re.finditer(r"\b(?:every|each)\b", working, re.I):
        if not weekdays:
            raise ValueError("Name the weekdays after 'every' or 'each'.")
        spans.append(match.span())
    has_repeat = bool(re.search(r"\b(?:every|each|weekdays?)\b", working, re.I)) or len(weekday_matches) > 1
    if scopes and regions:
        raise ValueError("Use either a date list/range or a named month range, not both.")
    segments, index = [], 0
    regions.sort()
    while index < len(regions):
        left = regions[index]
        prefix = re.search(r"\b(from|between)\s*$", text[:left[0]], re.I)
        if index + 1 < len(regions):
            right = regions[index + 1]
            gap = text[left[1]:right[0]]
            is_range = RANGE_CONNECTOR.fullmatch(gap) or (prefix and prefix[1].lower() == 'between' and re.fullmatch(r"\s+and\s+", gap, re.I))
            if is_range:
                if any(len(region[2]) != 1 or region[2][0][0] != region[2][0][1] for region in (left, right)):
                    raise ValueError("Use one date for each range endpoint.")
                segments.append((left[2][0][0], right[2][0][0]))
                spans.append((left[1], right[0]))
                if prefix:
                    spans.append(prefix.span())
                index += 2
                continue
        if prefix:
            if prefix[1].lower() == 'from' and len(left[2]) == 1 and left[2][0][0] != left[2][0][1]:
                spans.append(prefix.span())
            else:
                raise ValueError("Incomplete date range. Provide both start and end dates.")
        segments.extend(left[2])
        index += 1
    if scopes:
        segments = scopes
    elif not segments and weekdays:
        if has_repeat:
            segments = [(reference, date(reference.year, reference.month, calendar.monthrange(reference.year, reference.month)[1]))]
        else:
            day = reference + timedelta(days=(next(iter(weekdays)) - reference.weekday()) % 7)
            segments = [(day, day)]
    if not segments:
        raise ValueError("No complete date pattern recognized. Use dates, a range, or weekdays with a month range.")
    dates = set()
    for start, end in segments:
        if start > end:
            raise ValueError("Date range is reversed: the start must be on or before the end.")
        for offset in range((end - start).days + 1):
            day = start + timedelta(days=offset)
            if not weekdays or day.weekday() in weekdays:
                dates.add(day)
                if len(dates) > date_limit:
                    raise ValueError(limit_message(limit))
    if not dates:
        raise ValueError("The requested range produces no matching dates. Change the weekdays or range.")
    return tuple(sorted(dates)), spans, bool(has_repeat or scopes or any(start != end for start, end in segments))


def _date_clauses(tail):
    """Split only explicit date-first associations, retaining shared date lists.

    A session followed by a connector and another date starts a new clause.
    Semicolons explicitly separate clauses, including ones with no session.
    A plain date list with one trailing session remains a shared pattern.
    Unknown leftovers are validated, never discarded, by _parse_clause.
    """
    date_start = rf"(?:{DATE_TOKEN_PATTERN}|from\b|between\b)"
    boundary = re.compile(rf"(?:\s*;\s*|\s*,\s*(?:and\s+)?|\s+and\s+)(?=(?:on\s+)?{date_start})", re.I)
    clauses, start = [], 0
    for match in boundary.finditer(tail):
        prefix = tail[start:match.start()]
        has_session = any(re.search(pattern, prefix, re.I) for pattern in legacy.SESSION_PATTERNS.values())
        if ';' in match.group() or has_session:
            clauses.append(prefix)
            start = match.end()
    clauses.append(tail[start:])
    return clauses


def _parse_clause(tail, reference_date, date_limit, limit):
    dates, date_spans, recurring = _temporal(tail, reference_date, date_limit, limit)
    session_text = _masked(tail, date_spans, barrier=True)
    session, error = legacy.parse_session(session_text)
    if error:
        raise ValueError(error + " Attach each session to its date clause.")
    session_spans = [match.span() for pattern in legacy.SESSION_PATTERNS.values()
                     for match in re.finditer(pattern, session_text, re.I)]
    concrete_dates = list(re.finditer(DATE_TOKEN_PATTERN, tail, re.I))
    if len(concrete_dates) > 1 and any(concrete_dates[0].start() < start < concrete_dates[-1].start()
                                      for start, end in session_spans):
        raise ValueError("Unclear date/session association. Put each date before its session and separate clauses with 'and' or a semicolon.")
    remainder = _masked(tail, date_spans + session_spans)
    remainder = re.sub(r"\b(?:on|for|in|the|and|both)\b", " ", remainder, flags=re.I)
    remainder = re.sub(r"[\s,.:&/]+", " ", remainder).strip()
    if remainder:
        raise ValueError(f"Unrecognized or unsupported clause '{remainder}'. Use one shared location and explicit date/session clauses.")
    return dates, session or FULL_DAY, recurring


def parse_command(text, providers, locations, reference_date, *, max_assignments=None):
    """Resolve the complete ADD command or return a blocking clarification."""
    try:
        if type(reference_date) is not date:
            raise ValueError("An explicit reference date is required.")
        limit = assignment_limit() if max_assignments is None else max_assignments
        if type(limit) is not int or limit < 1:
            raise ValueError("The assignment limit must be a positive integer.")
        error = legacy._add_command_error(text)
        if error:
            raise ValueError(error)
        ids, location, tail = _entities(text.strip(), providers, locations)
        if len(ids) > limit:
            raise ValueError(limit_message(limit))
        pairs, recurring = set(), False
        for clause in _date_clauses(tail):
            dates, session, repeats = _parse_clause(clause, reference_date, limit // len(ids), limit)
            pairs.update((day, session) for day in dates)
            recurring |= repeats
            if len(pairs) * len(ids) > limit:
                raise ValueError(limit_message(limit))
        date_sessions = tuple(sorted(pairs))
        dates = tuple(sorted({day for day, session in pairs}))
        sessions = {session for day, session in pairs}
        return Command(ids, location, dates, next(iter(sessions)) if len(sessions) == 1 else None,
                       recurring, date_sessions=date_sessions)
    except (ValueError, OverflowError) as exc:
        return Command(error=str(exc))
