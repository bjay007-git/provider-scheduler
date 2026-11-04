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
        self.button('Confirm & Save').click().run()
        self.assert_clean()
        self.assertIn('1 added, 0 skipped, 0 failed', self.app.success[0].value)
        self.assertEqual(self.app.text_area[0].value, '')
        self.assertEqual(len(db.get_month_entries(2026, 11)), 2)
        self.button('Next month').click().run()
        self.assertIn('1 added, 0 skipped, 0 failed', self.app.success[0].value)
        self.app.text_area[0].input('Khan at CGH 2026-11-04').run()
        self.button('Confirm & Save').click().run()
        self.assert_clean()
        self.assertIn('0 added, 1 skipped, 0 failed', self.app.warning[0].value)
        self.assertNotEqual(self.app.text_area[0].value, '')
        self.button('Clear').click().run()
        self.assertEqual(self.app.text_area[0].value, '')

    def test_invalid_session_fails_without_write_and_retains_feedback(self):
        self.app.run()
        self.app.text_area[0].input('Khan CGH 2026-11-04 mornings and afternoons').run()
        self.button('Confirm & Save').click().run()
        self.assert_clean()
        self.assertIn('0 added, 0 skipped, 1 failed', self.app.error[0].value)
        self.assertEqual(db.get_month_entries(2026, 11), [])
        self.app.run()
        self.assertIn('0 added, 0 skipped, 1 failed', self.app.error[0].value)

    def test_copy_displayed_full_day_as_one_assignment(self):
        db.add_entry(1, 1, '2026-12-01', FULL_DAY)
        self.app.session_state['cal_year'] = 2026
        self.app.session_state['cal_month'] = 12
        self.app.run()
        targets = next(m for m in self.app.multiselect if m.key.startswith('copyentry_targets'))
        targets.select(date(2026, 12, 2)).run()
        self.button('Copy assignment').click().run()
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


if __name__ == '__main__':
    unittest.main()
