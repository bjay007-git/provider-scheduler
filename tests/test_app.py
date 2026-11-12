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
        self.app.text_area[0].input('Khan CGH 2026-11-04 mornings or afternoons').run()
        self.button('Interpret command').click().run()
        self.assert_clean()
        self.assertTrue(self.button('Approve changes').disabled)
        self.assertIn('Ambiguous', self.app.error[0].value)
        self.assertEqual(db.get_month_entries(2026, 11), [])
        self.app.run()
        self.assertTrue(self.button('Approve changes').disabled)
        self.assertIn('Ambiguous', self.app.error[0].value)

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
        proposed_dates = [row.assignment.entry_date for row in self.app.session_state['pending_changes'].rows]
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

    def test_calendar_remounts_after_approval_and_then_stays_stable(self):
        self.app.session_state['cal_year'] = 2026
        self.app.session_state['cal_month'] = 12
        self.interpret()
        original_key = self.calendar.call_args.kwargs['key']
        self.button('Edit').click().run()
        self.assertEqual(self.calendar.call_args.kwargs['key'], original_key)
        self.button('Interpret command').click().run()
        self.assertEqual(self.calendar.call_args.kwargs['key'], original_key)
        self.button('Approve changes').click().run()
        self.assert_clean()
        saved_key = self.calendar.call_args.kwargs['key']
        self.assertNotEqual(saved_key, original_key)
        self.assertEqual(self.calendar.call_args.kwargs['events'][0]['title'], 'SK-CGH')
        self.assertEqual(self.calendar.call_args.kwargs['options']['initialDate'], '2026-12-01')
        self.app.run()
        self.assertEqual(self.calendar.call_args.kwargs['key'], saved_key)
        self.assertEqual(len(db.get_month_entries(2026, 12)), 2)

    def test_calendar_remounts_after_copy_and_external_data_changes(self):
        db.add_entry(1, 1, '2026-12-01', FULL_DAY)
        self.app.session_state['cal_year'] = 2026
        self.app.session_state['cal_month'] = 12
        self.app.run()
        original_key = self.calendar.call_args.kwargs['key']
        targets = next(m for m in self.app.multiselect if m.key.startswith('copyentry_targets'))
        targets.select(date(2026, 12, 2)).run()
        self.button('Preview copy assignment').click().run()
        self.assertEqual(self.calendar.call_args.kwargs['key'], original_key)
        self.button('Approve changes').click().run()
        self.assert_clean()
        copied_key = self.calendar.call_args.kwargs['key']
        self.assertNotEqual(copied_key, original_key)
        self.assertEqual(len(self.calendar.call_args.kwargs['events']), 2)
        db.add_entry(2, 1, '2026-12-03', 'AM')
        self.app.run()
        self.assertNotEqual(self.calendar.call_args.kwargs['key'], copied_key)
        self.assertEqual(len(self.calendar.call_args.kwargs['events']), 3)

    def test_approval_outside_displayed_month_still_remounts_calendar(self):
        self.app.session_state['cal_year'] = 2026
        self.app.session_state['cal_month'] = 11
        self.interpret()
        original_key = self.calendar.call_args.kwargs['key']
        self.assertEqual(self.calendar.call_args.kwargs['events'], [])
        self.button('Approve changes').click().run()
        self.assert_clean()
        self.assertNotEqual(self.calendar.call_args.kwargs['key'], original_key)
        self.assertEqual(self.calendar.call_args.kwargs['events'], [])
        self.assertEqual(self.calendar.call_args.kwargs['options']['initialDate'], '2026-11-01')

    def record_voice(self, text='Khan CGH 2026-12-01', audio=b'test wav', recording_id=1):
        self.mic.return_value = {'bytes': audio, 'id': recording_id}
        with patch('voice.transcribe_wav', return_value=VoiceResult(text=text)):
            self.app.run()
        self.mic.return_value = None
        self.assert_clean()

    def test_approved_voice_add_clears_recording_and_recreates_microphone(self):
        self.record_voice()
        recording_key = self.mic.call_args.kwargs['key']
        self.assertTrue(any(b.label == 'Retry transcription' for b in self.app.button))
        self.button('Interpret command').click().run()
        self.button('Approve changes').click().run()
        self.assert_clean()
        self.assertNotEqual(self.mic.call_args.kwargs['key'], recording_key)
        self.assertEqual(self.app.text_area[0].value, '')
        self.assertFalse(any(b.label == 'Retry transcription' for b in self.app.button))
        self.assertEqual(len(self.app.get('audio')), 0)
        with self.assertRaises(KeyError):
            self.app.session_state['voice_audio']
        with self.assertRaises(KeyError):
            self.app.session_state['voice_result']
        self.assertEqual(len(db.get_month_entries(2026, 12)), 2)
        fresh_key = self.mic.call_args.kwargs['key']
        self.app.run()
        self.assertEqual(self.mic.call_args.kwargs['key'], fresh_key)
        self.record_voice('Khan RVC 2026-12-02 morning', b'second wav', 2)
        self.assertEqual(self.app.text_area[0].value, 'Khan RVC 2026-12-02 morning')

    def test_rejected_approval_keeps_recording_and_draft_for_correction(self):
        self.record_voice()
        original_key = self.mic.call_args.kwargs['key']
        self.button('Interpret command').click().run()
        db.add_entry(1, 2, '2026-12-01', 'PM')
        self.button('Approve changes').click().run()
        self.assert_clean()
        self.assertNotEqual(self.mic.call_args.kwargs['key'], original_key)
        self.assertEqual(self.app.text_area[0].value, 'Khan CGH 2026-12-01')
        self.assertEqual(self.app.session_state['voice_audio'], b'test wav')
        self.assertTrue(any(b.label == 'Retry transcription' for b in self.app.button))

    def test_clear_removes_recording_but_cancel_preserves_it(self):
        self.record_voice()
        original_key = self.mic.call_args.kwargs['key']
        self.button('Interpret command').click().run()
        self.button('Cancel').click().run()
        self.assertEqual(self.mic.call_args.kwargs['key'], original_key)
        self.assertEqual(self.app.session_state['voice_audio'], b'test wav')
        self.button('Clear').click().run()
        self.assert_clean()
        self.assertNotEqual(self.mic.call_args.kwargs['key'], original_key)
        self.assertEqual(self.app.text_area[0].value, '')
        self.assertFalse(any(b.label == 'Retry transcription' for b in self.app.button))

    def test_copy_approval_refreshes_microphone_without_erasing_unsaved_draft(self):
        db.add_entry(1, 1, '2026-12-01', FULL_DAY)
        self.app.session_state['cal_year'] = 2026
        self.app.session_state['cal_month'] = 12
        self.record_voice('Khan CGH 2026-12-09')
        original_key = self.mic.call_args.kwargs['key']
        targets = next(m for m in self.app.multiselect if m.key.startswith('copyentry_targets'))
        targets.select(date(2026, 12, 2)).run()
        self.button('Preview copy assignment').click().run()
        self.button('Approve changes').click().run()
        self.assert_clean()
        self.assertNotEqual(self.mic.call_args.kwargs['key'], original_key)
        self.assertEqual(self.app.text_area[0].value, 'Khan CGH 2026-12-09')
        self.assertEqual(self.app.session_state['voice_audio'], b'test wav')

    def test_paired_sessions_preview_and_approve_as_one_full_day(self):
        self.interpret('Khan CGH 2026-12-04 morning and afternoon')
        self.assertFalse(self.button('Approve changes').disabled)
        self.assertEqual(len(self.app.dataframe[0].value), 1)
        self.assertEqual(self.app.dataframe[0].value.iloc[0]['Session'], 'Full day')
        self.assertEqual(db.get_month_entries(2026, 12), [])
        self.button('Approve changes').click().run()
        self.assert_clean()
        self.assertIn('1 added, 0 skipped, 0 failed', self.app.success[0].value)
        self.assertEqual({row['half_day'] for row in db.get_month_entries(2026, 12)}, {'AM', 'PM'})

    def test_multi_provider_date_list_uses_existing_preview_and_approval(self):
        self.app.session_state['cal_year'] = 2027
        self.app.session_state['cal_month'] = 1
        self.interpret('Dr. Khan and Dr. Rao, City General, 2027-01-04 and 2027-01-05')
        table = self.app.dataframe[0].value
        self.assertEqual(len(table), 4)
        self.assertEqual(set(table['Provider']), {'Dr. Sarah Khan', 'Dr. Michael Rao'})
        self.assertEqual(set(table['Session']), {'Full day'})
        self.assertEqual(db.get_month_entries(2027, 1), [])
        self.button('Approve changes').click().run()
        self.assert_clean()
        self.assertIn('4 added, 0 skipped, 0 failed', self.app.success[0].value)
        self.assertEqual(len(db.get_month_entries(2027, 1)), 8)
        self.assertEqual(len(self.calendar.call_args.kwargs['events']), 4)

    def test_partial_provider_command_has_no_approvable_partial_preview(self):
        self.interpret('Khan and Unknown at CGH 2027-01-04')
        self.assertTrue(self.button('Approve changes').disabled)
        self.assertIn('No partial provider list', self.app.error[0].value)
        self.assertEqual(len(self.app.dataframe), 0)
        self.assertEqual(db.get_month_entries(2027, 1), [])

    def test_mixed_date_sessions_render_written_dates_and_approve_exact_batch(self):
        self.app.session_state['cal_year'] = 2027
        self.app.session_state['cal_month'] = 1
        self.interpret('Khan CGH 01/06/2027 morning and 2027-01-07 afternoon')
        table = self.app.dataframe[0].value
        self.assertEqual(list(table['Date']), ['January 06, 2027', 'January 07, 2027'])
        self.assertEqual(list(table['Session']), ['AM', 'PM'])
        self.assertEqual(db.get_month_entries(2027, 1), [])
        self.button('Cancel').click().run()
        self.assertEqual(db.get_month_entries(2027, 1), [])
        self.button('Interpret command').click().run()
        self.button('Approve changes').click().run()
        self.assert_clean()
        self.assertIn('2 added, 0 skipped, 0 failed', self.app.success[0].value)
        self.assertEqual([(r['entry_date'], r['half_day']) for r in db.get_month_entries(2027, 1)],
                         [('2027-01-06', 'AM'), ('2027-01-07', 'PM')])

    def test_one_mixed_clause_conflict_disables_whole_batch_approval(self):
        db.add_entry(1, 2, '2027-01-07', 'PM')
        self.interpret('Khan CGH 01/06/2027 morning and 2027-01-07 afternoon')
        table = self.app.dataframe[0].value
        self.assertTrue(table.iloc[0]['Validation status'].startswith('Valid:'))
        self.assertTrue(table.iloc[1]['Validation status'].startswith('Conflict:'))
        self.assertTrue(self.button('Approve changes').disabled)
        self.assertEqual(len(db.get_month_entries(2027, 1)), 1)

    def test_multi_provider_invalid_mapping_blocks_all_dates(self):
        self.interpret('Khan and Rao at RVC 2027-01-04 through 2027-01-05 mornings')
        self.assertTrue(self.button('Approve changes').disabled)
        table = self.app.dataframe[0].value
        self.assertEqual(len(table), 4)
        self.assertEqual(sum(value.startswith('Invalid:') for value in table['Validation status']), 2)
        self.button('Cancel').click().run()
        self.assertEqual(db.get_month_entries(2027, 1), [])


if __name__ == '__main__':
    unittest.main()
