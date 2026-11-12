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

## Verification run: 2026-11-09 through 2026-11-12

- Branch `feature/scheduling-enhancements`; implementation commit `d47c1b3`.
- Actual reference date reported by the app's Python process: **2026-11-09**.
- Fresh disposable app: **http://127.0.0.1:8504**. Source directory:
  `C:\Users\bjaya\AppData\Local\Temp\scheduler-stage3-161578ef-b03d-4c54-a902-35dc8e4dd5d2`.
- The lower assignment limit is **40 only in this disposable server process**;
  the repository default remains 500. Original database and Microsoft/ untouched.
- Initial read-only database baseline: **0 schedule rows, 0 approval receipts**.
- Browser-access host is unavailable. Native Chrome control was denied with
  "Computer Use was not approved to use Google Chrome". Browser checks are
  user-performed and recorded as passed only after user confirmation.

| Verification | Status | Evidence |
|---|---|---|
| Setup and original database preservation | Pass (shell/read-only checks) | Correct branch/commit; server health 200; original SHA-256 unchanged |
| Automated Stage 1/2/3 suites | Pass (automated only) | 95 tests; temporary databases |
| Multiple providers, weekdays, rest of month | Pass (user-performed browser check) | User confirmed 32 valid AM assignments: official provider/location names and Nov 9–13, 16–20, 23–27, 30; not approved |
| Date lists, ranges, Mon/Wed/Fri, next month | Pass (user browser) | Exact dates/counts confirmed; inclusive ranges, leap day, year boundary and default recurrence range |
| Omitted and combined sessions, deduplication | Pass (user browser + read-only) | Full day defaults, AM and PM, reversed words; repeated provider/date reduced to two assignments |
| Unknown/uncertain names and unsupported clauses | Pass (user browser) | Unknown/fuzzy providers, multiple locations, mixed sessions, alternatives, DELETE/MODIFY blocked |
| Synthetic ambiguous surnames/location aliases | Pass (automated only); browser not run | Temporary master-data fixtures in tests; disposable browser master data not modified |
| Invalid dates/ranges and provider-location mappings | Pass (user browser) | Numeric/invalid/reversed/no-matching dates and Rao/RVC mapping blocked approval |
| Disposable assignment limit | Pass (user browser) | Two providers next month rejected at limit 40 without truncation |
| Preview and no writes before approval | Pass (browser + read-only checks) | User left the matching preview unapproved; database remains at 0 schedule rows and 0 receipts |
| Edit/Cancel and input invalidation | Pass (user browser + read-only checks) | On Nov 12, user confirmed Edit removed the preview, changed mornings to afternoons, regenerated 26 PM assignments, then Cancel removed the preview; schedules/receipts remained 0/0 |
| Atomic approval and stale conflicts | Pass (user browser + read-only) | Full day, recurrence and both copy flows saved exact batches; late conflict rolled back all four proposed assignments |
| Duplicate command / repeated batch token | Pass (user browser / automated respectively) | Reinterpretation blocked all four conflicts; same-token idempotency tested automatically |
| Calendar labels and microphone transcription | Pass (user browser) | Full day and AM/PM labels, month/year navigation, transcription, editable draft and post-save clearing/remount confirmed |

No application-code fixes were needed in this verification run. Stage 4 has not
started; nothing pushed. The documentation commit records these results on top
of implementation commit `d47c1b3`.

### Resumed 2026-11-12

- Restarted the same disposable database at http://127.0.0.1:8504 with limit 40.
- Health check returned 200. Read-only baseline remains 0 schedule rows and
  0 approval receipts; original database SHA-256 remains unchanged.
- The app reference date is now 2026-11-12. The first example now produces
  26 logical assignments: each provider on Nov 12, 13, 16–20, 23–27, and 30.
  Earlier Nov 9 preview observations remain historical results.
- At restart, Edit/Cancel was the next check; the previous pending preview was
  cleared and regenerated. Its confirmed result is recorded below.
- Calendar recovery incident (resolved): the user reported a pandas initialization
  error. The first server traceback showed Windows Application Control blocking
  a pandas DLL; later imports reported partially initialized pandas. A fresh
  Python process successfully imported pandas 3.1.0 and constructed a DataFrame.
  Restarted only the disposable server; health 200, schedules/receipts still
  0/0, original database hash unchanged. No application code or dependencies
  changed. The user subsequently confirmed calendar rendering after refresh.
- User confirmed the calendar grid recovered after refresh. Confirmed the
  regenerated 26-assignment AM preview, Edit, replacement with 26 PM assignments,
  and Cancel. Read-only verification after Cancel: 0 schedules, 0 receipts.
  Subsequent microphone and calendar checks are recorded below.
- Full-day approval passed (user browser + read-only checks): preview showed
  four Full day assignments for Khan/Rao at CGH on Jan 4/5, 2027. User confirmed
  approval feedback 4 added, 0 skipped, 0 failed and combined calendar labels
  after navigation to January. Database contains exactly the eight expected
  AM/PM rows and one approval receipt. Duplicate-command check is recorded below.
- Reinterpreting the same approved command passed: user confirmed four
  full-day conflicts with approval disabled. Read-only counts remain eight
  schedule rows and one receipt. Durable replay of the same batch token remains
  covered by automated tests; this browser check verifies duplicate-command
  conflict blocking.
- Deduplication passed (user browser): repeated Khan and Jan 6, 2027 produced
  exactly two AM assignments (Khan/Rao). Preview cancelled without approval.
- Invalid mapping and inclusive two-day range passed (user browser): Khan/Rao
  at RVC Jan 6–7, 2027 produced four Full day assignments; Rao's mapping errors
  blocked the whole batch. Read-only counts remain eight rows and one receipt.
- User-performed browser rejection checks passed for unknown provider
  `Khan and Unknown CGH tomorrow`, uncertain spelling `Kahn and Rao CGH tomorrow`,
  multiple locations `Khan CGH and RVC tomorrow`, and mixed sessions
  `Khan CGH 2027-01-06 morning and 2027-01-07 afternoon`. Each blocked approval
  without accepting a recognized subset or collapsing mixed sessions to Full
  day. Read-only counts remain eight schedule rows and one receipt.
- User confirmed blocking errors for ambiguous numeric date `01/06/2027`,
  reversed range `2027-01-07 to 2027-01-06`, and invalid date `2027-02-30`.
- Next month with Mon/Wed/Fri passed (user browser, reference Nov 12, 2026):
  `Khan CGH next month Mon/Wed/Fri mornings` yielded 13 valid AM assignments
  on Dec 2, 4, 7, 9, 11, 14, 16, 18, 21, 23, 25, 28, 30. Not approved.
  Read-only database counts remain eight schedule rows and one receipt.
- User-performed browser checks passed: Dec 5–6, 2026 filtered to weekdays
  reports no matching dates; two providers for all of next month exceeds the
  disposable limit 40 and rejects without truncation; Feb 28–Mar 1, 2028
  produces three Full day assignments including leap day; Dec 31, 2026–Jan 2,
  2027 produces three Full day assignments across the year boundary. None
  approved. Read-only counts remain eight schedule rows and one receipt.
- Recurrence without explicit range passed (user browser + read-only check):
  `Ortiz CGH every Monday and Wednesday afternoons` expanded to Nov 16, 18,
  23, 25, 30, 2026 using Nov 12 reference date. User approved and confirmed
  5 added / 0 skipped / 0 failed and five PM calendar labels. Read-only query
  confirms exactly these five Ortiz rows; totals now 13 rows and two receipts.
- Stale-conflict atomicity passed (user browser + read-only checks): Tab A
  previewed four Full day Khan/Rao assignments at CGH Jan 8/9, 2027. Tab B
  approved Rao at LKH Jan 9 PM (one row). Tab A approval reported "Validation
  changed. Nothing saved; generate and review a new preview." Counts remain
  14 rows and three receipts; Jan 8/9 contains only Tab B's Rao/LKH/Jan 9/PM
  row. None of Tab A's batch was written.
- Copy-day passed (user browser + read-only): copied Jan 4, 2027 to Jan 6;
  preview contained two valid Full day Khan/Rao assignments at CGH. User
  confirmed approval feedback 2 added / 0 skipped / 0 failed and combined
  calendar labels. Exactly four AM/PM rows were added; totals 18 rows and
  four receipts.
- Copy-single-assignment passed (user browser + read-only): Khan/CGH Jan 4
  copied to Jan 7, 2027 as one Full day preview. User confirmed 1 added /
  0 skipped / 0 failed and one combined label. Both AM/PM rows saved; totals
  20 schedule rows and five receipts.
- User browser checks passed: reversed `afternoon and morning` produced one
  Full day preview for Khan/CGH Jan 10, 2027; changing the transcript date to
  Jan 11 without Edit automatically discarded the preview and approval controls.
  `morning or afternoon` required clarification. DELETE and MODIFY commands
  were rejected with "Only ADD commands are supported in this stage."
  Read-only totals remain 20 rows/five receipts, with no Jan 10/11 rows.
- Live voice flow passed (user browser): microphone populated the editable
  transcript with calendar visible. User corrected/interpreted
  `Khan at City General 2027-01-12 morning`, confirmed one valid AM preview,
  approved, and confirmed 1 added / 0 skipped / 0 failed, Jan 12 AM label,
  transcript clearing, and microphone/calendar visible without refresh.
- Final read-only verification: **21 schedule rows, six approval receipts**.
  The complete saved row set exactly matches the six approved batches; no
  unexpected or missing rows. Original database SHA-256 remains
  `D8C8383D235F4EA3B70A38060E60D7F6B5312C9E1DF545E69572A0C511B93E96`.
- All 95 automated regression tests passed again on Nov 12 using temporary
  databases. Browser checks were performed by the user, not browser automation.
  Synthetic name ambiguity and replay of the same batch token remain
  automated-only coverage; they are not claimed as browser-tested.
