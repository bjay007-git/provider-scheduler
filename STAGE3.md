# Stage 3: shared multi-provider ADD patterns

The transcript editor now uses a conservative deterministic parser. One command
can name multiple providers sharing **one location**, with a shared date/session
pattern or explicit date-first session clauses. The whole command must be understood; unknown providers,
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
| `Khan CGH 01/06/2027` | January 6, 2027, Full day; slash dates are always month first |
| `Khan CGH 2027-01-06 morning and 2027-01-07 afternoon` | January 6 AM and January 7 PM; sessions stay attached to their dates |
| `Khan CGH 01/07/2027 afternoon and 01/06/2027 morning` | Same two assignments in reversed clause order |
| `Khan CGH 2027-01-06; 2027-01-07 afternoon` | January 6 Full day and January 7 PM; semicolon makes the omitted session explicit |

Provider lists accept `and`, commas, or `&`; full names, unique surnames, and
unique initials resolve exactly. Location names/acronyms and unique name stems
without a final Hospital/Clinic/Medical Center suffix are accepted. For example,
City General resolves to City General Hospital. Ambiguous stems/surnames need
the full official name. Fuzzy suggestions are informational: correct the text
to an exact master-data alias before an assignment is proposed.

Sessions preserve the established behavior: omitted means Full day; explicit
morning/AM and afternoon/PM means Full day, including reversed order and plurals.
`morning or afternoon` requires clarification. Full day counts once and saves
AM and PM rows together. Date-first clauses may have different sessions, with
the same providers and location inherited by each clause. Different locations
and per-provider session patterns require separate commands.

Attach sessions after the date or date list. Separate dated clauses with `and`,
a comma, or a semicolon. A session followed by another dated clause does not
apply to that next clause. Omitted sessions in clearly separated clauses mean
Full day. A plain date list followed by one session pattern still shares that
pattern: `2027-01-06 and 2027-01-07 afternoons` means PM on both dates. Use
`2027-01-06; 2027-01-07 afternoons` for Full day on the first date and PM on the
second. Unclear associations or unsupported leftovers block the whole command.

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
- Use `MM/DD/YYYY`, zero-padded `YYYY-MM-DD`, or a written month name.
  Slash dates always mean month/day/year: `01/06/2027` is January 6, 2027.
  There is no DD/MM/YYYY fallback: `13/01/2027` is invalid. Impossible dates,
  invalid months and invalid leap days are rejected using the same real-date
  validation for every format. Other numeric formats remain unsupported.
  Preview dates use written months and four-digit years (e.g. `January 06, 2027`);
  frozen assignments, database storage and calendar event dates remain ISO.
  Invalid dates, reversed/incomplete ranges, unmatched clauses, and ranges with
  no matching weekdays all block approval with an explanation.
- This is a documented grammar, not general language understanding. Ordinal
  suffixes (`December 4th, 2026`) are accepted; spell-out day numbers, arbitrary
  time ranges, exceptions, multiple location clauses, and per-provider mixed
  sessions are unsupported. Per-date sessions use the explicit clauses above.
  DELETE/MODIFY and voice COPY commands
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
   `Khan CGH 2027-01-06 morning and afternoon 2027-01-07`,
   `Khan CGH 13/01/2027`, and `Khan CGH 2027-01-07 to 2027-01-06`.
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

## Verification run: 2026-11-09 through 2026-11-12 (original Stage 3 requirements)

This historical run preceded the month-first and mixed-clause changes below.
Its rejection results for valid slash dates and explicit mixed clauses describe
the previous requirements; use the current checklist above and the addendum
below for the new behavior.

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

## Parser requirements addendum: month-first dates and dated sessions

This change is separate from Stage 4. At implementation start there was no
Stage 4 work in the working tree. Existing preview validation, transaction
boundaries, approval receipts, and source-input invalidation are reused.

The current manual checklist replaces the old rejection expectations for valid
MM/DD/YYYY dates and explicit mixed date/session clauses. Prior verification
results above remain historical; the following browser checks are **pending**.
Use a fresh disposable source-only launch from the instructions above, with
the updated files. The previously running instance uses an older source copy.
Do not use the original database. These explicit dates do not depend on today.

1. Interpret `Khan CGH 01/06/2027`. Expect one Full day assignment displayed as
   **January 06, 2027**. Interpret ISO and written-month versions separately;
   expect identical date/session/provider/location. Cancel each preview.
2. Interpret `Khan CGH 2027-01-06 morning and 2027-01-07 afternoon`.
   Expect two logical assignments: **January 06, 2027 AM** and
   **January 07, 2027 PM**, official provider/location names, no writes.
   Cancel. Repeat with slash dates and reversed clause order:
   `Khan CGH 01/07/2027 afternoon and 01/06/2027 morning`.
   Expect the same two assignments. Edit/Cancel must still save nothing.
3. Interpret `Khan CGH 01/06/2027 morning and afternoon and 01/07/2027`.
   Expect two Full day assignments, including the clause with omitted session.
   Cancel. Interpret `Khan CGH 01/06/2027; 01/07/2027 afternoon`.
   Expect January 6 Full day and January 7 PM. Cancel.
4. Separately try `Khan CGH 13/01/2027`, `Khan CGH 04/31/2027`, and
   `Khan CGH 02/29/2027`: all must block approval, never swap month/day.
   `Khan CGH 02/29/2028` must preview February 29, 2028 Full day. Cancel.
5. Try `Khan CGH 01/06/2027 morning and afternoon 01/07/2027` and
   `Khan CGH 01/06/2027 morning and 01/07/2027 afternoon except holidays`.
   Each must block the complete command; no recognized subset may be approved.
6. On the fresh database, preview `Khan CGH 01/06/2027 morning and
   2027-01-07 afternoon` in tab A. In tab B approve
   `Khan RVC 01/07/2027 afternoon`. Tab A approval must reject the whole batch,
   require a new review, and add no January 6 row. Regenerate: January 6 is valid,
   January 7 conflicts, and approval remains disabled for both assignments.
7. Interpret `Khan CGH 01/08/2027 morning and 01/09/2027 afternoon`.
   Approve once: expect two added logical assignments/two database rows, exact
   AM/PM calendar labels and persistent feedback. Reinterpret: two conflicts,
   approval disabled, no duplicate rows. Recheck microphone/draft reset.

Automated coverage uses temporary databases and fixed reference dates. It
includes both clause orders and date formats, retained ISO/written formats,
leap-year rules, same-date combined sessions, omitted sessions, deduplication,
limits, unsupported/unclear clauses, exact preview text, no interpretation or
cancellation writes, changed inputs, one-assignment conflicts, stale conflicts,
whole-batch rollback, and same-token duplicate protection. Browser results
for this addendum are not claimed until the user performs the checks.

Validation: **107 automated tests passed**, including all Stage 1/2/3
regressions and Streamlit AppTest checks (calendar/microphone widgets stubbed).
No new live-browser checks were performed for this addendum.
