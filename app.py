"""Streamlit provider scheduling POC."""

import calendar as cal
from datetime import date, timedelta

import streamlit as st
from streamlit_calendar import calendar as st_calendar
from streamlit_mic_recorder import speech_to_text

import db
import nlu_parser as nlu
from scheduling import assignment_label, calendar_events, group_assignments, result_counts, shift_month

st.set_page_config(page_title="TiaMD Scheduler POC", layout="wide")
db.init_db()
db.seed_mock_data()

PROVIDERS = db.get_providers()
LOCATIONS = db.get_locations()
PALETTE = ["#1D9E75", "#378ADD", "#D85A30", "#B24BA6", "#C9971E", "#4C6EF5"]
PROVIDER_COLOR = {p["id"]: PALETTE[i % len(PALETTE)] for i, p in enumerate(PROVIDERS)}


def save_feedback(operation, results):
    st.session_state.operation_feedback = (operation, results)


def show_feedback():
    feedback = st.session_state.get("operation_feedback")
    if feedback is None:
        return
    operation, results = feedback
    counts = result_counts(results)
    summary = (f"{operation}: {counts['added']} added, {counts['skipped']} skipped, "
               f"{counts['failed']} failed (assignments).")
    if counts["failed"]:
        st.error(summary)
    elif counts["skipped"]:
        st.warning(summary)
    elif counts["added"]:
        st.success(summary)
    else:
        st.info(summary + " No source assignments or matching recurrence dates.")
    for result in results:
        if result.status != "added":
            provider = next((p["full_name"] for p in PROVIDERS if p["id"] == result.provider_id), "")
            session = "Full day" if result.session == "FULL" else (result.session or "")
            st.write(f"{result.entry_date} {provider} {session}: {result.message}")


def navigate_month(delta=None):
    if delta is None:
        target = date.today()
        year, month = target.year, target.month
    else:
        year, month = shift_month(st.session_state.cal_year, st.session_state.cal_month, delta)
    st.session_state.cal_year, st.session_state.cal_month = year, month


def clear_transcript():
    st.session_state.transcript = ""


st.title("TiaMD Physician Scheduler — POC")
st.caption("Speak or type an ADD schedule, review the transcript, and save. No session means Full day.")

# Feedback is retained until the next operation, including through navigation.
show_feedback()
st.subheader("1. Speak the schedule")
st.caption('Try: “Khan at City General Hospital next Monday” or “Rao at Lakeside Hospital every Tuesday afternoon”.')
st.session_state.setdefault("transcript", "")
# Clear before instantiating the keyed editor on the next run.
if st.session_state.pop("clear_transcript_after_save", False):
    clear_transcript()
heard = speech_to_text(
    language="en-US", start_prompt="Start speaking", stop_prompt="Stop",
    just_once=True, key="stt_widget",
)
if heard:
    st.session_state.transcript = heard
st.text_area("Review before saving (edit anything the mic misheard):", key="transcript", height=80)
btn_col, clear_col = st.columns([1, 5])
confirm_clicked = btn_col.button("Confirm & Save", type="primary", disabled=not st.session_state.transcript.strip())
clear_col.button("Clear", on_click=clear_transcript)

if confirm_clicked:
    parsed = nlu.parse_transcript(st.session_state.transcript, PROVIDERS, LOCATIONS)
    missing = [name for name, value in (("provider", parsed["provider_id"]),
                                       ("location", parsed["location_id"])) if value is None]
    if not parsed["is_recurring"] and parsed["entry_date"] is None:
        missing.append("date")
    if parsed["error"] or missing:
        results = [db.invalid_result(parsed["error"] or f"Couldn't identify: {', '.join(missing)}. Edit and confirm again.")]
    elif parsed["is_recurring"]:
        today = date.today()
        month_end = date(today.year, today.month, cal.monthrange(today.year, today.month)[1])
        results = db.add_recurring(parsed["provider_id"], parsed["location_id"], parsed["half_day"],
                                   parsed["weekdays"], today, month_end)
    else:
        results = [db.add_entry(parsed["provider_id"], parsed["location_id"], parsed["entry_date"], parsed["half_day"])]
    save_feedback("Add schedule", results)
    if any(r.status == "added" for r in results) and all(r.status == "added" for r in results):
        st.session_state.clear_transcript_after_save = True
    st.rerun()

st.divider()
st.subheader("2. Month schedule")
today = date.today()
st.session_state.setdefault("cal_year", today.year)
st.session_state.setdefault("cal_month", today.month)
previous, current, following = st.columns(3)
previous.button("Previous month", on_click=navigate_month, args=(-1,),
                disabled=(st.session_state.cal_year, st.session_state.cal_month) == (1, 1))
current.button("Today", on_click=navigate_month)
following.button("Next month", on_click=navigate_month, args=(1,),
                 disabled=(st.session_state.cal_year, st.session_state.cal_month) == (9999, 12))
year, month = st.session_state.cal_year, st.session_state.cal_month
st.caption(f"{cal.month_name[month]} {year}")
assignments = group_assignments(db.get_month_entries(year, month))
legend = " · ".join(f"{p['initials']} — {p['full_name']}" for p in PROVIDERS)
st.caption(legend)
# Installed streamlit-calendar 1.4.0 has no supported datesSet callback.
# Remount on month changes so initialDate is applied; disable internal navigation.
st_calendar(
    events=calendar_events(assignments, PROVIDER_COLOR),
    options={
        "initialView": "dayGridMonth", "initialDate": f"{year:04d}-{month:02d}-01",
        "headerToolbar": {"left": "title", "center": "", "right": ""},
        "showNonCurrentDates": False, "navLinks": False,
        "editable": False, "displayEventTime": False, "height": 650,
    },
    callbacks=[], key=f"scheduler_calendar_{year}_{month}",
)

st.divider()
st.subheader("3. Copy assignments")
tab_day, tab_entry = st.tabs(["Copy a whole day", "Copy a single assignment"])
with tab_day:
    src = st.date_input("Copy everything scheduled on:", value=today, key="copyday_src")
    day_targets = st.multiselect(
        "onto these date(s):", options=[src + timedelta(days=i) for i in range(1, 32)],
        format_func=lambda d: d.strftime("%a, %b %d"), key="copyday_targets",
    )
    if st.button("Copy day", disabled=not day_targets):
        save_feedback("Copy day", db.copy_day(src, day_targets))
        st.rerun()
with tab_entry:
    if not assignments:
        st.info("No assignments in the displayed month to copy from.")
    else:
        by_id = {a["id"]: a for a in assignments}
        chosen_id = st.selectbox(
            "Which assignment?", options=list(by_id),
            format_func=lambda i: f"{by_id[i]['entry_date']} — {assignment_label(by_id[i])}",
            key=f"copy_assignment_{year}_{month}",
        )
        src_date = date.fromisoformat(by_id[chosen_id]["entry_date"])
        entry_targets = st.multiselect(
            "onto these date(s):", options=[src_date + timedelta(days=i) for i in range(1, 32)],
            format_func=lambda d: d.strftime("%a, %b %d"), key=f"copyentry_targets_{year}_{month}_{chosen_id}",
        )
        if st.button("Copy assignment", disabled=not entry_targets):
            save_feedback("Copy assignment", db.copy_entry(chosen_id, entry_targets))
            st.rerun()
