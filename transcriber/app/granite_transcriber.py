"""Experimental IBM Granite Speech batch transcription backend.

The imports for transformers, torch, and torchaudio stay inside this module so
the default Whisper service remains importable when Granite dependencies are not
installed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

MODEL_IDS = {
    "granite-2b": "ibm-granite/granite-speech-4.1-2b",
    "granite-2b-plus": "ibm-granite/granite-speech-4.1-2b-plus",
}

SYSTEM_PROMPT = (
    "Knowledge Cutoff Date: April 2024.\n"
    "Today's Date: December 19, 2024.\n"
    "You are Granite, developed by IBM. You are a helpful AI assistant"
)


@dataclass
class _GraniteBundle:
    provider: str
    model_id: str
    device: str
    torch: Any
    torchaudio: Any
    processor: Any
    tokenizer: Any
    model: Any


_bundles: dict[str, _GraniteBundle] = {}


def build_granite_prompt(keyword_bias: Sequence[str] | None = None, *, provider: str = "granite-2b") -> str:
    """Build the single Granite ASR instruction, including keyword bias terms."""
    keywords = [term.strip() for term in keyword_bias or [] if term.strip()]
    if provider == "granite-2b-plus":
        prompt = "<|audio|> can you transcribe the speech into a written format?"
    else:
        prompt = "<|audio|>transcribe the speech with proper punctuation and capitalization."
    if keywords:
        prompt = f"{prompt} Keywords: {', '.join(keywords)}"
    return prompt


def _dependency_error(exc: ImportError) -> RuntimeError:
    return RuntimeError(
        "Granite ASR dependencies are not installed. Install the optional Granite extras with "
        "`pip install -r requirements-granite.txt` inside the transcriber service, or rebuild "
        "the Docker image with `--build-arg INSTALL_GRANITE=true`. Original import error: "
        f"{exc}"
    )


def _load_bundle(provider: str) -> _GraniteBundle:
    if provider in _bundles:
        return _bundles[provider]

    model_id = MODEL_IDS.get(provider)
    if not model_id:
        raise ValueError(f"Unknown Granite provider: {provider}")

    try:
        import torch
        import torchaudio
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor
    except ImportError as exc:
        raise _dependency_error(exc) from exc

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype_name = os.environ.get("GRANITE_TORCH_DTYPE", "bfloat16")
    dtype = getattr(torch, dtype_name, torch.bfloat16)
    if device == "cpu":
        dtype = torch.float32

    try:
        processor = AutoProcessor.from_pretrained(model_id)
        tokenizer = processor.tokenizer
        model = AutoModelForSpeechSeq2Seq.from_pretrained(
            model_id,
            device_map=device,
            dtype=dtype,
        )
    except TypeError:
        # Older transformers used torch_dtype. Keep this fallback while Granite
        # support is still settling across released versions.
        model = AutoModelForSpeechSeq2Seq.from_pretrained(
            model_id,
            device_map=device,
            torch_dtype=dtype,
        )
        processor = AutoProcessor.from_pretrained(model_id)
        tokenizer = processor.tokenizer
    except Exception as exc:
        raise RuntimeError(
            f"Failed to load {model_id}. Granite Speech 4.1 support requires a recent "
            "Transformers build; use `requirements-granite.txt` and ensure Hugging Face "
            f"model access is available. Original error: {exc}"
        ) from exc

    model.eval()
    bundle = _GraniteBundle(
        provider=provider,
        model_id=model_id,
        device=device,
        torch=torch,
        torchaudio=torchaudio,
        processor=processor,
        tokenizer=tokenizer,
        model=model,
    )
    _bundles[provider] = bundle
    return bundle


def _load_wav(bundle: _GraniteBundle, audio_path: Path) -> Any:
    wav, sample_rate = bundle.torchaudio.load(str(audio_path), normalize=True)
    if wav.shape[0] > 1:
        wav = wav.mean(dim=0, keepdim=True)
    if sample_rate != 16000:
        wav = bundle.torchaudio.functional.resample(wav, sample_rate, 16000)
    return wav


def transcribe(
    audio_path: Path,
    *,
    provider: str,
    language: str | None = None,
    keyword_bias: Sequence[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Transcribe a prepared WAV file with IBM Granite Speech.

    `language` is accepted for API compatibility. The current Granite prompt
    uses model auto-recognition rather than forcing language.
    """
    bundle = _load_bundle(provider)
    wav = _load_wav(bundle, audio_path)
    prompt = build_granite_prompt(keyword_bias, provider=provider)
    chat = [{"role": "user", "content": prompt}]
    if provider == "granite-2b-plus":
        chat.insert(0, {"role": "system", "content": SYSTEM_PROMPT})

    try:
        prompt_text = bundle.tokenizer.apply_chat_template(chat, tokenize=False, add_generation_prompt=True)
        inputs = bundle.processor(prompt_text, wav, device=bundle.device, return_tensors="pt").to(bundle.device)
        with bundle.torch.inference_mode():
            outputs = bundle.model.generate(
                **inputs,
                max_new_tokens=int(os.environ.get("GRANITE_MAX_NEW_TOKENS", "2000")),
                do_sample=False,
                num_beams=1,
            )
    except Exception as exc:
        raise RuntimeError(
            "Granite transcription failed. This backend is experimental and depends on "
            "the current Granite Speech generation API in Transformers. "
            f"Original error: {exc}"
        ) from exc

    num_input_tokens = inputs["input_ids"].shape[-1]
    new_tokens = outputs[0, num_input_tokens:].unsqueeze(0)
    text = bundle.tokenizer.batch_decode(
        new_tokens,
        add_special_tokens=False,
        skip_special_tokens=True,
    )[0].strip()

    duration = wav.shape[-1] / 16000 if getattr(wav, "shape", None) is not None else None
    segments = [{"start": 0.0, "end": duration or 0.0, "text": text}] if text else []
    info = {
        "language": language if language and language != "auto" else None,
        "language_probability": None,
        "duration": duration,
        "provider": provider,
        "model": bundle.model_id,
        "keyword_bias": list(keyword_bias or []),
    }
    return segments, info
