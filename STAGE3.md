# Stage 3: shared multi-provider ADD patterns

The transcript editor now uses a conservative deterministic parser. One command
can name multiple providers sharing **one location, one session pattern, and
one date pattern**. The whole command must be understood; unknown providers,
ambiguous names, uncertain spelling matches, and unsupported clauses require
correction. No recognized subset is silently accepted.

Interpret command expands every provider/date combination into the existing
Stage 2 preview. Official database names, exact dates, sessions, logical totals,
and mapping/conflict reasons remain visible. Any invalid mapping or conflict
blocks the entire batch. Only Approve changes saves; the existing transaction,
stale-preview checks, full-day atomicity, and durable duplicate-submission
receipts remain in use. Copy previews use those same safeguards.

## Supported examples

| Command | Meaning |
|---|---|
| `Dr. Khan and Dr. Rao, City General, weekday mornings, rest of the month.` | Both providers, CGH, AM, matching weekdays from today through month-end |
| `Khan and Rao at CGH on 2026-12-01, 2026-12-03 and 2026-12-05` | Six Full day assignments |
| `Khan, Rao, Khan at CGH December 1, 3 and 5, 2026 AM and PM` | Same six assignments; repeated Khan is deduplicated |
| `Khan and Rao CGH from 2026-12-01 through 2026-12-07 mornings` | Inclusive range, both providers, AM |
| `Khan CGH December 1-5, 2026 afternoons` | Five PM assignments |
| `Khan CGH between December 1, 2026 and December 5, 2026` | Inclusive written-date range, Full day |
| `Khan CGH next month Mon/Wed/Fri mornings` | Matching days from next month's first through last day |
| `Ortiz CGH every Monday and Wednesday afternoons` | Matching weekdays from today to month-end |
| `Khan CGH 2028-02-28 to 2028-03-01` | Three Full day assignments, including leap day |
| `Khan CGH tomorrow afternoon and morning` | One Full day assignment |

Provider lists accept `and`, commas, or `&`; full names, unique surnames, and
unique initials resolve exactly. Location names/acronyms and unique name stems
without a final Hospital/Clinic/Medical Center suffix are accepted. For example,
City General resolves to City General Hospital. Ambiguous stems/surnames need
the full official name. Fuzzy suggestions are informational: correct the text
to an exact master-data alias before an assignment is proposed.

Sessions preserve the established behavior: omitted means Full day; explicit
morning/AM and afternoon/PM means Full day, including reversed order and plurals.
`morning or afternoon` requires clarification. Full day counts once and saves
AM and PM rows together. Different locations or session patterns must be
interpreted and approved as separate commands.

## Deterministic date rules

- The parser requires an explicit `reference_date`. The UI supplies today's
  date at interpretation; tests supply fixed dates. Approval saves the frozen
  preview dates and never reinterprets the transcript.
- Rest of the month is reference date through that month's last day, inclusive.
  Next month is its first through last day, including December/January and leap
  years. Recurrence without a range uses reference date through month-end.
- Named weekdays, `weekdays`, and slash lists filter explicit ranges/months.
  `every Monday and Wednesday`, `Mon/Wed/Fri`, and plural weekdays imply recurrence.
  A lone bare weekday means its next occurrence on or after the reference date;
  `next Monday` is strictly after that date. `this Monday` is on or after it.
- Today, tomorrow, and yesterday are relative to the explicit reference date.
  Past dates remain permitted. Written dates without a year use the reference
  year; use explicit years for cross-year written ranges.
- Explicit lists support ISO dates, repeated written dates, and written month
  shorthand such as `December 1, 3 and 5, 2026`. Range endpoints are inclusive;
  repeated dates and providers are deduplicated before assignment counting.
- Use zero-padded `YYYY-MM-DD` or a written month name. Slash dates and other
  numeric date formats are rejected rather than assigned a guessed locale.
  Invalid dates, reversed/incomplete ranges, unmatched clauses, and ranges with
  no matching weekdays all block approval with an explanation.
- This is a documented grammar, not general language understanding. Ordinal
  suffixes (`December 4th, 2026`) are accepted; spell-out day numbers, arbitrary
  time ranges, exceptions, multiple location clauses, and per-provider or
  per-date mixed sessions are unsupported. DELETE/MODIFY and voice COPY commands
  remain rejected; existing copy controls are available.

## Configurable expansion limit

`SCHEDULER_MAX_ASSIGNMENTS` defaults to **500 logical assignments**. It must be
a positive integer. One full day counts once; two providers on three dates
count as six. The parser stops expansion with a blocking error if the complete
deduplicated request exceeds the limit; it never returns a truncated batch.
The limit also applies to structured/copy previews and is rechecked on approval.
Lowering it after preview may require a new, smaller preview.

Set the environment variable before starting Streamlit, for example:

```powershell
$env:SCHEDULER_MAX_ASSIGNMENTS = '500'
```

Restart the server after changing its environment or updating imported source
files. A browser refresh alone may leave old server modules/previews in use.

## Automated verification

```powershell
& '.\.venv ProviderScheduler\Scripts\python.exe' -m unittest discover -s tests -v
```

All database tests use temporary databases. Coverage includes fixed-date
expansion, boundaries/leap years, partial/unknown/ambiguous commands, limits,
official preview names and counts, no writes before approval, invalid mappings,
new conflicts after preview, rollback, duplicate submissions, and the Stage 1
and Stage 2 regression suites. Browser rendering and live microphone capture
are separate manual checks.

## Disposable browser launch

Use a new source-only copy. Do not copy the original database or the Stage 2
test database. Run in a normal terminal to allow speech recognition networking.

```powershell
Set-Location 'C:\Users\bjaya\Desktop\Tiatech\Scheduler\Code'
$stage3Python = Join-Path (Get-Location).Path '.venv ProviderScheduler\Scripts\python.exe'
$stage3Scratch = Join-Path ([System.IO.Path]::GetTempPath()) ('scheduler-stage3-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $stage3Scratch | Out-Null
Copy-Item -LiteralPath app.py,db.py,nlu_parser.py,command_parser.py,changes.py,scheduling.py,voice.py -Destination $stage3Scratch
Write-Output "Disposable directory: $stage3Scratch"
Push-Location -LiteralPath $stage3Scratch
try {
    & $stage3Python -m streamlit run app.py --server.address=127.0.0.1 --server.port=8504 --server.headless=true --browser.gatherUsageStats=false
} finally { Pop-Location }
```

Open **http://127.0.0.1:8504**. Existing servers and their test databases can
remain untouched. Ctrl+C stops this server.

## Manual checklist

1. Interpret the first example above. Review both official provider names,
   official hospital name, AM, and all exact remaining weekdays. For reference
   date **2026-11-07**, expect **32** logical assignments (16 weekdays each).
   Do not approve: Edit, change the transcript, regenerate, then Cancel. Verify
   no entries appeared. Record and interpret a spoken version too.
2. Interpret `Khan and Rao CGH 2027-01-04 and 2027-01-05 AM and PM`. Expect four
   valid Full day rows and no writes. Approve; expect four added assignments
   backed by eight database rows and four combined calendar labels. Reinterpret
   it: all four conflicts block approval. Calendar/mic remain visible and the
   completed voice draft clears without a browser reload.
3. Interpret `Khan, Khan and Rao CGH 2027-01-06 and 2027-01-06 mornings`.
   Expect two logical assignments. Cancel. Interpret
   `Khan and Rao at RVC 2027-01-06 to 2027-01-07`: Rao's invalid mapping must
   block the whole four-assignment batch. Cancel.
4. Interpret each separately: `Khan and Unknown CGH tomorrow`,
   `Kahn and Rao CGH tomorrow`, `Khan CGH and RVC tomorrow`,
   `Khan CGH 2027-01-06 morning and 2027-01-07 afternoon`,
   `Khan CGH 01/06/2027`, and `Khan CGH 2027-01-07 to 2027-01-06`.
   Each must require correction and permit no approval. Existing duplicated
   surname/location aliases are also blocked; synthetic ambiguities are covered
   by automated tests without modifying master data manually.
5. Preview `Khan CGH next month Mon/Wed/Fri mornings` and check its exact dates
   against next month's calendar. Cancel. Interpret
   `Khan CGH 2026-12-05 to 2026-12-06 weekdays`: expect no matching dates.
6. In tab A, preview `Khan and Rao CGH 2027-01-08 and 2027-01-09`. In tab B,
   approve `Rao LKH 2027-01-09 afternoon`. Tab A's stale approval must save
   nothing from its entire four-assignment batch and require a new review.
7. With the default limit, interpret `Khan and Rao CGH 2027-01-01 to 2028-12-31`.
   Expect an oversized-request error, not a partial preview. Alternatively
   restart the disposable server with a limit of 4 and test a six-assignment
   command. Restore the setting afterward.
8. Recheck copy-day/copy-assignment previews and approvals, Edit/Cancel/input
   invalidation, full-day conflicts, and December/January navigation as in
   Stage 2. No saved recurrence rules, month duplication, DELETE/MODIFY, new date
   picker, or LLM integration is part of this stage.
