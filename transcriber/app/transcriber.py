"""Whisper transcription wrapper using faster-whisper."""

import os
from pathlib import Path
from typing import Any, Generator, Sequence

from faster_whisper import WhisperModel

# Model configuration
MODEL_ID = "deepdml/faster-whisper-large-v3-turbo-ct2"
MODELS_DIR = os.environ.get("HF_HOME", "/models")

# Estimated Real-Time Factor (processing_time / audio_duration)
# RTF ~0.07 means 1 minute of audio takes ~4.2 seconds to process
ESTIMATED_RTF = 0.07

# Decode settings tuned for long-form audio.  Whisper's default
# condition_on_previous_text=True can let an erroneous segment poison later
# windows and snowball into repeated words/phrases.  A temperature fallback list
# lets faster-whisper retry low-confidence or high-compression windows instead of
# accepting the first greedy decode.
WHISPER_TEMPERATURE_FALLBACKS = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)

# Singleton model instance
_model: WhisperModel | None = None


def build_whisper_initial_prompt(keyword_bias: Sequence[str] | None = None) -> str | None:
    """
    Build a lightweight faster-whisper initial prompt from keyword hints.

    Kept for backwards-compatible callers/tests. The transcription path now uses
    faster-whisper's ``hotwords`` option for keyword hints so the hints do not
    become part of the rolling prompt context on long audio.
    """
    keywords = [term.strip() for term in keyword_bias or [] if term.strip()]
    if not keywords:
        return None
    return "Terms that may appear in this audio: " + ", ".join(keywords)


def build_whisper_hotwords(keyword_bias: Sequence[str] | None = None) -> str | None:
    """Build faster-whisper hotwords from keyword hints."""
    keywords = [term.strip() for term in keyword_bias or [] if term.strip()]
    return ", ".join(keywords) if keywords else None


def whisper_transcribe_options(
    language: str | None = None,
    keyword_bias: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Return safer faster-whisper decode options for long-form files."""
    return {
        "language": language if language and language != "auto" else None,
        "temperature": WHISPER_TEMPERATURE_FALLBACKS,
        "word_timestamps": False,
        "condition_on_previous_text": False,
        "compression_ratio_threshold": 2.4,
        "log_prob_threshold": -1.0,
        "no_speech_threshold": 0.6,
        "vad_filter": True,
        "hotwords": build_whisper_hotwords(keyword_bias),
    }


def get_model() -> WhisperModel:
    """Get or initialize the Whisper model (singleton)."""
    global _model
    if _model is None:
        print(f"Loading model: {MODEL_ID}")
        _model = WhisperModel(
            MODEL_ID,
            device="cuda",
            compute_type="float16",
            download_root=MODELS_DIR,
        )
        print(f"Model {MODEL_ID} loaded successfully")
    return _model


def transcribe(
    audio_path: Path,
    language: str | None = None,
    keyword_bias: Sequence[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Transcribe audio file using faster-whisper.
    
    Args:
        audio_path: Path to audio file (should be 16kHz mono WAV)
        language: Language code ('en', 'de') or None for auto-detect
        keyword_bias: Optional best-effort terms passed as faster-whisper hotwords
        
    Returns:
        Tuple of (segments_list, info_dict)
        - segments_list: List of dicts with 'start', 'end', 'text' keys
        - info_dict: Dict with 'language', 'language_probability', 'duration'
    """
    model = get_model()
    
    # Transcribe
    segments_iter, info = model.transcribe(
        str(audio_path),
        **whisper_transcribe_options(language, keyword_bias),
    )
    
    # Collect segments
    segments = [
        {
            "start": seg.start,
            "end": seg.end,
            "text": seg.text,
        }
        for seg in segments_iter
    ]
    
    # Extract info
    info_dict = {
        "language": info.language,
        "language_probability": info.language_probability,
        "duration": info.duration,
    }
    
    return segments, info_dict


def transcribe_stream(
    audio_path: Path,
    language: str | None = None,
    keyword_bias: Sequence[str] | None = None,
) -> Generator[tuple[dict[str, Any] | None, dict[str, Any] | None], None, None]:
    """
    Transcribe audio file, yielding segments as they're generated.
    
    Args:
        audio_path: Path to audio file (should be 16kHz mono WAV)
        language: Language code ('en', 'de') or None for auto-detect
        keyword_bias: Optional best-effort terms passed as faster-whisper hotwords
        
    Yields:
        Tuples of (segment_dict, info_dict)
        - First yield: (None, info_dict) with duration and language info
        - Subsequent yields: (segment_dict, None) for each segment
    """
    model = get_model()
    
    # Transcribe
    segments_iter, info = model.transcribe(
        str(audio_path),
        **whisper_transcribe_options(language, keyword_bias),
    )
    
    # Yield info first
    info_dict = {
        "language": info.language,
        "language_probability": info.language_probability,
        "duration": info.duration,
    }
    yield None, info_dict
    
    # Yield segments as they're generated
    for seg in segments_iter:
        segment_dict = {
            "start": seg.start,
            "end": seg.end,
            "text": seg.text,
        }
        yield segment_dict, None
