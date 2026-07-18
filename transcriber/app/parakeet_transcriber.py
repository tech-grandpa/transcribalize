"""Experimental NVIDIA Parakeet TDT batch transcription backend.

The heavy imports stay inside this module so the default Whisper service remains
importable when optional Transformers/torch audio dependencies are not installed.
"""

from __future__ import annotations

import gc
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

MODEL_ID = "nvidia/parakeet-tdt-0.6b-v3"
DEFAULT_CHUNK_SECONDS = 60.0
DEFAULT_CHUNK_OVERLAP_SECONDS = 0.0


@dataclass
class _ParakeetBundle:
    model_id: str
    torch: Any
    torchaudio: Any
    processor: Any
    model: Any


_bundle: _ParakeetBundle | None = None


def _dependency_error(exc: ImportError) -> RuntimeError:
    return RuntimeError(
        "NVIDIA Parakeet dependencies are not installed. Install the optional ASR extras with "
        "`pip install -r requirements-granite.txt` inside the transcriber service, or rebuild "
        "the Docker image with `--build-arg INSTALL_GRANITE=true`. Original import error: "
        f"{exc}"
    )


def _load_bundle() -> _ParakeetBundle:
    global _bundle
    if _bundle is not None:
        return _bundle

    try:
        import torch  # type: ignore[import-not-found]
        import torchaudio  # type: ignore[import-not-found]
        from transformers import AutoModelForTDT, AutoProcessor  # type: ignore[import-not-found]
    except ImportError as exc:
        raise _dependency_error(exc) from exc

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype_name = os.environ.get("PARAKEET_TORCH_DTYPE", "bfloat16")
    dtype = getattr(torch, dtype_name, torch.bfloat16)
    if device == "cpu":
        dtype = torch.float32

    try:
        processor = AutoProcessor.from_pretrained(MODEL_ID)
        try:
            model = AutoModelForTDT.from_pretrained(
                MODEL_ID,
                dtype=dtype,
                device_map=device,
            )
        except TypeError:
            # Older Transformers releases used torch_dtype before dtype.
            model = AutoModelForTDT.from_pretrained(
                MODEL_ID,
                torch_dtype=dtype,
                device_map=device,
            )
    except Exception as exc:
        raise RuntimeError(
            f"Failed to load {MODEL_ID}. Parakeet v3 needs the optional ASR dependencies "
            "from `requirements-granite.txt` (including recent Transformers and librosa); "
            "rebuild the Docker image with `INSTALL_GRANITE=true`. "
            f"Original error: {exc}"
        ) from exc

    model.eval()
    _bundle = _ParakeetBundle(
        model_id=MODEL_ID,
        torch=torch,
        torchaudio=torchaudio,
        processor=processor,
        model=model,
    )
    return _bundle


def _load_audio(bundle: _ParakeetBundle, audio_path: Path) -> tuple[Any, float | None]:
    wav, sample_rate = bundle.torchaudio.load(str(audio_path), normalize=True)
    if wav.shape[0] > 1:
        wav = wav.mean(dim=0, keepdim=True)

    target_sample_rate = getattr(bundle.processor.feature_extractor, "sampling_rate", 16000)
    if sample_rate != target_sample_rate:
        wav = bundle.torchaudio.functional.resample(wav, sample_rate, target_sample_rate)

    # Transformers' Parakeet processor expects one 1D audio sample.
    audio = wav.squeeze(0)
    duration = audio.shape[-1] / target_sample_rate if getattr(audio, "shape", None) is not None else None
    if hasattr(audio, "detach"):
        audio = audio.detach().cpu().numpy()
    return audio, duration


def _parakeet_chunk_seconds() -> float:
    return max(1.0, float(os.environ.get("PARAKEET_CHUNK_SECONDS", str(DEFAULT_CHUNK_SECONDS))))


def _parakeet_chunk_overlap_seconds() -> float:
    return max(0.0, float(os.environ.get("PARAKEET_CHUNK_OVERLAP_SECONDS", str(DEFAULT_CHUNK_OVERLAP_SECONDS))))


def _iter_audio_chunks(audio: Any, sample_rate: int, *, chunk_seconds: float, overlap_seconds: float):
    """Yield (start_seconds, end_seconds, chunk_audio) windows for bounded-memory inference."""
    total_samples = len(audio)
    chunk_samples = max(1, int(chunk_seconds * sample_rate))
    overlap_samples = max(0, int(overlap_seconds * sample_rate))
    if overlap_samples >= chunk_samples:
        overlap_samples = 0
    step_samples = chunk_samples - overlap_samples

    start = 0
    while start < total_samples:
        end = min(total_samples, start + chunk_samples)
        yield start / sample_rate, end / sample_rate, audio[start:end]
        if end >= total_samples:
            break
        start += step_samples


def _cleanup_cuda(bundle: _ParakeetBundle) -> None:
    """Release transient tensors between chunks without unloading the selected model."""
    gc.collect()
    cuda = getattr(bundle.torch, "cuda", None)
    if cuda is not None and cuda.is_available():
        cuda.empty_cache()


def _decode_output(bundle: _ParakeetBundle, output: Any) -> str:
    sequences = output.sequences if hasattr(output, "sequences") else output
    decoded = bundle.processor.decode(sequences, skip_special_tokens=True)
    if isinstance(decoded, tuple):
        decoded = decoded[0]
    if isinstance(decoded, list):
        return " ".join(str(item).strip() for item in decoded if str(item).strip()).strip()
    return str(decoded).strip()


def transcribe(
    audio_path: Path,
    *,
    language: str | None = None,
    keyword_bias: Sequence[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Transcribe a prepared WAV file with NVIDIA Parakeet TDT 0.6B v3.

    `language` and `keyword_bias` are accepted for API compatibility. Parakeet v3
    is multilingual but does not expose Whisper-style language forcing or hotword
    biasing through this Transformers API path.
    """
    bundle = _load_bundle()
    audio, duration = _load_audio(bundle, audio_path)
    sample_rate = getattr(bundle.processor.feature_extractor, "sampling_rate", 16000)
    chunk_seconds = _parakeet_chunk_seconds()
    overlap_seconds = _parakeet_chunk_overlap_seconds()

    segments: list[dict[str, Any]] = []
    for start, end, chunk in _iter_audio_chunks(
        audio,
        sample_rate,
        chunk_seconds=chunk_seconds,
        overlap_seconds=overlap_seconds,
    ):
        inputs = None
        output = None
        try:
            inputs = bundle.processor(chunk, sampling_rate=sample_rate)
            inputs.to(device=bundle.model.device, dtype=bundle.model.dtype)
        except Exception as exc:
            _cleanup_cuda(bundle)
            raise RuntimeError(f"Parakeet input preparation failed. Original error: {exc}") from exc

        try:
            with bundle.torch.inference_mode():
                output = bundle.model.generate(**inputs, return_dict_in_generate=True)
        except Exception as exc:
            _cleanup_cuda(bundle)
            raise RuntimeError(f"Parakeet transcription failed. Original error: {exc}") from exc

        text = _decode_output(bundle, output)
        if text:
            segments.append({"start": start, "end": end, "text": text})
        del inputs, output
        _cleanup_cuda(bundle)

    info = {
        "language": language if language and language != "auto" else None,
        "language_probability": None,
        "duration": duration,
        "provider": "parakeet-tdt-0.6b-v3",
        "model": bundle.model_id,
        "keyword_bias": list(keyword_bias or []),
        "chunk_seconds": chunk_seconds,
        "chunk_overlap_seconds": overlap_seconds,
    }
    return segments, info
