"""Voice regressions use generated WAVs and mocked recognition, never a mic."""

from io import BytesIO
import unittest
from unittest.mock import patch
import wave

import speech_recognition as sr

from voice import transcribe_wav


def wav_bytes(channels=1, frames=100):
    buffer = BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\x01\x00" * channels * frames)
    return buffer.getvalue()


class VoiceCase(unittest.TestCase):
    def test_wav_header_is_removed_and_stereo_is_mono(self):
        for channels in (1, 2):
            with self.subTest(channels=channels):
                with patch.object(sr.Recognizer, 'recognize_google', return_value=' Khan CGH tomorrow ') as recognize:
                    result = transcribe_wav(wav_bytes(channels))
                self.assertEqual(result.text, 'Khan CGH tomorrow')
                self.assertEqual(result.error, '')
                audio = recognize.call_args.args[0]
                self.assertEqual(audio.frame_data, bytes([channels, 0]) * 100)
                self.assertEqual(audio.sample_rate, 16000)
                self.assertEqual(recognize.call_args.kwargs['language'], 'en-US')
                self.assertTrue(recognize.call_args.kwargs['endpoint'].startswith('https://'))

    def test_failure_reasons_are_distinct(self):
        for error, expected in [(sr.UnknownValueError(), 'Could not understand'),
                                (sr.RequestError('connection refused'), 'Speech service unavailable'),
                                (TimeoutError(), 'timed out')]:
            with self.subTest(error=error):
                with patch.object(sr.Recognizer, 'recognize_google', side_effect=error):
                    result = transcribe_wav(wav_bytes())
                self.assertEqual(result.text, '')
                self.assertIn(expected, result.error)

    def test_invalid_empty_and_blank_audio_are_not_success(self):
        with patch.object(sr.Recognizer, 'recognize_google') as recognize:
            self.assertIn('readable WAV', transcribe_wav(b'bad').error)
            self.assertIn('empty', transcribe_wav(wav_bytes(frames=0)).error)
            recognize.assert_not_called()
        with patch.object(sr.Recognizer, 'recognize_google', return_value=' '):
            self.assertIn('No speech', transcribe_wav(wav_bytes()).error)

    def test_connection_timeout_is_bounded(self):
        with patch.object(sr.Recognizer, 'recognize_google', autospec=True, return_value='Khan') as recognize:
            transcribe_wav(wav_bytes())
        self.assertEqual(recognize.call_args.args[0].operation_timeout, 15)


if __name__ == '__main__':
    unittest.main()
