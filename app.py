"""
app.py — TiaMD Physician Scheduler POC

Flow: admin speaks -> browser transcribes live (free, no API key) -> admin
reviews/edits the raw transcript -> confirm -> rule-based parser extracts
provider/location/date/half-day (no LLM call) -> validated and saved ->
shown on a month calendar. Plus three speed-up tools: copy a whole day,
copy a single assignment, and voice-driven recurring weekly rules.
"""

import calendar as cal
from datetime import date, timedelta

import streamlit as st
from streamlit_calendar import calendar as st_calendar
from streamlit_mic_recorder import speech_to_text

import db
import nlu_parser as nlu

st.set_page_config(page_title="TiaMD Scheduler POC", layout="wide")
db.init_db()
db.seed_mock_data()

PROVIDERS = db.get_providers()
LOCATIONS = db.get_locations()
PALETTE = ["#1D9E75", "#378ADD", "#D85A30", "#B24BA6", "#C9971E", "#4C6EF5"]
PROVIDER_COLOR = {p["id"]: PALETTE[i % len(PALETTE)] for i, p in enumerate(PROVIDERS)}

st.title("🩺 TiaMD Physician Scheduler — POC")
st.caption(
    "Speak a schedule, confirm the transcript, and it's added to the month calendar. "
    "No LLM calls — parsing is fuzzy matching + a date library, so it costs nothing per request."
)

# ============================================================
# 1. Voice capture + transcript-only confirmation
# ============================================================
st.subheader("1. Speak the schedule")
st.caption(
    "Try: \u201cDr. Sarah Khan at City General Hospital next Monday morning\u201d or "
    "\u201cRao at Riverside Clinic every Tuesday and Thursday afternoon\u201d"
)

if "transcript" not in st.session_state:
    st.session_state.transcript = ""

heard = speech_to_text(
    language="en-US",
    start_prompt="\U0001F3A4 Start speaking",
    stop_prompt="\u23F9 Stop",
    just_once=True,
    key="stt_widget",
)
if heard:
    st.session_state.transcript = heard

st.session_state.transcript = st.text_area(
    "Review before saving (fix anything the mic misheard, then confirm):",
    value=st.session_state.transcript,
    height=80,
)

btn_col, clear_col = st.columns([1, 5])
confirm_clicked = btn_col.button(
    "\u2705 Confirm & Save", type="primary", disabled=not st.session_state.transcript.strip()
)
if clear_col.button("Clear"):
    st.session_state.transcript = ""
    st.rerun()

if confirm_clicked:
    parsed = nlu.parse_transcript(st.session_state.transcript, PROVIDERS, LOCATIONS)

    missing = []
    if not parsed["provider_id"]:
        missing.append("provider")
    if not parsed["location_id"]:
        missing.append("location")
    if not parsed["half_day"]:
        missing.append("AM/PM (morning/afternoon)")
    if not parsed["is_recurring"] and not parsed["entry_date"]:
        missing.append("date")

    if missing:
        st.error(f"Couldn't identify: {', '.join(missing)}. Edit the text above and confirm again.")
    elif not db.is_mapped(parsed["provider_id"], parsed["location_id"]):
        p_name = next(p["full_name"] for p in PROVIDERS if p["id"] == parsed["provider_id"])
        l_name = next(l["name"] for l in LOCATIONS if l["id"] == parsed["location_id"])
        st.error(f"{p_name} isn't mapped to {l_name} in this practice — check the wording, or update the mapping.")
    else:
        if parsed["is_recurring"]:
            today = date.today()
            month_end = date(today.year, today.month, cal.monthrange(today.year, today.month)[1])
            results = db.add_recurring(
                parsed["provider_id"], parsed["location_id"], parsed["half_day"],
                parsed["weekdays"], today, month_end,
            )
        else:
            success, msg = db.add_entry(
                parsed["provider_id"], parsed["location_id"],
                parsed["entry_date"].isoformat(), parsed["half_day"],
            )
            results = [(parsed["entry_date"].isoformat(), success, msg)]

        added = [r for r in results if r[1]]
        skipped = [r for r in results if not r[1]]
        p_name = next(p["full_name"] for p in PROVIDERS if p["id"] == parsed["provider_id"])
        l_acr = next(l["acronym"] for l in LOCATIONS if l["id"] == parsed["location_id"])

        st.success(
            f"Added {len(added)} entr{'y' if len(added) == 1 else 'ies'} \u2014 "
            f"{p_name} @ {l_acr} ({parsed['half_day']})."
        )
        if skipped:
            st.warning(
                f"Skipped {len(skipped)} due to conflicts (provider already booked that date/half): "
                f"{', '.join(r[0] for r in skipped)}"
            )
        st.session_state.transcript = ""
        st.rerun()

st.divider()

# ============================================================
# 2. Month calendar
# ============================================================
st.subheader("2. Month schedule")

today = date.today()
st.session_state.setdefault("cal_year", today.year)
st.session_state.setdefault("cal_month", today.month)

entries = db.get_month_entries(st.session_state.cal_year, st.session_state.cal_month)

legend = "&nbsp;&nbsp;&nbsp;".join(
    f"<span style='color:{PROVIDER_COLOR[p['id']]}'>\u2b24</span> {p['initials']} \u2013 {p['full_name']}"
    for p in PROVIDERS
)
st.markdown(legend, unsafe_allow_html=True)

events = []
for e in entries:
    start_t, end_t = ("08:00:00", "12:00:00") if e["half_day"] == "AM" else ("13:00:00", "17:00:00")
    color = PROVIDER_COLOR.get(e["provider_id"], "#888888")
    events.append({
        "id": str(e["id"]),
        "title": f"{e['provider_initials']}-{e['location_acronym']}-{e['half_day']}",
        "start": f"{e['entry_date']}T{start_t}",
        "end": f"{e['entry_date']}T{end_t}",
        "backgroundColor": color,
        "borderColor": color,
    })

st_calendar(
    events=events,
    options={
        "initialView": "dayGridMonth",
        "initialDate": f"{st.session_state.cal_year:04d}-{st.session_state.cal_month:02d}-01",
        "headerToolbar": {"left": "title", "center": "", "right": "today prev,next"},
        "height": 650,
    },
    key="scheduler_calendar",
)

st.divider()

# ============================================================
# 3. Speed-up tools: copy a day, copy a single assignment
#    (recurring weekly rules are handled above via voice, e.g. "every Monday")
# ============================================================
st.subheader("3. Speed up the rest of the month")
tab_day, tab_entry = st.tabs(["\U0001F4CB Copy a whole day", "\U0001F501 Copy a single assignment"])

with tab_day:
    src = st.date_input("Copy everything scheduled on:", value=today, key="copyday_src")
    day_targets = st.multiselect(
        "onto these date(s):",
        options=[src + timedelta(days=i) for i in range(1, 32)],
        format_func=lambda d: d.strftime("%a, %b %d"),
        key="copyday_targets",
    )
    if st.button("Copy day", disabled=not day_targets):
        results = db.copy_day(src.isoformat(), [d.isoformat() for d in day_targets])
        copied = sum(1 for r in results if r[3])
        skipped = sum(1 for r in results if not r[3])
        if not results:
            st.info("Nothing was scheduled on that day to copy.")
        else:
            st.success(f"Copied {copied} entries.")
            if skipped:
                st.warning(f"{skipped} skipped \u2014 provider already booked that date/half.")
        st.rerun()

with tab_entry:
    month_entries = db.get_month_entries(st.session_state.cal_year, st.session_state.cal_month)
    if not month_entries:
        st.info("No entries yet this month to copy from.")
    else:
        labels = {
            e["id"]: f"{e['entry_date']} {e['half_day']} \u2014 {e['provider_initials']} @ {e['location_acronym']}"
            for e in month_entries
        }
        chosen_id = st.selectbox("Which assignment?", options=list(labels), format_func=lambda i: labels[i])
        src_date_obj = date.fromisoformat(next(e["entry_date"] for e in month_entries if e["id"] == chosen_id))
        entry_targets = st.multiselect(
            "onto these date(s):",
            options=[src_date_obj + timedelta(days=i) for i in range(1, 32)],
            format_func=lambda d: d.strftime("%a, %b %d"),
            key="copyentry_targets",
        )
        if st.button("Copy assignment", disabled=not entry_targets):
            results = db.copy_entry(chosen_id, [d.isoformat() for d in entry_targets])
            copied = sum(1 for r in results if r[1])
            skipped = sum(1 for r in results if not r[1])
            st.success(f"Copied to {copied} day(s).")
            if skipped:
                st.warning(f"{skipped} skipped \u2014 conflict on that date.")
            st.rerun()
