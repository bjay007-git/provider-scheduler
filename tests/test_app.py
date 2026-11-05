"""Streamlit integration checks with temporary DB and stubbed browser widgets."""

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import db
import streamlit_calendar
import streamlit_mic_recorder
from voice import VoiceResult
from scheduling import FULL_DAY

APP = str(Path(__file__).resolve().parents[1] / 'app.py')


class StreamlitCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db_patch = patch.object(db, 'DB_PATH', str(Path(temp.name) / 'app.sqlite3'))
        db_patch.start()
        self.addCleanup(db_patch.stop)
        calendar_patch = patch.object(streamlit_calendar, 'calendar', return_value={})
        self.calendar = calendar_patch.start()
        self.addCleanup(calendar_patch.stop)
        mic_patch = patch.object(streamlit_mic_recorder, 'mic_recorder', return_value=None)
        self.mic = mic_patch.start()
        self.addCleanup(mic_patch.stop)
        db.init_db()
        db.seed_mock_data()
        self.app = AppTest.from_file(APP, default_timeout=15)

    def button(self, label):
        return next(b for b in self.app.button if b.label == label)

    def assert_clean(self):
        self.assertEqual(len(self.app.exception), 0, str(self.app.exception))

    def test_navigation_loads_calendar_and_selector_across_year(self):
        db.add_entry(1, 1, '2026-12-31', FULL_DAY)
        db.add_entry(1, 2, '2027-01-01', 'PM')
        self.app.session_state['cal_year'] = 2026
        self.app.session_state['cal_month'] = 12
        self.app.run()
        self.assert_clean()
        self.assertEqual(self.calendar.call_args.kwargs['events'][0]['title'], 'SK-CGH')
        self.assertEqual(self.calendar.call_args.kwargs['callbacks'], [])
        self.assertEqual(self.calendar.call_args.kwargs['options']['headerToolbar']['right'], '')
        self.assertEqual(self.app.selectbox[0].options, ['2026-12-31 — SK-CGH'])
        self.button('Next month').click().run()
        self.assert_clean()
        self.assertEqual(self.app.session_state['cal_year'], 2027)
        self.assertEqual(self.app.session_state['cal_month'], 1)
        self.assertEqual(self.calendar.call_args.kwargs['options']['initialDate'], '2027-01-01')
        self.assertEqual(self.app.selectbox[0].options, ['2027-01-01 — SK-RVC-PM'])
        self.button('Previous month').click().run()
        self.assert_clean()
        self.assertEqual(self.app.selectbox[0].options, ['2026-12-31 — SK-CGH'])
        self.button('Today').click().run()
        self.assert_clean()
        self.assertEqual((self.app.session_state['cal_year'], self.app.session_state['cal_month']),
                         (date.today().year, date.today().month))

    def test_add_full_day_feedback_persists_and_duplicate_skips(self):
        self.app.run()
        self.app.text_area[0].input('Add Khan at CGH 2026-11-04').run()
        self.button('Interpret command').click().run()
        self.assertEqual(db.get_month_entries(2026, 11), [])
        self.button('Approve changes').click().run()
        self.assert_clean()
        self.assertIn('1 added, 0 skipped, 0 failed', self.app.success[0].value)
        self.assertEqual(self.app.text_area[0].value, '')
        self.assertEqual(len(db.get_month_entries(2026, 11)), 2)
        self.button('Next month').click().run()
        self.assertIn('1 added, 0 skipped, 0 failed', self.app.success[0].value)
        self.app.text_area[0].input('Khan at CGH 2026-11-04').run()
        self.button('Interpret command').click().run()
        self.assert_clean()
        self.assertTrue(self.button('Approve changes').disabled)
        self.assertIn('Conflict:', self.app.dataframe[0].value.iloc[0]['Validation status'])
        self.assertNotEqual(self.app.text_area[0].value, '')
        self.button('Clear').click().run()
        self.assertEqual(self.app.text_area[0].value, '')

    def test_invalid_session_fails_without_write_and_retains_feedback(self):
        self.app.run()
        self.app.text_area[0].input('Khan CGH 2026-11-04 mornings and afternoons').run()
        self.button('Interpret command').click().run()
        self.assert_clean()
        self.assertTrue(self.button('Approve changes').disabled)
        self.assertIn('Invalid:', self.app.dataframe[0].value.iloc[0]['Validation status'])
        self.assertEqual(db.get_month_entries(2026, 11), [])
        self.app.run()
        self.assertTrue(self.button('Approve changes').disabled)
        self.assertIn('Invalid:', self.app.dataframe[0].value.iloc[0]['Validation status'])

    def test_copy_displayed_full_day_as_one_assignment(self):
        db.add_entry(1, 1, '2026-12-01', FULL_DAY)
        self.app.session_state['cal_year'] = 2026
        self.app.session_state['cal_month'] = 12
        self.app.run()
        targets = next(m for m in self.app.multiselect if m.key.startswith('copyentry_targets'))
        targets.select(date(2026, 12, 2)).run()
        self.button('Preview copy assignment').click().run()
        self.button('Approve changes').click().run()
        self.assert_clean()
        self.assertIn('1 added, 0 skipped, 0 failed', self.app.success[0].value)
        self.assertEqual(len(db.get_month_entries(2026, 12)), 4)
        self.assertEqual(len(self.calendar.call_args.kwargs['events']), 2)

    def test_voice_transcript_remains_editable_without_retranscribing(self):
        self.mic.return_value = {'bytes': b'test wav', 'id': 1}
        with patch('voice.transcribe_wav', return_value=VoiceResult(text='Khan CGH tomorrow')) as transcribe:
            self.app.run()
            self.assert_clean()
            self.assertEqual(self.app.text_area[0].value, 'Khan CGH tomorrow')
            self.mic.return_value = None
            self.app.text_area[0].input('Khan RVC tomorrow morning').run()
            self.assertEqual(self.app.text_area[0].value, 'Khan RVC tomorrow morning')
            self.button('Clear').click().run()
            self.assertEqual(self.app.text_area[0].value, '')
            transcribe.assert_called_once_with(b'test wav')

    def test_voice_failure_is_visible_and_can_retry(self):
        self.app.run()
        self.app.text_area[0].input('Keep my draft').run()
        self.mic.return_value = {'bytes': b'test wav', 'id': 1}
        with patch('voice.transcribe_wav', return_value=VoiceResult(error='Speech service unavailable.')):
            self.app.run()
        self.assert_clean()
        self.assertEqual(self.app.text_area[0].value, 'Keep my draft')
        self.assertEqual(self.app.error[0].value, 'Speech service unavailable.')
        self.mic.return_value = None
        self.app.run()
        self.assertEqual(self.app.error[0].value, 'Speech service unavailable.')
        with patch('voice.transcribe_wav', return_value=VoiceResult(text='Khan CGH tomorrow')):
            self.button('Retry transcription').click().run()
        self.assert_clean()
        self.assertEqual(self.app.text_area[0].value, 'Khan CGH tomorrow')
        self.assertEqual(len(self.app.error), 0)

    def interpret(self, transcript='Khan CGH 2026-12-01'):
        self.app.run()
        self.app.text_area[0].input(transcript).run()
        self.button('Interpret command').click().run()
        self.assert_clean()

    def test_interpret_edit_and_cancel_never_write(self):
        self.interpret()
        self.assertEqual(db.get_month_entries(2026, 12), [])
        preview_id = self.app.session_state['pending_changes'].batch_id
        self.assertEqual(self.app.dataframe[0].value.iloc[0]['Session'], 'Full day')
        self.button('Edit').click().run()
        self.assert_clean()
        self.assertFalse(any(b.label == 'Approve changes' for b in self.app.button))
        self.assertEqual(self.app.text_area[0].value, 'Khan CGH 2026-12-01')
        self.assertEqual(db.get_month_entries(2026, 12), [])
        self.app.text_area[0].input('Khan RVC 2026-12-02 morning').run()
        self.button('Interpret command').click().run()
        self.assertNotEqual(self.app.session_state['pending_changes'].batch_id, preview_id)
        self.button('Cancel').click().run()
        self.assert_clean()
        self.assertEqual(db.get_month_entries(2026, 12), [])
        self.assertFalse(any(b.label == 'Approve changes' for b in self.app.button))
        with db.get_conn() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM approved_batches').fetchone()[0], 0)

    def test_input_change_invalidates_approval_even_when_clicked_together(self):
        self.interpret()
        self.app.text_area[0].set_value('Khan CGH 2026-12-02')
        self.button('Approve changes').click().run()
        self.assert_clean()
        self.assertEqual(db.get_month_entries(2026, 12), [])
        self.assertFalse(any(b.label == 'Approve changes' for b in self.app.button))
        self.assertTrue(any('Inputs changed' in info.value for info in self.app.info))

    def test_unrelated_source_input_and_month_changes_invalidate_preview(self):
        self.interpret()
        self.app.date_input[0].set_value(date(2026, 12, 5)).run()
        self.assertFalse(any(b.label == 'Approve changes' for b in self.app.button))
        self.button('Interpret command').click().run()
        self.button('Next month').click().run()
        self.assert_clean()
        self.assertFalse(any(b.label == 'Approve changes' for b in self.app.button))
        self.assertEqual(db.get_month_entries(2026, 12), [])

    def test_voice_recording_invalidates_existing_preview(self):
        self.interpret()
        self.mic.return_value = {'bytes': b'new wav', 'id': 2}
        with patch('voice.transcribe_wav', return_value=VoiceResult(text='Khan CGH 2026-12-01')):
            self.app.run()
        self.assert_clean()
        self.assertFalse(any(b.label == 'Approve changes' for b in self.app.button))
        self.assertEqual(db.get_month_entries(2026, 12), [])

    def test_conflict_after_preview_requires_review_again(self):
        self.interpret()
        db.add_entry(1, 2, '2026-12-01', 'PM')
        self.button('Approve changes').click().run()
        self.assert_clean()
        self.assertIn('Nothing saved', self.app.error[0].value)
        self.assertEqual(len(db.get_month_entries(2026, 12)), 1)
        self.assertFalse(any(b.label == 'Approve changes' for b in self.app.button))
        self.button('Interpret command').click().run()
        self.assertTrue(self.button('Approve changes').disabled)

    def test_changed_mapping_on_approval_saves_nothing(self):
        self.interpret()
        with db.get_conn() as conn:
            conn.execute('DELETE FROM provider_location_map WHERE provider_id=1 AND location_id=1')
        self.button('Approve changes').click().run()
        self.assert_clean()
        self.assertEqual(db.get_month_entries(2026, 12), [])
        self.assertIn('Nothing saved', self.app.error[0].value)
        self.assertFalse(any(b.label == 'Approve changes' for b in self.app.button))

    def test_recurring_preview_can_cancel_then_approve(self):
        self.interpret('Ortiz CGH every Monday afternoons')
        proposed_dates = list(self.app.dataframe[0].value['Date'])
        self.assertTrue(proposed_dates)
        self.assertEqual(db.get_month_entries(date.today().year, date.today().month), [])
        self.button('Cancel').click().run()
        self.assertEqual(db.get_month_entries(date.today().year, date.today().month), [])
        self.button('Interpret command').click().run()
        self.button('Approve changes').click().run()
        self.assert_clean()
        rows = db.get_month_entries(date.today().year, date.today().month)
        self.assertEqual([row['entry_date'] for row in rows], proposed_dates)
        self.assertEqual(len(self.calendar.call_args.kwargs['events']), len(proposed_dates))

    def test_copy_day_preview_cancel_and_approve_as_whole_batch(self):
        db.add_entry(1, 1, '2026-12-01', FULL_DAY)
        db.add_entry(2, 1, '2026-12-01', 'AM')
        self.app.session_state['cal_year'] = 2026
        self.app.session_state['cal_month'] = 12
        self.app.run()
        self.app.date_input[0].set_value(date(2026, 12, 1)).run()
        targets = next(m for m in self.app.multiselect if m.key == 'copyday_targets')
        targets.select(date(2026, 12, 2)).run()
        self.button('Preview copy day').click().run()
        self.assert_clean()
        self.assertEqual(len(self.app.dataframe[0].value), 2)
        self.assertEqual(len(db.get_month_entries(2026, 12)), 3)
        self.button('Cancel').click().run()
        self.assertEqual(len(db.get_month_entries(2026, 12)), 3)
        self.button('Preview copy day').click().run()
        self.button('Approve changes').click().run()
        self.assert_clean()
        self.assertEqual(len(db.get_month_entries(2026, 12)), 6)
        self.assertIn('2 added, 0 skipped, 0 failed', self.app.success[0].value)
        self.assertFalse(any(b.label == 'Approve changes' for b in self.app.button))
        self.app.run()
        self.assertEqual(len(db.get_month_entries(2026, 12)), 6)

    def test_copy_target_and_assignment_selection_invalidate_pending_approval(self):
        db.add_entry(1, 1, '2026-12-01', FULL_DAY)
        db.add_entry(2, 1, '2026-12-01', 'AM')
        self.app.session_state['cal_year'] = 2026
        self.app.session_state['cal_month'] = 12
        self.app.run()
        targets = next(m for m in self.app.multiselect if m.key.startswith('copyentry_targets'))
        targets.select(date(2026, 12, 2)).run()
        self.button('Preview copy assignment').click().run()
        targets = next(m for m in self.app.multiselect if m.key.startswith('copyentry_targets'))
        targets.select(date(2026, 12, 3)).run()
        self.assertFalse(any(b.label == 'Approve changes' for b in self.app.button))
        self.button('Preview copy assignment').click().run()
        self.app.selectbox[0].select(self.app.selectbox[0].options[1]).run()
        self.assert_clean()
        self.assertFalse(any(b.label == 'Approve changes' for b in self.app.button))
        self.assertEqual(len(db.get_month_entries(2026, 12)), 3)


if __name__ == '__main__':
    unittest.main()
