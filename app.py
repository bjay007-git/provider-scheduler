"""Streamlit provider scheduling POC."""

import calendar as cal
from datetime import date, timedelta

import streamlit as st
from streamlit_calendar import calendar as st_calendar
from streamlit_mic_recorder import mic_recorder

import db
import changes
from voice import transcribe_wav
from scheduling import assignment_label, calendar_events, group_assignments, result_counts, shift_month

st.set_page_config(page_title="TiaMD Scheduler POC", layout="wide")
db.init_db()
db.seed_mock_data()

PROVIDERS = db.get_providers()
LOCATIONS = db.get_locations()
PALETTE = ["#1D9E75", "#378ADD", "#D85A30", "#B24BA6", "#C9971E", "#4C6EF5"]
PROVIDER_COLOR = {p["id"]: PALETTE[i % len(PALETTE)] for i, p in enumerate(PROVIDERS)}


def save_feedback(operation, results, message="", status="approved"):
    st.session_state.operation_feedback = (operation, results, message, status)


def show_feedback():
    feedback = st.session_state.get("operation_feedback")
    if feedback is None:
        return
    operation, results, *details = feedback
    message, status = details if details else ("", "approved")
    st.caption("Last operation result")
    if message:
        (st.error if status in ("rejected", "error") else st.info)(message)
    if not results:
        return
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
    """Clear the whole voice draft before rendering its widgets again."""
    st.session_state.transcript = ""
    st.session_state.pop("voice_audio", None)
    st.session_state.pop("voice_result", None)
    old_key = f"voice_recorder_{st.session_state.get('mic_revision', 0)}"
    st.session_state.pop(old_key + "_output", None)
    st.session_state.mic_revision = st.session_state.get("mic_revision", 0) + 1
    st.session_state.voice_revision = st.session_state.get("voice_revision", 0) + 1


st.title("TiaMD Physician Scheduler — POC")
st.caption("Speak or type an ADD schedule, review the transcript, interpret it, then approve the proposed changes. No session means Full day.")

# Feedback is retained until the next operation, including through navigation.
# Keep its changing child elements inside a stable block so adding feedback
# cannot shift the microphone iframe's position in the Streamlit element tree.
with st.container():
    show_feedback()
requested_preview = None
st.subheader("1. Speak the schedule")
st.caption('Try: “Khan at City General Hospital next Monday” or “Rao at Lakeside Hospital every Tuesday afternoon”.')
st.session_state.setdefault("transcript", "")
# Clear before instantiating the keyed editor on the next run.
if st.session_state.pop("clear_transcript_after_save", False):
    clear_transcript()
st.caption("Click Start speaking, allow microphone access, speak, then click Stop. "
           "Google speech recognition requires an internet connection.")
recording = mic_recorder(
    start_prompt="Start speaking", stop_prompt="Stop",
    format="wav", just_once=True,
    key=f"voice_recorder_{st.session_state.get('mic_revision', 0)}",
)
if recording:
    st.session_state.voice_audio = recording["bytes"]
    st.session_state.voice_revision = st.session_state.get("voice_revision", 0) + 1
retry_voice = False
if st.session_state.get("voice_audio"):
    st.audio(st.session_state.voice_audio, format="audio/wav")
    retry_voice = st.button("Retry transcription")
if recording or retry_voice:
    with st.spinner("Transcribing recording..."):
        result = transcribe_wav(st.session_state.voice_audio)
    st.session_state.voice_result = result
    if result.text:
        st.session_state.transcript = result.text
voice_result = st.session_state.get("voice_result")
if voice_result:
    if voice_result.error:
        st.error(voice_result.error)
    else:
        st.caption("Recording transcribed. Review the text below before saving.")
st.text_area("Review before saving (edit anything the mic misheard):", key="transcript", height=80)
btn_col, clear_col = st.columns([1, 5])
interpret_clicked = btn_col.button("Interpret command", type="primary", disabled=not st.session_state.transcript.strip())
clear_col.button("Clear", on_click=clear_transcript)

if interpret_clicked:
    requested_preview = ("transcript",)

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
# Remount on month/data changes and successful approvals. Reusing the iframe
# through an approval-triggered rerun can leave the calendar blank until reload.
events = calendar_events(assignments, PROVIDER_COLOR)
calendar_revision = st.session_state.get("calendar_revision", 0)
calendar_key = f"scheduler_calendar_{year}_{month}_{calendar_revision}_{changes.input_signature(events)[:16]}"
st_calendar(
    events=events,
    options={
        "initialView": "dayGridMonth", "initialDate": f"{year:04d}-{month:02d}-01",
        "headerToolbar": {"left": "title", "center": "", "right": ""},
        "showNonCurrentDates": False, "navLinks": False,
        "editable": False, "displayEventTime": False, "height": 650,
    },
    callbacks=[], key=calendar_key,
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
    if st.button("Preview copy day", disabled=not day_targets):
        requested_preview = ("day", src, day_targets)
chosen_id, entry_targets = None, []
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
        if st.button("Preview copy assignment", disabled=not entry_targets):
            requested_preview = ("entry", chosen_id, entry_targets)

# Evaluate all inputs after rendering every editor. A stale preview is discarded
# before any approval button can execute, even if two widgets change together.
source_inputs = {
    "transcript": st.session_state.transcript,
    "voice_revision": st.session_state.get("voice_revision", 0),
    "copy_day_source": src, "copy_day_targets": day_targets,
    "copy_entry_id": chosen_id, "copy_entry_targets": entry_targets,
    "calendar_month": (year, month), "today": today,
}
pending = st.session_state.get("pending_changes")
if pending and pending.input_signature != changes.input_signature(source_inputs):
    st.session_state.pop("pending_changes", None)
    pending = None
    st.info("Inputs changed. The previous preview was discarded; generate a new preview.")
if requested_preview:
    if requested_preview[0] == "transcript":
        pending = changes.preview_add(st.session_state.transcript, PROVIDERS, LOCATIONS, source_inputs)
    else:
        pending = changes.preview_copy(*requested_preview, source_inputs)
    st.session_state.pending_changes = pending
if pending:
    st.divider()
    st.subheader("4. Review proposed changes")
    st.caption(pending.operation + ". Nothing is saved until you approve.")
    totals = {status: sum(row.status == status for row in pending.rows) for status in ("valid", "conflict", "invalid")}
    st.write(f"{len(pending.rows)} assignments: {totals['valid']} valid, "
             f"{totals['conflict']} conflicts, {totals['invalid']} invalid.")
    if pending.rows:
        st.dataframe(changes.preview_table(pending), hide_index=True, width="stretch")
    for issue in pending.issues:
        st.error(issue)
    if not pending.can_approve:
        st.warning("Approval is blocked. Correct the inputs and generate a new preview.")
    approve_col, edit_col, cancel_col = st.columns(3)
    if approve_col.button("Approve changes", type="primary", disabled=not pending.can_approve):
        outcome = changes.approve(pending, source_inputs)
        st.session_state.pop("pending_changes", None)
        save_feedback(pending.operation, outcome.results, outcome.message, outcome.status)
        if outcome.status == "approved":
            st.session_state.calendar_revision = st.session_state.get("calendar_revision", 0) + 1
            if pending.operation == "Add schedule":
                st.session_state.clear_transcript_after_save = True
        # A successful ADD resets the entire draft on the next run. Other
        # outcomes refresh the iframe while keeping any unsaved voice draft.
        if not (outcome.status == "approved" and pending.operation == "Add schedule"):
            old_key = f"voice_recorder_{st.session_state.get('mic_revision', 0)}"
            st.session_state.pop(old_key + "_output", None)
            st.session_state.mic_revision = st.session_state.get("mic_revision", 0) + 1
        st.rerun()
    if edit_col.button("Edit"):
        st.session_state.pop("pending_changes", None)
        st.session_state.review_notice = "Preview discarded. Edit the source inputs above, then generate a new preview."
        st.rerun()
    if cancel_col.button("Cancel"):
        st.session_state.pop("pending_changes", None)
        st.session_state.review_notice = "Proposed changes cancelled. Nothing was saved."
        st.rerun()
else:
    notice = st.session_state.pop("review_notice", None)
    if notice:
        st.info(notice)
