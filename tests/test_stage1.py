"""Stage 1 regression tests. Every database lives in TemporaryDirectory."""

import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import db
import nlu_parser as nlu
from scheduling import FULL_DAY, assignment_label, calendar_events, group_assignments, result_counts, shift_month


class DatabaseCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(db, "DB_PATH", str(Path(self.temp.name) / "test.sqlite3"))
        self.db_patch.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.db_patch.stop)
        db.init_db()
        db.seed_mock_data()

    def rows(self):
        with db.get_conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM schedule_entries ORDER BY entry_date, half_day")]

    def test_validation_and_past_dates(self):
        for provider, location, day, session in [
            (999, 1, "2026-11-01", "AM"), (1, 999, "2026-11-01", "AM"),
            (1, 3, "2026-11-01", "AM"), (1, 1, "2026-02-30", "AM"),
            (1, 1, "2026-2-01", "AM"), (1, 1, "nonsense", "AM"),
            (1, 1, None, "AM"), (1, 1, "2026-11-01", "night"),
            (1, 1, "2026-11-01", None),
        ]:
            with self.subTest(provider=provider, location=location, day=day, session=session):
                self.assertEqual(db.add_entry(provider, location, day, session).status, "invalid")
        self.assertEqual(self.rows(), [])
        self.assertEqual(db.add_entry(1, 1, "2000-02-29", "AM").status, "added")

    def test_same_practice_even_with_mapping(self):
        with db.get_conn() as conn:
            conn.execute("INSERT INTO practices VALUES (2, 'Other')")
            conn.execute("INSERT INTO locations VALUES (4, 2, 'Other hospital', 'OTH')")
            conn.execute("INSERT INTO provider_location_map VALUES (1,4)")
        result = db.add_entry(1, 4, "2026-11-01", "AM")
        self.assertEqual(result.status, "invalid")
        self.assertIn("same practice", result.message)

    def test_duplicate_and_full_day_conflicts_are_atomic(self):
        for occupied in ("AM", "PM"):
            with self.subTest(occupied=occupied):
                day = "2026-11-01" if occupied == "AM" else "2026-11-02"
                self.assertEqual(db.add_entry(1, 2, day, occupied).status, "added")
                self.assertEqual(db.add_entry(1, 1, day, occupied).status, "conflict")
                self.assertEqual(db.add_entry(1, 1, day, FULL_DAY).status, "conflict")
                self.assertEqual(sum(r['entry_date'] == day for r in self.rows()), 1)
        self.assertEqual(db.add_entry(1, 1, "2026-11-03", FULL_DAY).status, "added")
        self.assertEqual(len(self.rows()), 4)
        self.assertEqual(db.add_entry(1, 1, "2026-11-03", FULL_DAY).status, "conflict")

    def test_database_error_rolls_back_both_halves(self):
        with db.get_conn() as conn:
            conn.execute("""CREATE TRIGGER fail_pm BEFORE INSERT ON schedule_entries
                            WHEN NEW.half_day='PM' BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
        result = db.add_entry(1, 1, "2026-11-01", FULL_DAY)
        self.assertEqual(result.status, "error")
        self.assertIn("test failure", result.message)
        self.assertEqual(self.rows(), [])

    def test_connection_errors_are_not_conflicts(self):
        with patch.object(db, "get_conn", side_effect=sqlite3.OperationalError("unavailable")):
            self.assertEqual(db.add_entry(1, 1, "2026-11-01", "AM").status, "error")
            self.assertEqual(db.copy_day("2026-11-01", ["2026-11-02"])[0].status, "error")
            self.assertEqual(db.add_recurring(1, 1, "AM", [0], "2026-11-01", "2026-11-30")[0].status, "error")

    def test_copy_full_day_from_either_id(self):
        db.add_entry(1, 1, "2026-11-01", FULL_DAY)
        ids = [r['id'] for r in self.rows()]
        for entry_id, target in zip(ids, ("2026-11-02", "2026-11-03")):
            results = db.copy_entry(entry_id, [target])
            self.assertEqual(result_counts(results), {'added': 1, 'skipped': 0, 'failed': 0})
        db.add_entry(1, 2, "2026-11-04", "PM")
        self.assertEqual(db.copy_entry(ids[0], ["2026-11-04"])[0].status, "conflict")
        self.assertEqual(sum(r['entry_date'] == '2026-11-04' for r in self.rows()), 1)
        self.assertEqual(db.copy_entry(ids[0], ["bad"])[0].status, "invalid")
        self.assertEqual(db.copy_entry(999, ["2026-11-05"])[0].status, "invalid")

    def test_copy_day_counts_logical_assignments_and_revalidates(self):
        db.add_entry(1, 1, "2026-11-01", FULL_DAY)
        db.add_entry(2, 1, "2026-11-01", "AM")
        results = db.copy_day("2026-11-01", ["2026-11-02", "2026-11-02", "bad"])
        self.assertEqual(result_counts(results), {'added': 2, 'skipped': 2, 'failed': 2})
        with db.get_conn() as conn:
            conn.execute("DELETE FROM provider_location_map WHERE provider_id=1 AND location_id=1")
        results = db.copy_day("2026-11-01", ["2026-11-03"])
        self.assertEqual(result_counts(results), {'added': 1, 'skipped': 0, 'failed': 1})
        self.assertEqual(db.copy_day("invalid", ["2026-11-03"])[0].status, "invalid")
        self.assertEqual(db.copy_day("2026-11-30", ["2026-12-01"]), [])

    def test_copy_split_locations_remain_separate(self):
        db.add_entry(1, 1, "2026-11-01", "AM")
        db.add_entry(1, 2, "2026-11-01", "PM")
        self.assertEqual(len(db.copy_day("2026-11-01", ["2026-11-02"])), 2)
        copied = [r for r in self.rows() if r['entry_date'] == '2026-11-02']
        self.assertEqual([(r['location_id'], r['half_day']) for r in copied], [(1, 'AM'), (2, 'PM')])

    def test_recurrence_validation_before_writes(self):
        for days, start, end in [([], '2026-11-01', '2026-11-30'),
                                  ([0, 7], '2026-11-01', '2026-11-30'),
                                  ([-1], '2026-11-01', '2026-11-30'),
                                  ([True], '2026-11-01', '2026-11-30'),
                                  (['1'], '2026-11-01', '2026-11-30'),
                                  (None, '2026-11-01', '2026-11-30'),
                                  ([0], '2026-12-01', '2026-11-01'),
                                  ([0], '2026-02-30', '2026-11-01')]:
            with self.subTest(days=days, start=start, end=end):
                self.assertEqual(db.add_recurring(1, 1, FULL_DAY, days, start, end)[0].status, 'invalid')
        self.assertEqual(db.add_recurring(1, 3, FULL_DAY, [0], '2026-11-01', '2026-11-30')[0].status, 'invalid')
        self.assertEqual(self.rows(), [])

    def test_recurring_full_day_conflict_and_inclusive_bounds(self):
        db.add_entry(1, 2, "2026-11-09", "PM")
        results = db.add_recurring(1, 1, FULL_DAY, [0, 0], date(2026, 11, 2), date(2026, 11, 16))
        self.assertEqual([r.entry_date for r in results], ['2026-11-02', '2026-11-09', '2026-11-16'])
        self.assertEqual(result_counts(results), {'added': 2, 'skipped': 1, 'failed': 0})
        self.assertEqual(len(self.rows()), 5)
        self.assertEqual(db.add_recurring(1, 1, 'AM', [1], '2026-11-02', '2026-11-02'), [])

    def test_month_queries_display_and_year_transition(self):
        db.add_entry(1, 1, '2026-12-31', FULL_DAY)
        db.add_entry(1, 1, '2027-01-01', 'AM')
        db.add_entry(1, 2, '2027-01-01', 'PM')
        december = group_assignments(db.get_month_entries(2026, 12))
        january = group_assignments(db.get_month_entries(*shift_month(2026, 12, 1)))
        self.assertEqual(len(december), 1)
        self.assertEqual(assignment_label(december[0]), 'SK-CGH')
        self.assertEqual({assignment_label(a) for a in january}, {'SK-CGH-AM', 'SK-RVC-PM'})
        events = calendar_events(december, {1: '#123456'})
        self.assertEqual(events[0]['title'], 'SK-CGH')
        self.assertEqual(events[0]['start'], '2026-12-31T08:00:00')
        self.assertEqual(events[0]['end'], '2026-12-31T17:00:00')
        self.assertEqual(shift_month(2027, 1, -1), (2026, 12))


class ParserCase(unittest.TestCase):
    def parse(self, session='', command='Add'):
        return nlu.parse_transcript(f'{command} Khan at CGH 2026-11-04 {session}',
                                    [{'id': 1, 'full_name': 'Dr. Sarah Khan', 'initials': 'SK'}],
                                    [{'id': 1, 'name': 'City General Hospital', 'acronym': 'CGH'}],
                                    today=date(2026, 11, 1))

    def test_add_omission_and_supported_sessions(self):
        for phrase, expected in [('', FULL_DAY), ('full day', FULL_DAY), ('all day', FULL_DAY),
                                  ('morning', 'AM'), ('mornings', 'AM'), ('afternoons', 'PM'),
                                  ('a.m.', 'AM'), ('p.m.', 'PM'), ('first half', 'AM')]:
            with self.subTest(phrase=phrase):
                parsed = self.parse(phrase)
                self.assertIsNone(parsed['error'])
                self.assertEqual(parsed['half_day'], expected)
                self.assertEqual(parsed['entry_date'], date(2026, 11, 4))

    def test_reject_contradictions_unsupported_and_non_add(self):
        for phrase in ['morning or afternoon', 'AM or PM', 'morning/afternoon', 'AM PM',
                       'morning and afternoon only morning', 'not morning and afternoon',
                       'full day morning', 'evenings',
                       'night', 'half day', 'midday', '9:00', '9-5', '8 AM', 'not morning',
                       'at 9', 'both halves', 'full time', '9\u20135', '9 o\u2019clock', '9 to 5']:
            with self.subTest(phrase=phrase):
                self.assertIsNotNone(self.parse(phrase)['error'])
        for command in ['Delete', 'Modify', 'Copy', 'Move', 'Cancel', 'Reschedule']:
            self.assertIsNotNone(self.parse(command=command)['error'])
            self.assertIsNone(self.parse(command=command)['half_day'])

    def test_recurring_plural_session(self):
        parsed = self.parse('every Monday and Wednesday afternoons')
        self.assertTrue(parsed['is_recurring'])
        self.assertEqual(parsed['weekdays'], [0, 2])
        self.assertEqual(parsed['half_day'], 'PM')

    def test_conjoined_sessions_mean_full_day(self):
        for phrase in ['morning and afternoon', 'mornings and afternoons', 'AM and PM',
                       'a.m. and p.m.', 'afternoon and morning', 'PM and AM',
                       'morning & afternoon', 'both the morning and the afternoon',
                       'first half and second half']:
            with self.subTest(phrase=phrase):
                parsed = self.parse(phrase)
                self.assertIsNone(parsed['error'])
                self.assertEqual(parsed['half_day'], FULL_DAY)
                self.assertEqual(parsed['entry_date'], date(2026, 11, 4))
        recurring = self.parse('every Monday and Wednesday mornings and afternoons')
        self.assertTrue(recurring['is_recurring'])
        self.assertEqual(recurring['weekdays'], [0, 2])
        self.assertEqual(recurring['half_day'], FULL_DAY)

    def test_invalid_iso_date_is_not_reinterpreted(self):
        for value in ['2026-13-01', '2026-02-30', '2026-00-04']:
            self.assertIsNone(nlu.parse_single_date(f'Khan CGH {value}', date(2026, 11, 1)))


if __name__ == '__main__':
    unittest.main()
