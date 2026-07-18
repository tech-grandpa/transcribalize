"""Batch ASR provider selection and keyword parsing."""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Generator, Sequence

from .transcriber import transcribe as transcribe_whisper
from .transcriber import transcribe_stream as transcribe_whisper_stream


@dataclass(frozen=True)
class ASRProvider:
    id: str
    name: str
    description: str
    experimental: bool = False
    supports_keywords: bool = False
    supports_streaming: bool = True


ASR_PROVIDERS: dict[str, ASRProvider] = {
    "whisper": ASRProvider(
        id="whisper",
        name="Whisper large-v3 turbo",
        description="Default faster-whisper backend. Keyword hints are passed as hotwords.",
        supports_keywords=True,
    ),
    "granite-2b": ASRProvider(
        id="granite-2b",
        name="IBM Granite Speech 4.1 2B",
        description="Experimental file transcription backend with prompt-based keyword biasing.",
        experimental=True,
        supports_keywords=True,
        supports_streaming=False,
    ),
    "granite-2b-plus": ASRProvider(
        id="granite-2b-plus",
        name="IBM Granite Speech 4.1 2B Plus",
        description="Experimental Granite plus backend. Useful for rich ASR, but currently exposed as plain file transcription.",
        experimental=True,
        supports_keywords=True,
        supports_streaming=False,
    ),
    "parakeet-tdt-0.6b-v3": ASRProvider(
        id="parakeet-tdt-0.6b-v3",
        name="NVIDIA Parakeet TDT 0.6B v3",
        description="Multilingual NVIDIA Parakeet backend via Transformers.",
        experimental=False,
        supports_keywords=False,
        supports_streaming=False,
    ),
}

GRANITE_PROVIDER_IDS = {"granite-2b", "granite-2b-plus"}
PARAKEET_PROVIDER_IDS = {"parakeet-tdt-0.6b-v3"}
_configured_default = os.environ.get("ASR_BACKEND", "whisper")
DEFAULT_ASR_PROVIDER = _configured_default if _configured_default in ASR_PROVIDERS else "whisper"


def list_asr_providers() -> list[dict[str, Any]]:
    """Return ASR providers in UI order."""
    return [
        asdict(ASR_PROVIDERS[key])
        for key in ("whisper", "parakeet-tdt-0.6b-v3", "granite-2b", "granite-2b-plus")
    ]


def validate_asr_provider(provider: str | None) -> str:
    provider_id = (provider or DEFAULT_ASR_PROVIDER).strip() or DEFAULT_ASR_PROVIDER
    if provider_id not in ASR_PROVIDERS:
        valid = ", ".join(ASR_PROVIDERS)
        raise ValueError(f"Invalid ASR backend: {provider_id}. Valid: {valid}")
    return provider_id


def parse_keyword_bias(raw_keywords: str | Sequence[str] | None) -> list[str]:
    """Parse comma/newline separated keyword hints, preserving order and case."""
    if raw_keywords is None:
        return []
    if isinstance(raw_keywords, str):
        candidates = re.split(r"[,\n\r]+", raw_keywords)
    else:
        candidates = []
        for item in raw_keywords:
            candidates.extend(re.split(r"[,\n\r]+", item))

    keywords: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        keyword = " ".join(candidate.strip().split())
        key = keyword.casefold()
        if keyword and key not in seen:
            keywords.append(keyword)
            seen.add(key)
    return keywords


def transcribe_file(
    audio_path: Path,
    *,
    provider: str | None = None,
    language: str | None = None,
    keyword_bias: Sequence[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Transcribe a prepared WAV file through the selected batch ASR provider."""
    provider_id = validate_asr_provider(provider)
    if provider_id == "whisper":
        return transcribe_whisper(audio_path, language=language, keyword_bias=keyword_bias)
    if provider_id in PARAKEET_PROVIDER_IDS:
        from .parakeet_transcriber import transcribe as transcribe_parakeet

        return transcribe_parakeet(
            audio_path,
            language=language,
            keyword_bias=keyword_bias,
        )

    from .granite_transcriber import transcribe as transcribe_granite

    return transcribe_granite(
        audio_path,
        provider=provider_id,
        language=language,
        keyword_bias=keyword_bias,
    )


def transcribe_file_stream(
    audio_path: Path,
    *,
    provider: str | None = None,
    language: str | None = None,
    keyword_bias: Sequence[str] | None = None,
) -> Generator[tuple[dict[str, Any] | None, dict[str, Any] | None], None, None]:
    """
    Transcribe a prepared WAV file and yield segment events.

    Granite currently generates a complete response in one call, so this yields
    provider metadata and then one final segment instead of true segment streaming.
    """
    provider_id = validate_asr_provider(provider)
    if provider_id == "whisper":
        yield from transcribe_whisper_stream(audio_path, language=language, keyword_bias=keyword_bias)
        return

    segments, info = transcribe_file(
        audio_path,
        provider=provider_id,
        language=language,
        keyword_bias=keyword_bias,
    )
    yield None, info
    for segment in segments:
        yield segment, None
