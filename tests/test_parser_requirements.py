"""Month-first dates and independently dated session clauses; temp databases only."""

from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import changes
import command_parser as parser
import db
import nlu_parser
from scheduling import FULL_DAY


class ParserRequirementsCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db_patch = patch.object(db, 'DB_PATH', str(Path(temp.name) / 'parser.sqlite3'))
        db_patch.start()
        self.addCleanup(db_patch.stop)
        db.init_db()
        db.seed_mock_data()
        self.providers, self.locations = db.get_providers(), db.get_locations()
        self.inputs = {'transcript': 'test'}

    def parse(self, tail, **kwargs):
        return parser.parse_command('Khan CGH ' + tail, self.providers, self.locations,
                                    date(2026, 11, 12), **kwargs)

    def preview(self, tail):
        return changes.preview_add('Khan CGH ' + tail, self.providers, self.locations,
                                   self.inputs, today=date(2026, 11, 12))

    def stored(self, table='schedule_entries'):
        with db.get_conn() as conn:
            return [tuple(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY rowid')]

    def test_month_first_dates_lists_ranges_and_existing_formats(self):
        for tail in ('01/06/2027', '2027-01-06', 'January 6, 2027', '6 January 2027'):
            with self.subTest(tail=tail):
                result = self.parse(tail)
                self.assertFalse(result.error, result.error)
                self.assertEqual(result.date_sessions, ((date(2027, 1, 6), FULL_DAY),))
        for tail in ('01/06/2027 and 01/07/2027', '01/06/2027 to 01/07/2027',
                     '01/06/2027 and 2027-01-07', 'between 01/06/2027 and 01/07/2027'):
            result = self.parse(tail)
            self.assertFalse(result.error, result.error)
            self.assertEqual(result.dates, (date(2027, 1, 6), date(2027, 1, 7)))

    def test_impossible_month_first_dates_never_fall_back_to_day_first(self):
        for tail in ('13/01/2027', '31/12/2027', '00/06/2027', '01/00/2027',
                     '01/32/2027', '04/31/2027', '02/29/2027', '02/29/2100',
                     '02/30/2028', '2027-02-29', 'February 29, 2027'):
            with self.subTest(tail=tail):
                preview = self.preview(tail)
                self.assertFalse(preview.can_approve)
                self.assertEqual(preview.rows, ())
                self.assertEqual(changes.approve(preview, self.inputs).status, 'rejected')
        self.assertEqual(self.parse('02/29/2028').dates, (date(2028, 2, 29),))
        self.assertEqual(self.parse('02/29/2000').dates, (date(2000, 2, 29),))
        self.assertEqual(self.stored(), [])

    def test_legacy_single_date_adapter_uses_same_month_first_policy(self):
        self.assertEqual(nlu_parser.parse_single_date('Khan CGH 01/06/2027'), date(2027, 1, 6))
        self.assertEqual(nlu_parser.parse_single_date('Khan CGH 02/29/2028'), date(2028, 2, 29))
        for invalid in ('13/01/2027', '31/12/2027', '02/29/2027', '00/01/2027'):
            self.assertIsNone(nlu_parser.parse_single_date('Khan CGH ' + invalid))

    def test_both_clause_orders_and_date_formats_keep_sessions_local(self):
        for first, second in (('2027-01-06', '2027-01-07'), ('01/06/2027', '01/07/2027'),
                              ('01/06/2027', '2027-01-07'), ('January 6, 2027', 'January 7, 2027')):
            for tail in (f'{first} morning and {second} afternoon',
                         f'{second} afternoon and {first} morning'):
                with self.subTest(tail=tail):
                    preview = self.preview(tail)
                    self.assertTrue(preview.can_approve, preview.issues)
                    self.assertEqual([(r.assignment.entry_date, r.assignment.session) for r in preview.rows],
                                     [('2027-01-06', 'AM'), ('2027-01-07', 'PM')])
                    self.assertEqual([r['Date'] for r in changes.preview_table(preview)],
                                     ['January 06, 2027', 'January 07, 2027'])
                    self.assertEqual(self.stored(), [])
                    self.assertEqual(self.stored('approved_batches'), [])

    def test_combined_sessions_on_same_date_and_omission_within_clear_clause(self):
        for wording in ('morning and afternoon', 'afternoon and morning', 'AM and PM', 'PM and AM'):
            result = self.parse(f'01/06/2027 {wording} and 2027-01-07 afternoon')
            self.assertFalse(result.error, result.error)
            self.assertEqual(result.date_sessions, ((date(2027, 1, 6), FULL_DAY), (date(2027, 1, 7), 'PM')))
        for tail in ('2027-01-06 morning and 2027-01-07',
                     '2027-01-07; 2027-01-06 morning'):
            result = self.parse(tail)
            self.assertFalse(result.error, result.error)
            self.assertEqual(result.date_sessions, ((date(2027, 1, 6), 'AM'), (date(2027, 1, 7), FULL_DAY)))

    def test_shared_providers_dedup_and_limit_apply_to_complete_batch(self):
        tail = '01/06/2027 AM and 01/07/2027 PM and 01/06/2027 morning'
        result = self.parse(tail, max_assignments=2)
        self.assertFalse(result.error, result.error)
        self.assertEqual(len(result.date_sessions), 2)
        self.assertIn('limit', self.parse(tail, max_assignments=1).error)
        preview = changes.preview_add('Khan and Rao CGH ' + tail, self.providers, self.locations, self.inputs)
        self.assertEqual(len(preview.rows), 4)
        self.assertEqual({r.provider_name for r in preview.rows}, {'Dr. Sarah Khan', 'Dr. Michael Rao'})

    def test_unclear_or_unsupported_portion_blocks_whole_command(self):
        for tail in ('01/06/2027 morning or afternoon and 01/07/2027 PM',
                     '01/06/2027 AM and banana', '01/06/2027 AM and 02/30/2027 PM',
                     '01/06/2027 morning and Rao afternoon',
                     '01/06/2027 morning and RVC 01/07/2027 afternoon',
                     '01/06/2027 morning and afternoon 01/07/2027',
                     '01/06/2027 morning; nonsense', '01/06/2027;'):
            with self.subTest(tail=tail):
                preview = self.preview(tail)
                self.assertFalse(preview.can_approve, tail)
                self.assertEqual(preview.rows, (), tail)
        self.assertEqual(self.stored(), [])

    def test_exact_batch_approval_and_repeat_protection(self):
        preview = self.preview('01/06/2027 morning and 2027-01-07 afternoon')
        self.assertEqual(changes.approve(preview, self.inputs).status, 'approved')
        with db.get_conn() as conn:
            rows = [tuple(r) for r in conn.execute('SELECT entry_date,half_day FROM schedule_entries ORDER BY entry_date')]
        self.assertEqual(rows, [('2027-01-06', 'AM'), ('2027-01-07', 'PM')])
        self.assertEqual(changes.approve(preview, self.inputs).status, 'already_applied')
        self.assertEqual(len(self.stored()), 2)
        self.assertEqual(len(self.stored('approved_batches')), 1)

    def test_one_conflict_blocks_other_valid_assignment_and_stale_batch(self):
        for stale in (False, True):
            with self.subTest(stale=stale):
                with db.get_conn() as conn:
                    conn.execute('DELETE FROM schedule_entries')
                if not stale:
                    db.add_entry(1, 2, '2027-01-07', 'PM')
                preview = self.preview('01/06/2027 morning and 2027-01-07 afternoon')
                if stale:
                    self.assertTrue(preview.can_approve)
                    db.add_entry(1, 2, '2027-01-07', 'PM')
                else:
                    self.assertEqual([r.status for r in preview.rows], ['valid', 'conflict'])
                self.assertEqual(changes.approve(preview, self.inputs).status, 'rejected')
                self.assertEqual(len(self.stored()), 1)
                self.assertEqual(self.stored('approved_batches'), [])

    def test_mixed_batch_rollback_and_changed_inputs(self):
        preview = self.preview('01/06/2027 morning and 2027-01-07 afternoon')
        self.assertEqual(changes.approve(preview, {'transcript': 'edited'}).status, 'rejected')
        self.assertEqual(self.stored(), [])
        with db.get_conn() as conn:
            conn.execute("""CREATE TRIGGER stop_second BEFORE INSERT ON schedule_entries
                            WHEN NEW.entry_date='2027-01-07'
                            BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
        self.assertEqual(changes.approve(preview, self.inputs).status, 'error')
        self.assertEqual(self.stored(), [])
        self.assertEqual(self.stored('approved_batches'), [])


if __name__ == '__main__':
    unittest.main()
