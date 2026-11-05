# Stage 1 verification

This is the historical Stage 1 checklist. The current application adds an
Interpret command / Approve changes step; use [STAGE2.md](STAGE2.md) for current
launch instructions and approval checks.

Run automated tests from the project directory (PowerShell):

```powershell
& '.\.venv ProviderScheduler\Scripts\python.exe' -m unittest discover -s tests -v
```

Tests create temporary SQLite databases and patch `db.DB_PATH` before initializing
or running the app. They never initialize, seed, or write the local POC database.
Streamlit integration tests stub the microphone and calendar browser widgets;
check their browser rendering manually below.

For manual testing without changing the existing local database, copy only the
source files into a disposable directory and launch Streamlit there:

```powershell
$stage1Source = (Get-Location).Path
$stage1Python = Join-Path $stage1Source '.venv ProviderScheduler\Scripts\python.exe'
$stage1Scratch = Join-Path ([System.IO.Path]::GetTempPath()) ('scheduler-stage1-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $stage1Scratch | Out-Null
Copy-Item -LiteralPath app.py,db.py,nlu_parser.py,scheduling.py,voice.py,changes.py -Destination $stage1Scratch
Push-Location -LiteralPath $stage1Scratch
try { & $stage1Python -m streamlit run app.py } finally { Pop-Location }
```

Manual checklist (type transcripts or use the microphone):

- On localhost, allow microphone access, click Start speaking, say a schedule,
  then click Stop. The captured audio should be playable and the recognized
  text should appear in the editor. Google speech recognition requires internet
  access from the computer running Streamlit. Unclear speech and service errors
  now show a reason; Retry transcription reuses the recorded audio. Recordings
  are kept in the current session's memory, not in SQLite or local audio files.

- Add `Khan at CGH 2026-12-01`. Expect one added assignment and one `SK-CGH`
  label without AM/PM after navigating to December 2026.
- Repeat it. Expect zero added, one skipped, zero failed, with a booking reason.
  Navigate away and back; the feedback should remain visible.
- Add `Khan at CGH 2026-12-02 mornings` and `Khan at RVC 2026-12-02 afternoons`.
  Expect two separate labels. Try `Khan at CGH 2026-12-02` and verify the full-day
  request skips both halves without changing either existing assignment.
- Try `Khan at LKH 2026-12-03`, an invalid ISO date such as `2026-13-01`, and
  `Khan at CGH 2026-12-03 morning or afternoon`. Expect failures with reasons
  and no added assignments. `evenings` and clock times should also fail.
- Select the December 1 full-day assignment in the copy selector and copy it
  to December 3. Expect one added assignment and one combined label. Copy a
  whole day and verify the counts describe assignments, including full days.
- Navigate December to January and back. Check the title, loaded events, and
  copy selector change together. Today should return to the current month.
  There should be no competing navigation buttons inside the calendar.
- Add `Ortiz at CGH every Monday afternoons`. Verify occurrences from today
  through the current month's end. Repeat to check skipped occurrence counts.

Implementation notes:

- No schema migration is needed. Full days use existing AM and PM rows; matching
  rows from before Stage 1 are combined for display and copying as well.
- The parser defaults an omitted session only for ADD commands (including the
  existing implicit ADD phrasing and recurring ADDs). Database APIs require an
  explicit `AM`, `PM`, or `FULL` session. Other command types remain unsupported.
- `add_entry`, copy functions, and recurrence return `WriteResult` objects with
  `added`, `conflict`, `invalid`, or `error` status. Conflicts count as skipped;
  invalid input and database errors count as failed. A rejected rule or source
  is one failed operation; valid occurrences/copies are counted individually.
- Each full-day assignment is atomic. Bulk operations may succeed for some
  assignments while skipping or failing others; each result retains its reason.
- Installed `streamlit-calendar` 1.4.0's Python wrapper documents `dateClick`,
  `eventClick`, `eventChange`, `eventsSet`, `select`, and `eventMouseEnter`.
  Its bundled frontend wires these callbacks but does not expose a `datesSet`
  month-change callback. `eventsSet` reports event changes, so it is unsuitable
  as a reliable month-navigation signal. Streamlit controls own the month;
  a month-specific widget key reapplies `initialDate` when it changes.
- No restriction on past dates was added. Recurrence remains an expansion
  through the current month's end; rules are not saved.
