"""Transcribe the recorder's WAV container and report recognition failures."""

from dataclasses import dataclass
from io import BytesIO
import logging
import wave

import speech_recognition as sr

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VoiceResult:
    text: str = ""
    error: str = ""


def transcribe_wav(audio_bytes, language="en-US"):
    recognizer = sr.Recognizer()
    recognizer.operation_timeout = 15
    try:
        # mic_recorder emits a complete WAV file, not raw PCM. AudioFile strips
        # the container header and converts stereo recordings to mono.
        with sr.AudioFile(BytesIO(audio_bytes)) as source:
            audio = recognizer.record(source)
        if not audio.frame_data:
            return VoiceResult(error="The recording is empty. Record again and check your microphone input.")
        text = recognizer.recognize_google(
            audio, language=language, endpoint="https://www.google.com/speech-api/v2/recognize",
        )
        if not text or not text.strip():
            return VoiceResult(error="No speech was recognized. Record again and speak clearly near the microphone.")
        return VoiceResult(text=text.strip())
    except sr.UnknownValueError:
        return VoiceResult(error="Could not understand the recording. Play it below to check the microphone, then record again.")
    except sr.RequestError as exc:
        return VoiceResult(error=f"Speech service unavailable. Check this computer's internet connection and retry. Details: {exc}")
    except TimeoutError:
        return VoiceResult(error="Speech recognition timed out. Check the internet connection and retry.")
    except (ValueError, EOFError, wave.Error):
        return VoiceResult(error="The recording is not a readable WAV file. Record again.")
    except OSError as exc:
        logger.exception("Audio conversion or speech connection failed")
        return VoiceResult(error=f"Audio conversion or connection failed. Retry or type the schedule. Details: {exc}")
    except Exception:
        logger.exception("Speech transcription failed")
        return VoiceResult(error="Speech transcription failed. Retry or type the schedule; details are in the server log.")
