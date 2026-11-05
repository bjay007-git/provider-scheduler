# Stage 2: preview and approval

All schedule operations in the UI now use a common review flow. Editing a
transcript, interpreting it, previewing copies, Edit, and Cancel never write
schedule entries. A preview lists each exact ADD assignment with official names,
date, session, and validation status. Full day is one assignment backed by two
rows. Recurrence still expands from today through the current month's end.
An explicit "morning and afternoon" or "AM and PM" also means Full day.
Alternatives such as "morning or afternoon" remain ambiguous and block approval.

Approve changes is disabled for empty, invalid, conflicting, or overlapping
batches. Changing any transcript/copy source/target, a new voice recording, or
the displayed month discards the pending preview. Edit and Cancel also discard
it and preserve the source inputs so they can be corrected.

Approval rechecks the exact proposed assignments under `BEGIN IMMEDIATE`.
Provider/location details, practice membership, eligibility mappings, bookings,
and copy-source snapshots are checked before inserting. One failure saves no
assignments and removes the pending approval; generate a new preview to retry.
The whole batch, including every full day's AM and PM rows, commits together.
An `approved_batches` receipt in that same transaction prevents duplicate
application of a batch, including simultaneous submissions and later retries.
The additive table is created by `init_db`; existing schedules are not migrated.

Completed results survive Streamlit reruns; approval refreshes the calendar.
Pending previews live only in the current session. A new browser session needs
a new preview. The existing Stage 1 database helper APIs remain available for
compatibility, but the app's write path is the approval transaction.

`changes.Assignment` carries an action and explicit IDs/date/session/source;
`ChangeSet` contains immutable reviewed assignments, source snapshots, and a
batch ID. Current transcript and copy adapters share validation and approval.
Future command adapters can produce the same structure, but only ADD actions
are supported in this stage. No DELETE/MODIFY or LLM integration was added.

## Automated checks

From the repository in PowerShell:

```powershell
& '.\.venv ProviderScheduler\Scripts\python.exe' -m unittest discover -s tests -v
```

Every test uses a temporary database. Coverage includes read-only interpretation,
Edit/Cancel/input invalidation, exact recurrence/copy expansion, official labels,
successful approvals, intra-batch conflicts, new bookings, removed mappings,
renamed locations, changed copy sources, rollback of sessions and receipts,
duplicate/concurrent approval, calendar refresh, voice, and Stage 1 regressions.
Browser microphone capture and calendar rendering still need manual verification.

## Disposable manual test launch

Run this in your own PowerShell terminal so speech recognition has normal
network access. It copies source only; the original database stays untouched.
The previous Stage 1 test server on port 8502 can keep running separately.

```powershell
Set-Location 'C:\Users\bjaya\Desktop\Tiatech\Scheduler\Code'
$stage2Source = (Get-Location).Path
$stage2Python = Join-Path $stage2Source '.venv ProviderScheduler\Scripts\python.exe'
$stage2Scratch = Join-Path ([System.IO.Path]::GetTempPath()) ('scheduler-stage2-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $stage2Scratch | Out-Null
Copy-Item -LiteralPath app.py,db.py,nlu_parser.py,scheduling.py,voice.py,changes.py -Destination $stage2Scratch
Write-Output "Disposable test directory: $stage2Scratch"
Push-Location -LiteralPath $stage2Scratch
try {
    & $stage2Python -m streamlit run app.py --server.address=127.0.0.1 --server.port=8503 --server.headless=true --browser.gatherUsageStats=false
} finally {
    Pop-Location
}
```

Open **http://127.0.0.1:8503**. Stop with Ctrl+C in the launch terminal.

## Manual checklist

1. Type `Khan at CGH 2026-12-01` and click **Interpret command**. Expect one
   valid ADD row: **Dr. Sarah Khan**, **City General Hospital**, **2026-12-01**,
   **Full day**. Nothing is yet on the calendar. Click **Edit**, change to
   December 2, reinterpret, then **Cancel**. Neither date should be scheduled.
2. Interpret the December 1 input again and **Approve changes**. Expect one
   added assignment; the calendar grid must remain visible without a browser
   refresh, and December's calendar shows `SK-CGH`. Rerun or navigate away
   and back: the result remains and no duplicate is inserted. Reinterpreting
   the same command now shows a conflict and disables approval.
   Also try an ADD using the microphone: after approval, Start speaking must
   remain visible, and the transcript, audio player, and Retry transcription
   button must clear. Record a second command without refreshing the browser.
   Rejected approvals and Cancel preserve the draft for correction; Clear
   removes both the transcript and recording. Copy approvals preserve an
   unrelated unsaved voice draft.
3. Interpret `Khan at LKH 2026-12-03` and
   `Khan at CGH 2026-12-03 morning or afternoon` separately. Each must show an
   invalid reason and disabled approval. Correct the input; the old preview
   must disappear before a new one can be approved.
4. Interpret a valid December 3 assignment. Before approving, change the
   transcript, a copy source/target, or the displayed month. Approval disappears;
   regenerate the preview. Repeat with a fresh microphone recording to check
   that voice also invalidates a pending approval.
5. Interpret `Ortiz at CGH every Monday afternoons`. Review the exact dates
   from today to month-end, cancel once, then regenerate and approve. Expect one
   assignment per previewed date, with no dates added on the cancellation.
6. In December, select the December 1 full day and choose December 4 and 5.
   **Preview copy assignment** must show two Full day rows without saving.
   Cancel once, then preview again and approve: two added assignments.
   **Preview copy day** from December 1 onto December 6 should behave the same
   way with one proposed/added assignment.
7. Open a second tab to this disposable app. In tab A, interpret
   `Khan at CGH 2026-12-07`. In tab B, interpret and approve
   `Khan at RVC 2026-12-07 afternoon`. Return to tab A and approve its still-visible
   preview. Expect **nothing saved** and a request for a new review. December 7
   must contain only `SK-RVC-PM`; no CGH AM row may have been inserted.
8. For a stale copy-source check, approve `Khan at CGH 2026-12-08 morning`.
   In tab A preview copying that assignment onto December 9. In tab B approve
   `Khan at CGH 2026-12-08 afternoon`. Tab A's approval must reject the changed
   source. Regenerating the copy preview now shows Full day.

Mapping changes and forced transaction failures are covered by automated tests;
they need no manual changes to either the original or disposable database.
