"""Read-only proposals and atomic approval against temporary databases only."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import changes
from changes import Assignment
import db
from scheduling import FULL_DAY


class ChangeSetCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db_patch = patch.object(db, 'DB_PATH', str(Path(temp.name) / 'approval.sqlite3'))
        db_patch.start()
        self.addCleanup(db_patch.stop)
        db.init_db()
        db.seed_mock_data()
        self.inputs = {'transcript': 'Khan CGH tomorrow'}

    def stored(self, table='schedule_entries'):
        with db.get_conn() as conn:
            return [tuple(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY rowid')]

    def preview(self, assignments=None):
        return changes.preview_assignments(assignments or [Assignment('ADD', 1, 1, '2026-12-01', FULL_DAY)],
                                            'Add schedule', self.inputs)

    def test_interpretation_has_official_names_and_never_writes(self):
        preview = changes.preview_add('Khan at CGH 2026-12-01', db.get_providers(), db.get_locations(), self.inputs)
        self.assertTrue(preview.can_approve)
        self.assertEqual(changes.preview_table(preview)[0], {
            'Action': 'ADD', 'Provider': 'Dr. Sarah Khan', 'Location': 'City General Hospital',
            'Date': 'December 01, 2026', 'Session': 'Full day', 'Validation status': 'Valid: Ready for approval.'})
        self.assertEqual(self.stored(), [])
        self.assertEqual(self.stored('approved_batches'), [])

    def test_approve_single_and_full_day(self):
        for index, session in enumerate(('AM', FULL_DAY)):
            with self.subTest(session=session):
                preview = self.preview([Assignment('ADD', 1, 1, f'2026-12-0{index + 1}', session)])
                result = changes.approve(preview, self.inputs)
                self.assertEqual(result.status, 'approved')
                self.assertEqual(len(result.results), 1)
        self.assertEqual(len(self.stored()), 3)
        self.assertEqual(len(self.stored('approved_batches')), 2)

    def test_recurring_preview_expands_then_approves_exact_dates(self):
        preview = changes.preview_add('Khan at CGH every Monday', db.get_providers(), db.get_locations(),
                                      self.inputs, today=date(2026, 11, 5))
        self.assertEqual([r.assignment.entry_date for r in preview.rows],
                         ['2026-11-09', '2026-11-16', '2026-11-23', '2026-11-30'])
        self.assertEqual(self.stored(), [])
        result = changes.approve(preview, self.inputs)
        self.assertEqual(result.status, 'approved')
        self.assertEqual(len(result.results), 4)
        self.assertEqual(len(self.stored()), 8)

    def test_invalid_recurring_rule_is_blocked(self):
        for days, start, end in [([7], '2026-11-01', '2026-11-30'),
                                  ([], '2026-11-01', '2026-11-30'),
                                  ([0], '2026-11-30', '2026-11-01')]:
            preview = changes.preview_recurrence(1, 1, 'AM', days, start, end, self.inputs)
            self.assertFalse(preview.can_approve)
            self.assertTrue(preview.issues)
            self.assertEqual(changes.approve(preview, self.inputs).status, 'rejected')
        self.assertEqual(self.stored(), [])

    def test_copy_day_and_assignment_preview_preserve_full_day(self):
        db.add_entry(1, 1, '2026-12-01', FULL_DAY)
        source_id = self.stored()[1][0]  # Selecting either half copies the full day.
        before = self.stored()
        preview = changes.preview_copy('entry', source_id, ['2026-12-02', '2026-12-03'], self.inputs)
        self.assertEqual(len(preview.rows), 2)
        self.assertEqual(self.stored(), before)
        self.assertTrue(all(r.assignment.session == FULL_DAY for r in preview.rows))
        self.assertEqual(changes.approve(preview, self.inputs).status, 'approved')
        db.add_entry(2, 1, '2026-12-01', 'AM')
        preview = changes.preview_copy('day', '2026-12-01', ['2026-12-04'], self.inputs)
        self.assertEqual(len(preview.rows), 2)
        result = changes.approve(preview, self.inputs)
        self.assertEqual(result.status, 'approved')
        self.assertEqual(len(result.results), 2)
        self.assertEqual(len(self.stored()), 10)

    def test_invalid_conflicting_and_overlapping_batches_never_partially_save(self):
        db.add_entry(1, 1, '2026-12-01', 'PM')
        before = self.stored()
        for invalid in [Assignment('ADD', 1, 1, '2026-12-01', FULL_DAY),
                        Assignment('ADD', 1, 3, '2026-12-01', 'AM'),
                        Assignment('ADD', 1, 1, '2026-02-30', 'AM'),
                        Assignment('DELETE', 1, 1, '2026-12-03', 'AM')]:
            with self.subTest(invalid=invalid):
                preview = self.preview([Assignment('ADD', 2, 1, '2026-12-02', 'AM'), invalid])
                self.assertFalse(preview.can_approve)
                self.assertEqual(changes.approve(preview, self.inputs).status, 'rejected')
                self.assertEqual(self.stored(), before)
        preview = self.preview([Assignment('ADD', 1, 1, '2026-12-02', FULL_DAY),
                                Assignment('ADD', 1, 2, '2026-12-02', 'PM')])
        self.assertFalse(preview.can_approve)
        self.assertIn('Overlapping', preview.rows[1].reason)

    def test_changed_inputs_require_new_preview(self):
        preview = self.preview()
        self.assertEqual(changes.approve(preview, {'transcript': 'Changed'}).status, 'rejected')
        self.assertEqual(self.stored(), [])

    def test_new_conflict_rejects_entire_batch(self):
        preview = self.preview([Assignment('ADD', 1, 1, '2026-12-01', FULL_DAY),
                                Assignment('ADD', 2, 1, '2026-12-02', 'AM')])
        db.add_entry(2, 3, '2026-12-02', 'AM')
        before = self.stored()
        result = changes.approve(preview, self.inputs)
        self.assertEqual(result.status, 'rejected')
        self.assertIn('Nothing saved', result.message)
        self.assertEqual(self.stored(), before)
        self.assertEqual(self.stored('approved_batches'), [])

    def test_removed_mapping_rejects_entire_batch(self):
        preview = self.preview()
        with db.get_conn() as conn:
            conn.execute('DELETE FROM provider_location_map WHERE provider_id=1 AND location_id=1')
        result = changes.approve(preview, self.inputs)
        self.assertEqual(result.status, 'rejected')
        self.assertIn('not allowed', result.results[0].message)
        self.assertEqual(self.stored(), [])

    def test_metadata_change_requires_review(self):
        preview = self.preview()
        with db.get_conn() as conn:
            conn.execute("UPDATE locations SET name='Renamed hospital' WHERE id=1")
        result = changes.approve(preview, self.inputs)
        self.assertEqual(result.status, 'rejected')
        self.assertIn('details changed', result.message)
        self.assertEqual(self.stored(), [])

    def test_stale_copy_source_requires_review(self):
        db.add_entry(1, 1, '2026-12-01', 'AM')
        source_id = self.stored()[0][0]
        day_preview = changes.preview_copy('day', '2026-12-01', ['2026-12-02'], self.inputs)
        entry_preview = changes.preview_copy('entry', source_id, ['2026-12-03'], self.inputs)
        db.add_entry(1, 1, '2026-12-01', 'PM')
        before = self.stored()
        for preview in [day_preview, entry_preview]:
            result = changes.approve(preview, self.inputs)
            self.assertEqual(result.status, 'rejected')
            self.assertIn('Copy source changed', result.message)
            self.assertEqual(self.stored(), before)

    def test_missing_and_empty_copy_sources_cannot_approve(self):
        for kind, value in [('entry', 999), ('day', '2026-12-01'), ('day', 'invalid')]:
            preview = changes.preview_copy(kind, value, ['2026-12-02'], self.inputs)
            self.assertFalse(preview.can_approve)
            self.assertTrue(preview.issues)

    def test_mid_batch_failure_rolls_back_every_session_and_receipt(self):
        preview = self.preview([Assignment('ADD', 1, 1, '2026-12-01', FULL_DAY),
                                Assignment('ADD', 2, 1, '2026-12-02', FULL_DAY)])
        with db.get_conn() as conn:
            conn.execute("""CREATE TRIGGER fail_last BEFORE INSERT ON schedule_entries
                            WHEN NEW.provider_id=2 AND NEW.half_day='PM'
                            BEGIN SELECT RAISE(ABORT, 'forced batch failure'); END""")
        result = changes.approve(preview, self.inputs)
        self.assertEqual(result.status, 'error')
        self.assertTrue(all(r.status == 'error' for r in result.results))
        self.assertEqual(self.stored(), [])
        self.assertEqual(self.stored('approved_batches'), [])

    def test_receipt_failure_also_rolls_back_schedule_rows(self):
        preview = self.preview()
        with db.get_conn() as conn:
            conn.execute("""CREATE TRIGGER fail_receipt BEFORE INSERT ON approved_batches
                            BEGIN SELECT RAISE(ABORT, 'forced receipt failure'); END""")
        self.assertEqual(changes.approve(preview, self.inputs).status, 'error')
        self.assertEqual(self.stored(), [])

    def test_repeat_approval_is_durably_idempotent(self):
        preview = self.preview()
        self.assertEqual(changes.approve(preview, self.inputs).status, 'approved')
        before = self.stored()
        self.assertEqual(changes.approve(preview, self.inputs).status, 'already_applied')
        self.assertEqual(self.stored(), before)
        self.assertEqual(len(self.stored('approved_batches')), 1)
        altered_row = replace(preview.rows[0], assignment=replace(preview.rows[0].assignment, entry_date='2026-12-02'))
        self.assertEqual(changes.approve(replace(preview, rows=(altered_row,)), self.inputs).status, 'rejected')

    def test_simultaneous_approvals_apply_only_once(self):
        preview = self.preview()
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(lambda _: changes.approve(preview, self.inputs).status, range(2)))
        self.assertCountEqual(statuses, ['approved', 'already_applied'])
        self.assertEqual(len(self.stored()), 2)

    def test_paired_sessions_use_full_day_atomicity_when_one_half_conflicts(self):
        db.add_entry(1, 2, '2026-12-04', 'PM')
        before = self.stored()
        for phrase in ['morning and afternoon', 'AM and PM']:
            preview = changes.preview_add(f'Khan CGH 2026-12-04 {phrase}', db.get_providers(),
                                          db.get_locations(), self.inputs)
            self.assertEqual(preview.rows[0].assignment.session, FULL_DAY)
            self.assertEqual(preview.rows[0].status, 'conflict')
            self.assertFalse(preview.can_approve)
            self.assertEqual(changes.approve(preview, self.inputs).status, 'rejected')
            self.assertEqual(self.stored(), before)

    def test_conflict_reasons_describe_existing_single_and_full_day_bookings(self):
        db.add_entry(1, 1, '2026-12-01', 'AM')
        db.add_entry(1, 1, '2026-12-02', 'PM')
        db.add_entry(1, 1, '2026-12-03', FULL_DAY)
        db.add_entry(1, 1, '2026-12-04', 'AM')
        db.add_entry(1, 2, '2026-12-04', 'PM')
        for day, expected in [('2026-12-01', 'AM'), ('2026-12-02', 'PM'),
                              ('2026-12-03', 'Full day (AM and PM)'), ('2026-12-04', 'AM and PM')]:
            with self.subTest(day=day):
                preview = self.preview([Assignment('ADD', 1, 1, day, FULL_DAY)])
                self.assertEqual(preview.rows[0].status, 'conflict')
                self.assertEqual(preview.rows[0].reason, f'Provider already booked for {expected}.')
        self.assertTrue(self.preview([Assignment('ADD', 1, 1, '2026-12-01', 'PM')]).can_approve)
        self.assertTrue(self.preview([Assignment('ADD', 1, 1, '2026-12-02', 'AM')]).can_approve)

    def test_copy_conflict_reports_full_day_without_writes(self):
        db.add_entry(1, 1, '2026-12-01', FULL_DAY)
        db.add_entry(1, 1, '2026-12-06', FULL_DAY)
        before = self.stored()
        preview = changes.preview_copy('day', '2026-12-01', ['2026-12-06'], self.inputs)
        self.assertFalse(preview.can_approve)
        self.assertEqual(changes.preview_table(preview)[0]['Validation status'],
                         'Conflict: Provider already booked for Full day (AM and PM).')
        self.assertEqual(changes.approve(preview, self.inputs).status, 'rejected')
        self.assertEqual(self.stored(), before)


if __name__ == '__main__':
    unittest.main()
