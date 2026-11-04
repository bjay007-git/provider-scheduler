"""Shared presentation helpers for logical assignments and calendar state."""

FULL_DAY = "FULL"


def group_assignments(entries):
    """Combine AM and PM only when provider, location, and date all match."""
    groups = {}
    for entry in entries:
        key = (entry["entry_date"], entry["provider_id"], entry["location_id"])
        groups.setdefault(key, []).append(entry)
    assignments = []
    for rows in groups.values():
        assignment = dict(rows[0])
        assignment["entry_ids"] = tuple(sorted(row["id"] for row in rows))
        assignment["id"] = assignment["entry_ids"][0]
        if {row["half_day"] for row in rows} == {"AM", "PM"}:
            assignment["half_day"] = FULL_DAY
        assignments.append(assignment)
    return sorted(assignments, key=lambda a: (a["entry_date"], a["provider_id"], a["half_day"]))


def assignment_label(assignment):
    label = f"{assignment['provider_initials']}-{assignment['location_acronym']}"
    if assignment["half_day"] != FULL_DAY:
        label += f"-{assignment['half_day']}"
    return label


def calendar_events(assignments, colors):
    events = []
    for assignment in assignments:
        session = assignment["half_day"]
        start, end = ("08:00:00", "17:00:00") if session == FULL_DAY else (
            ("08:00:00", "12:00:00") if session == "AM" else ("13:00:00", "17:00:00")
        )
        color = colors.get(assignment["provider_id"], "#888888")
        events.append({
            "id": str(assignment["id"]), "title": assignment_label(assignment),
            "start": f"{assignment['entry_date']}T{start}",
            "end": f"{assignment['entry_date']}T{end}",
            "backgroundColor": color, "borderColor": color,
        })
    return events


def shift_month(year, month, delta):
    index = year * 12 + month - 1 + delta
    new_year, month_index = divmod(index, 12)
    if not 1 <= new_year <= 9999:
        raise ValueError("Calendar year is outside the supported range.")
    return new_year, month_index + 1


def result_counts(results):
    return {
        "added": sum(r.status == "added" for r in results),
        "skipped": sum(r.status == "conflict" for r in results),
        "failed": sum(r.status in ("invalid", "error") for r in results),
    }
