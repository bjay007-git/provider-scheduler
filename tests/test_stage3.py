"""Stage 3 deterministic grammar and transactional regressions."""

from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import changes
import command_parser as parser
import db
from scheduling import FULL_DAY

PROVIDERS = [
    {'id': 1, 'full_name': 'Dr. Sarah Khan', 'initials': 'SK'},
    {'id': 2, 'full_name': 'Dr. Michael Rao', 'initials': 'MR'},
    {'id': 3, 'full_name': 'Dr. Emily Chen', 'initials': 'EC'},
    {'id': 4, 'full_name': 'Dr. James Ortiz', 'initials': 'JO'},
]
LOCATIONS = [
    {'id': 1, 'name': 'City General Hospital', 'acronym': 'CGH'},
    {'id': 2, 'name': 'Riverside Clinic', 'acronym': 'RVC'},
    {'id': 3, 'name': 'Lakeside Hospital', 'acronym': 'LKH'},
]
REFERENCE = date(2026, 11, 7)


class GrammarCase(unittest.TestCase):
    def parse(self, text, reference=REFERENCE, **kwargs):
        return parser.parse_command(text, PROVIDERS, LOCATIONS, reference, **kwargs)

    def valid(self, text, reference=REFERENCE, **kwargs):
        result = self.parse(text, reference, **kwargs)
        self.assertFalse(result.error, result.error)
        return result

    def invalid(self, text, message=None, **kwargs):
        result = self.parse(text, **kwargs)
        self.assertTrue(result.error, text)
        self.assertEqual(result.provider_ids, ())
        self.assertEqual(result.dates, ())
        if message:
            self.assertIn(message.lower(), result.error.lower())
        return result

    def test_requested_example_shared_provider_pattern(self):
        result = self.valid('Dr. Khan and Dr. Rao, City General, weekday mornings, rest of the month.')
        self.assertEqual(result.provider_ids, (1, 2))
        self.assertEqual(result.location_id, 1)
        self.assertEqual(result.session, 'AM')
        self.assertEqual(len(result.dates), 16)
        self.assertEqual(result.dates[0], date(2026, 11, 9))
        self.assertEqual(result.dates[-1], date(2026, 11, 30))
        self.assertTrue(all(day.weekday() < 5 for day in result.dates))

    def test_provider_forms_and_deduplication(self):
        for providers in ['Khan and Rao', 'Dr. Sarah Khan, Dr. Michael Rao', 'SK & MR', 'Khan, Rao, Khan']:
            with self.subTest(providers=providers):
                result = self.valid(f'{providers} at CGH on 2026-12-01 and 2026-12-01')
                self.assertEqual(result.provider_ids, (1, 2))
                self.assertEqual(result.dates, (date(2026, 12, 1),))

    def test_names_starting_with_dr_are_not_stripped_as_titles(self):
        providers = PROVIDERS + [{'id': 10, 'full_name': 'Dr. Drew Stone', 'initials': 'DS'}]
        result = parser.parse_command('Drew Stone at CGH tomorrow', providers, LOCATIONS, REFERENCE)
        self.assertFalse(result.error)
        self.assertEqual(result.provider_ids, (10,))

    def test_explicit_iso_and_written_lists(self):
        for tail in ['2026-12-01, 2026-12-03 and 2026-12-05',
                     'December 1, 3 and 5, 2026', 'December 1, 3, and 5,2026',
                     '1 December 2026, 3 December 2026 and 5 December 2026']:
            with self.subTest(tail=tail):
                self.assertEqual(self.valid('Khan CGH on ' + tail).dates,
                                 (date(2026, 12, 1), date(2026, 12, 3), date(2026, 12, 5)))

    def test_inclusive_ranges_and_written_shorthand(self):
        for tail in ['from 2026-12-01 to 2026-12-03', '2026-12-01 through 2026-12-03',
                     'between December 1, 2026 and December 3, 2026', 'December 1-3, 2026',
                     'from December 1 to 3, 2026', 'December 1 through December 3, 2026']:
            with self.subTest(tail=tail):
                result = self.valid('Khan CGH ' + tail)
                self.assertEqual(result.dates, (date(2026, 12, 1), date(2026, 12, 2), date(2026, 12, 3)))

    def test_ranges_with_weekday_filters(self):
        result = self.valid('Khan and Rao CGH from 2026-12-01 through 2026-12-07 Mon/Wed/Fri mornings')
        self.assertEqual(result.dates, (date(2026, 12, 2), date(2026, 12, 4), date(2026, 12, 7)))

    def test_rest_of_month_is_inclusive(self):
        result = self.valid('Khan CGH rest of the month', date(2026, 11, 30))
        self.assertEqual(result.dates, (date(2026, 11, 30),))
        self.assertEqual(result.session, FULL_DAY)
        leap = self.valid('Khan CGH rest of this month', date(2028, 2, 28))
        self.assertEqual(leap.dates, (date(2028, 2, 28), date(2028, 2, 29)))

    def test_next_month_year_boundary_and_leap_year(self):
        for reference, first, last, count in [(date(2026, 12, 20), date(2027, 1, 1), date(2027, 1, 31), 31),
                                              (date(2028, 1, 1), date(2028, 2, 1), date(2028, 2, 29), 29),
                                              (date(2027, 1, 1), date(2027, 2, 1), date(2027, 2, 28), 28)]:
            result = self.valid('Khan CGH next month', reference)
            self.assertEqual((result.dates[0], result.dates[-1], len(result.dates)), (first, last, count))

    def test_explicit_cross_year_and_leap_ranges(self):
        result = self.valid('Khan CGH 2026-12-31 to 2027-01-02')
        self.assertEqual(result.dates, (date(2026, 12, 31), date(2027, 1, 1), date(2027, 1, 2)))
        self.assertEqual(len(self.valid('Khan CGH 2028-02-28 to 2028-03-01').dates), 3)

    def test_implicit_recurrence_default_range(self):
        for pattern in ['every Monday and Wednesday', 'Mon/Wed', 'Mondays and Wednesdays']:
            result = self.valid(f'Khan CGH {pattern} afternoons')
            self.assertEqual(result.session, 'PM')
            self.assertEqual(result.dates, tuple(date(2026, 11, d) for d in [9, 11, 16, 18, 23, 25, 30]))
        self.assertEqual(len(self.valid('Khan CGH weekdays').dates), 16)
        self.assertEqual(len(self.valid('Khan CGH Mon/Wed/Fri').dates), 10)

    def test_relative_dates_use_only_explicit_reference(self):
        for phrase, expected in [('today', date(2026, 11, 7)), ('tomorrow', date(2026, 11, 8)),
                                  ('yesterday', date(2026, 11, 6)), ('next Monday', date(2026, 11, 9)),
                                  ('Monday', date(2026, 11, 9))]:
            self.assertEqual(self.valid('Khan CGH ' + phrase).dates, (expected,))
        self.assertEqual(self.valid('Khan CGH next Monday', date(2026, 11, 9)).dates, (date(2026, 11, 16),))
        self.assertEqual(self.valid('Khan CGH December 4', date(2030, 1, 1)).dates, (date(2030, 12, 4),))
        self.assertTrue(self.parse('Khan CGH tomorrow', reference=None).error)

    def test_full_day_combined_and_omitted_session(self):
        for session in ['', 'full day', 'morning and afternoon', 'AM and PM', 'PM and AM',
                        'afternoon and morning', 'mornings and afternoons', 'both the morning and the afternoon']:
            result = self.valid('Khan and Rao CGH 2026-12-01 ' + session)
            self.assertEqual(result.session, FULL_DAY)

    def test_alternatives_and_unsupported_sessions_are_blocked(self):
        for session in ['morning or afternoon', 'AM or PM', 'evenings', 'at 9 AM', 'not morning']:
            self.invalid('Khan CGH 2026-12-01 ' + session)

    def test_no_partial_provider_acceptance(self):
        for names in ['Khan and Dr. Unknown', 'Unknown and Rao', 'Khan, Nobody, Rao', 'Khan and Raox']:
            self.invalid(f'{names} at CGH tomorrow', 'provider')

    def test_ambiguous_surname_and_full_name_disambiguation(self):
        providers = PROVIDERS + [{'id': 9, 'full_name': 'Dr. Adam Khan', 'initials': 'AK'}]
        result = parser.parse_command('Khan and Rao CGH tomorrow', providers, LOCATIONS, REFERENCE)
        self.assertIn('Ambiguous provider', result.error)
        self.assertIn('Dr. Adam Khan', result.error)
        result = parser.parse_command('Dr. Sarah Khan and Rao CGH tomorrow', providers, LOCATIONS, REFERENCE)
        self.assertFalse(result.error)
        self.assertEqual(result.provider_ids, (1, 2))

    def test_fuzzy_matches_need_confirmation_not_automatic_acceptance(self):
        self.invalid('Kahn and Rao at CGH tomorrow', 'Uncertain provider')
        self.invalid('Khan at City Gneral tomorrow', 'Uncertain location')

    def test_ambiguous_location_alias_requires_full_name(self):
        locations = LOCATIONS + [{'id': 8, 'name': 'City General Clinic', 'acronym': 'CGC'}]
        result = parser.parse_command('Khan at City General tomorrow', PROVIDERS, locations, REFERENCE)
        self.assertIn('Ambiguous location', result.error)
        result = parser.parse_command('Khan at City General Hospital tomorrow', PROVIDERS, locations, REFERENCE)
        self.assertFalse(result.error)
        self.assertEqual(result.location_id, 1)

    def test_multiple_locations_and_mixed_clauses_are_not_collapsed(self):
        for text in ['Khan at CGH and RVC tomorrow morning',
                     'Khan at CGH tomorrow morning and Rao at RVC tomorrow afternoon',
                     'Khan at CGH every Monday morning and Wednesday afternoon',
                     'Khan at CGH tomorrow morning and Rao morning',
                     'Khan at CGH and Unknown Clinic tomorrow']:
            self.invalid(text)

    def test_unknown_clauses_are_not_silently_omitted(self):
        for text in ['Khan CGH 2026-12-01 and banana', 'Khan CGH tomorrow except holidays',
                     'Khan CGH December 1, x, 5', 'Khan CGH weekly', 'Khan CGH every',
                     'Khan CGH next month and 2026-12-01', 'Khan CGH every Monday for two months']:
            self.invalid(text)

    def test_invalid_reversed_incomplete_and_ambiguous_dates(self):
        for text in ['2026-02-30', '2026-13-01', '2026-2-3', 'April 31, 2026',
                     '2026-12-03 to 2026-12-01', 'December 5-1, 2026', 'from December 1',
                     '2026-12-01 to', '13/04/2026', '04-12-2026', '12/04', '2026/12/04']:
            self.invalid('Khan CGH ' + text)

    def test_no_matching_dates_helpful_error(self):
        self.invalid('Khan CGH 2026-12-05 through 2026-12-06 weekdays', 'no matching dates')

    def test_delete_modify_and_future_actions_are_rejected(self):
        for action in ['Delete', 'Remove', 'Modify', 'Change', 'Copy', 'Duplicate']:
            self.invalid(action + ' Khan CGH tomorrow', 'Only ADD')

    def test_deduplicated_assignment_limit_rejects_instead_of_truncating(self):
        self.invalid('Khan and Rao CGH next month', 'limit of 4', max_assignments=4)
        result = self.valid('Khan, Khan and Rao CGH 2026-12-01 and 2026-12-01', max_assignments=2)
        self.assertEqual(len(result.provider_ids) * len(result.dates), 2)
        with patch.dict('os.environ', {'SCHEDULER_MAX_ASSIGNMENTS': '1'}):
            self.invalid('Khan and Rao CGH tomorrow', 'limit of 1')
        with patch.dict('os.environ', {'SCHEDULER_MAX_ASSIGNMENTS': 'bad'}):
            self.invalid('Khan CGH tomorrow', 'positive integer')


class BatchCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db_patch = patch.object(db, 'DB_PATH', str(Path(temp.name) / 'stage3.sqlite3'))
        db_patch.start()
        self.addCleanup(db_patch.stop)
        db.init_db()
        db.seed_mock_data()
        self.inputs = {'transcript': 'test'}

    def count(self, table='schedule_entries'):
        with db.get_conn() as conn:
            return conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]

    def preview(self, text):
        return changes.preview_add(text, db.get_providers(), db.get_locations(), self.inputs, today=REFERENCE)

    def test_shared_example_counts_official_names_and_atomic_save(self):
        preview = self.preview('Dr. Khan and Dr. Rao, City General, weekday mornings, rest of the month.')
        self.assertTrue(preview.can_approve, preview.issues)
        self.assertEqual(len(preview.rows), 32)
        self.assertEqual({r.provider_name for r in preview.rows}, {'Dr. Sarah Khan', 'Dr. Michael Rao'})
        self.assertEqual({r.location_name for r in preview.rows}, {'City General Hospital'})
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.count('approved_batches'), 0)
        result = changes.approve(preview, self.inputs)
        self.assertEqual(result.status, 'approved')
        self.assertEqual(len(result.results), 32)
        self.assertEqual(self.count(), 32)
        self.assertEqual(changes.approve(preview, self.inputs).status, 'already_applied')
        self.assertEqual(self.count(), 32)

    def test_duplicate_mentions_produce_one_logical_assignment_each(self):
        preview = self.preview('Khan, Khan and Rao CGH December 1, 1 and 2, 2026 AM and PM')
        self.assertTrue(preview.can_approve, preview.issues)
        self.assertEqual(len(preview.rows), 4)
        self.assertTrue(all(r.assignment.session == FULL_DAY for r in preview.rows))
        self.assertEqual(changes.approve(preview, self.inputs).status, 'approved')
        self.assertEqual(self.count(), 8)

    def test_unknown_provider_blocks_whole_interpretation_without_partial_rows(self):
        preview = self.preview('Khan and Unknown CGH December 1-3, 2026')
        self.assertFalse(preview.can_approve)
        self.assertEqual(preview.rows, ())
        self.assertIn('No partial provider list', preview.issues[0])
        self.assertEqual(self.count(), 0)

    def test_one_invalid_mapping_blocks_whole_multi_provider_batch(self):
        preview = self.preview('Khan and Rao at RVC 2026-12-01 and 2026-12-02')
        self.assertEqual(len(preview.rows), 4)
        self.assertEqual(sum(r.status == 'invalid' for r in preview.rows), 2)
        self.assertEqual(changes.approve(preview, self.inputs).status, 'rejected')
        self.assertEqual(self.count(), 0)

    def test_stale_conflict_prevents_every_provider_and_date_from_saving(self):
        preview = self.preview('Khan and Rao CGH 2026-12-01 to 2026-12-02')
        self.assertTrue(preview.can_approve)
        db.add_entry(2, 3, '2026-12-02', 'PM')
        result = changes.approve(preview, self.inputs)
        self.assertEqual(result.status, 'rejected')
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.count('approved_batches'), 0)

    def test_mid_batch_failure_rolls_back_all_providers_and_both_sessions(self):
        preview = self.preview('Khan and Rao CGH 2026-12-01 and 2026-12-02')
        with db.get_conn() as conn:
            conn.execute("""CREATE TRIGGER stop_last BEFORE INSERT ON schedule_entries
                            WHEN NEW.provider_id=2 AND NEW.entry_date='2026-12-02' AND NEW.half_day='PM'
                            BEGIN SELECT RAISE(ABORT, 'stage3 failure'); END""")
        self.assertEqual(changes.approve(preview, self.inputs).status, 'error')
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.count('approved_batches'), 0)

    def test_limit_applies_to_shared_previews_copies_and_approval(self):
        preview = self.preview('Khan and Rao CGH 2026-12-01 and 2026-12-02')
        with patch.dict('os.environ', {'SCHEDULER_MAX_ASSIGNMENTS': '3'}):
            self.assertEqual(changes.approve(preview, self.inputs).status, 'rejected')
            self.assertFalse(self.preview('Khan and Rao CGH 2026-12-01 and 2026-12-02').can_approve)
        self.assertEqual(self.count(), 0)
        db.add_entry(1, 1, '2026-12-01', FULL_DAY)
        with patch.dict('os.environ', {'SCHEDULER_MAX_ASSIGNMENTS': '1'}):
            copy = changes.preview_copy('day', '2026-12-01', ['2026-12-02', '2026-12-03'], self.inputs)
            self.assertFalse(copy.can_approve)
            self.assertEqual(copy.rows, ())
        self.assertEqual(self.count(), 2)


if __name__ == '__main__':
    unittest.main()
