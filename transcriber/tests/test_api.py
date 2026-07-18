from __future__ import annotations

import importlib
import math
import shutil
import struct
import sys
import types
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture()
def main_module(tmp_path, monkeypatch):
    monkeypatch.chdir(REPO_ROOT)

    for name in [
        "app.main",
        "app.transcriber",
        "app.asr_providers",
        "app.granite_transcriber",
        "app.llm",
        "app.live_transcription",
    ]:
        sys.modules.pop(name, None)

    fake_faster_whisper = types.ModuleType("faster_whisper")

    class DummyWhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            return iter([]), SimpleNamespace(language="en", language_probability=1.0, duration=0.0)

    fake_faster_whisper.WhisperModel = DummyWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_faster_whisper)

    fake_litellm = types.ModuleType("litellm")

    async def acompletion(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="stub response"))]
        )

    fake_litellm.acompletion = acompletion
    fake_litellm.set_verbose = False
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)

    fake_live = types.ModuleType("app.live_transcription")

    async def handle_transcription_session(websocket, language="auto"):
        return None

    fake_live.handle_transcription_session = handle_transcription_session
    monkeypatch.setitem(sys.modules, "app.live_transcription", fake_live)

    module = importlib.import_module("app.main")
    monkeypatch.setattr(module, "UPLOADS_DIR", tmp_path / "uploads")
    return module


@pytest.fixture()
def client(main_module):
    return TestClient(main_module.app)


def _make_sample_wav(path: Path, *, seconds: float = 0.25, sample_rate: int = 16000) -> Path:
    frame_count = int(seconds * sample_rate)
    amplitude = 12000
    frequency = 440.0

    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        frames = bytearray()
        for i in range(frame_count):
            value = int(amplitude * math.sin(2 * math.pi * frequency * (i / sample_rate)))
            frames.extend(struct.pack("<h", value))
        wav_file.writeframes(bytes(frames))

    return path


def test_health_models_and_upload_config(client):
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "Transcribalize API" in docs.text

    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    schema = openapi.json()
    assert schema["info"]["title"] == "Transcribalize API"
    assert "audio/video transcription" in schema["info"]["summary"]
    assert "chunked upload" in schema["info"]["description"]
    assert schema["paths"]["/analyze/stream"]["post"]["summary"] == (
        "Stream transcription and analysis workflow events"
    )
    assert schema["paths"]["/transcribe"]["post"]["tags"] == ["Transcription"]
    assert schema["paths"]["/upload/config"]["get"]["tags"] == ["Uploads", "Discovery"]

    models = client.get("/models")
    assert models.status_code == 200
    ids = {item["id"] for item in models.json()}
    assert "anthropic/claude-opus-4.8" in ids
    assert "google/gemini-3.5-flash" in ids
    assert "openai/gpt-5.5" in ids
    assert "anthropic/claude-opus-4.6" not in ids
    assert "openai/gpt-5.4" not in ids
    assert "openai/gpt-5.2" not in ids

    upload_config = client.get("/upload/config")
    assert upload_config.status_code == 200
    payload = upload_config.json()
    assert payload["cloudflare_cap_bytes"] == 100 * 1024 * 1024
    assert payload["max_direct_upload_bytes"] < payload["cloudflare_cap_bytes"]
    assert payload["chunk_upload_size_bytes"] == 8 * 1024 * 1024


def test_frontend_uses_local_assets_and_sanitizes_markdown():
    index_html = (REPO_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    settings_html = (REPO_ROOT / "static" / "settings.html").read_text(encoding="utf-8")

    assert "<title>Transcribalize</title>" in index_html
    assert "<title>Settings - Transcribalize</title>" in settings_html

    for html in (index_html, settings_html):
        assert "fonts.googleapis.com" not in html
        assert "fonts.gstatic.com" not in html
        assert '<script src="https://' not in html

    assert '<script src="/static/vendor/marked.min.js"></script>' in index_html
    assert '<script src="/static/vendor/purify.min.js"></script>' in index_html
    assert "FORBID_TAGS" in index_html
    assert "FORBID_ATTR" in index_html
    assert "'background'" in index_html
    assert "DOMPurify.sanitize(marked.parse(text), MARKDOWN_SANITIZE_CONFIG)" in index_html
    assert (REPO_ROOT / "static" / "vendor" / "marked.min.js").is_file()
    assert (REPO_ROOT / "static" / "vendor" / "purify.min.js").is_file()


@pytest.mark.parametrize("route", ["/", "/live", "/settings"])
def test_ui_responses_restrict_passive_external_loading(client, route):
    response = client.get(route)

    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "img-src 'self' data: blob:" in csp
    assert "media-src 'self' blob:" in csp
    assert "connect-src 'self' ws://testserver wss://testserver" in csp
    assert "object-src 'none'" in csp
    assert "https:" not in csp
    assert "http:" not in csp
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize(
    "host",
    [
        "example.com",
        "example.com:8443",
        "127.0.0.1",
        "127.0.0.1:8000",
    ],
)
def test_ui_accepts_valid_host_authorities_for_websocket_csp(client, host):
    response = client.get("/", headers={"host": host})

    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    assert f"connect-src 'self' ws://{host} wss://{host}" in csp


@pytest.mark.parametrize(
    "host",
    [
        "testserver; img-src *",
        "[:::]",
        "[1]",
        "[12345::1]",
        "999.999.999.999",
        "example..com",
        "-example.com",
        ".",
        "example.com:",
        "example.com:0",
        "example.com:65536",
        "example.com:99999",
        "::1",
        "[::1",
        "[::1]:not-a-port",
        "[::1]",
        "[2001:db8::1]:8443",
        "[fe80::1%eth0]",
        "[fe80::1%;script-src-elem *;]",
    ],
)
def test_ui_rejects_malformed_host_before_building_csp(client, host):
    response = client.get("/", headers={"host": host})

    assert response.status_code == 400


@pytest.mark.parametrize(
    "authority",
    [
        "[fe80::1%scope with space]",
        "[fe80::1%scope\tname]",
        "[fe80::1%25eth0]",
    ],
)
def test_ui_host_validator_rejects_scoped_ipv6_variants(main_module, authority):
    with pytest.raises(ValueError):
        main_module._validate_ui_host(authority)


def test_transcribe_endpoint_end_to_end_with_real_wav(client, main_module, tmp_path, monkeypatch):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for the end-to-end transcription test")

    sample_path = _make_sample_wav(tmp_path / "sample.wav")

    def fake_transcribe_file(audio_path, provider="whisper", language="auto", keyword_bias=None):
        assert audio_path.exists()
        assert audio_path.suffix == ".wav"
        assert provider == "whisper"
        assert keyword_bias == []
        return (
            [
                {"start": 0.0, "end": 0.12, "text": "Hello "},
                {"start": 0.12, "end": 0.25, "text": "world"},
            ],
            {"language": "en", "language_probability": 0.99, "duration": 0.25},
        )

    monkeypatch.setattr(main_module, "transcribe_file", fake_transcribe_file)

    with sample_path.open("rb") as handle:
        response = client.post(
            "/transcribe",
            data={"language": "auto", "format": "text"},
            files={"file": (sample_path.name, handle.read(), "audio/wav")},
        )

    assert response.status_code == 200
    assert response.text == "Hello world"
    assert response.headers["content-type"].startswith("text/plain")


@pytest.mark.parametrize(
    "endpoint",
    ["/transcribe", "/transcribe/stream", "/analyze", "/analyze/stream"],
)
def test_direct_upload_limit_returns_clear_error(client, main_module, monkeypatch, endpoint):
    monkeypatch.setattr(main_module, "MAX_DIRECT_UPLOAD_BYTES", 1)

    response = client.post(
        endpoint,
        data={"language": "auto", "output_language": "source", "model": ""},
        files={"file": ("tiny.mp3", b"ab", "audio/mpeg")},
    )

    assert response.status_code == 413
    assert "Direct uploads are limited" in response.json()["detail"]


def test_chunked_upload_flow_streams_transcript_and_cleans_up(client, main_module, monkeypatch):
    async def fake_transcribe_path_streaming_local(
        input_path,
        filename,
        language="auto",
        asr_backend="whisper",
        keyword_bias=None,
    ):
        assert input_path.exists()
        assert asr_backend == "whisper"
        assert keyword_bias == []
        yield {"type": "stage", "stage": "extract", "message": "Extracting audio…"}
        yield {"type": "stage", "stage": "transcribe", "message": "Running transcription…"}
        yield {"type": "progress", "percent": 55, "text": "hello"}
        yield {"type": "done", "result": "hello world"}

    monkeypatch.setattr(
        main_module,
        "_transcribe_path_streaming_local",
        fake_transcribe_path_streaming_local,
    )

    init = client.post(
        "/upload/init",
        data={
            "filename": "sample.mp3",
            "size": len(b"helloworld"),
            "content_type": "audio/mpeg",
        },
    )
    assert init.status_code == 200
    upload_id = init.json()["upload_id"]

    uploaded = client.post(
        "/upload/chunk",
        data={"upload_id": upload_id, "index": "0"},
        files={"chunk": ("part-0.bin", b"helloworld", "application/octet-stream")},
    )
    assert uploaded.status_code == 200

    complete = client.post("/upload/complete", data={"upload_id": upload_id})
    assert complete.status_code == 200
    assert complete.json()["status"] == "ready"

    response = client.post(
        "/analyze/stream",
        data={"upload_id": upload_id, "language": "auto", "output_language": "source", "model": ""},
    )
    assert response.status_code == 200
    body = response.text
    assert '"type": "transcribe_stage"' in body
    assert '"type": "transcript"' in body
    assert "hello world" in body
    assert not (main_module.UPLOADS_DIR / upload_id).exists()


@pytest.mark.parametrize(
    "upload_id",
    ["../outside", "/tmp/outside", "A" * 32, "a" * 31, "g" * 32],
)
def test_chunk_upload_endpoints_reject_invalid_upload_ids(client, main_module, upload_id):
    response = client.post("/upload/complete", data={"upload_id": upload_id})

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid upload ID"
    assert not (main_module.UPLOADS_DIR.parent / "outside").exists()


def test_analyze_stream_auto_includes_preconditions(client, main_module, monkeypatch):
    calls: list[tuple[str, str]] = []

    async def fake_analyze_transcript(transcript, task_id, output_language=None, model=None, custom_prompt=None):
        calls.append((task_id, transcript))
        return f"{task_id}: {transcript}"

    monkeypatch.setattr(main_module, "analyze_transcript", fake_analyze_transcript)

    response = client.post(
        "/analyze/stream",
        data={
            "transcript_text": "Hallo Welt",
            "model": "anthropic/claude-opus-4.8",
            "tasks": ["summary"],
        },
    )

    assert response.status_code == 200
    body = response.text
    assert '"task": "improve"' in body
    assert '"task": "summary"' in body
    assert [task for task, _ in calls] == ["improve", "summary"]
    assert calls[1][1].startswith("improve: Hallo Welt")


def test_whisper_long_form_decode_options_reduce_repetition_risk(main_module):
    from app import transcriber

    class FakeWhisperModel:
        def __init__(self):
            self.calls = []

        def transcribe(self, audio, **kwargs):
            self.calls.append((audio, kwargs))
            return (
                iter([SimpleNamespace(start=0.0, end=1.0, text="Hallo Welt")]),
                SimpleNamespace(language="de", language_probability=0.99, duration=1.0),
            )

    fake_model = FakeWhisperModel()
    transcriber._model = fake_model

    segments, info = transcriber.transcribe(
        Path("/tmp/sample.wav"),
        language="de",
        keyword_bias=["Granite", "Watson"],
    )

    assert segments == [{"start": 0.0, "end": 1.0, "text": "Hallo Welt"}]
    assert info["language"] == "de"
    _, kwargs = fake_model.calls[-1]
    assert kwargs["language"] == "de"
    assert kwargs["temperature"] == transcriber.WHISPER_TEMPERATURE_FALLBACKS
    assert kwargs["condition_on_previous_text"] is False
    assert kwargs["compression_ratio_threshold"] == 2.4
    assert kwargs["log_prob_threshold"] == -1.0
    assert kwargs["no_speech_threshold"] == 0.6
    assert kwargs["vad_filter"] is True
    assert kwargs["hotwords"] == "Granite, Watson"
    assert "initial_prompt" not in kwargs

    list(transcriber.transcribe_stream(Path("/tmp/sample.wav"), language="auto"))
    _, stream_kwargs = fake_model.calls[-1]
    assert stream_kwargs["language"] is None
    assert stream_kwargs["condition_on_previous_text"] is False


def test_asr_provider_list_and_keyword_parsing(client):
    from app.asr_providers import parse_keyword_bias
    from app.transcriber import build_whisper_initial_prompt

    response = client.get("/asr/providers")
    assert response.status_code == 200
    providers = response.json()
    ids = [item["id"] for item in providers]
    assert ids == ["whisper", "parakeet-tdt-0.6b-v3", "granite-2b", "granite-2b-plus"]
    by_id = {item["id"]: item for item in providers}
    assert by_id["parakeet-tdt-0.6b-v3"]["experimental"] is False
    assert by_id["granite-2b"]["experimental"] is True

    assert parse_keyword_bias("Granite, Watson\nOpenAI\r\nGranite") == [
        "Granite",
        "Watson",
        "OpenAI",
    ]
    assert build_whisper_initial_prompt([]) is None
    assert build_whisper_initial_prompt(["Granite", "Watson"]) == (
        "Terms that may appear in this audio: Granite, Watson"
    )


def test_transcribe_passes_selected_asr_backend_and_keywords(client, main_module, tmp_path, monkeypatch):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for the end-to-end transcription test")

    sample_path = _make_sample_wav(tmp_path / "sample.wav")
    calls = []

    def fake_transcribe_file(audio_path, provider="whisper", language="auto", keyword_bias=None):
        calls.append((provider, language, keyword_bias))
        return (
            [{"start": 0.0, "end": 0.25, "text": "IBM Granite"}],
            {"language": language, "language_probability": None, "duration": 0.25},
        )

    monkeypatch.setattr(main_module, "transcribe_file", fake_transcribe_file)

    with sample_path.open("rb") as handle:
        response = client.post(
            "/transcribe",
            data={
                "language": "de",
                "format": "text",
                "asr_backend": "parakeet-tdt-0.6b-v3",
                "keyword_bias": "IBM, Granite\nWatson",
            },
            files={"file": (sample_path.name, handle.read(), "audio/wav")},
        )

    assert response.status_code == 200
    assert response.text == "IBM Granite"
    assert calls == [("parakeet-tdt-0.6b-v3", "de", ["IBM", "Granite", "Watson"])]


def test_asr_provider_routes_parakeet_backend(main_module, monkeypatch):
    from app.asr_providers import transcribe_file

    fake_parakeet = types.ModuleType("app.parakeet_transcriber")
    calls = []

    def fake_transcribe(audio_path, language=None, keyword_bias=None):
        calls.append((audio_path, language, keyword_bias))
        return (
            [{"start": 0.0, "end": 1.0, "text": "Parakeet transcript"}],
            {"provider": "parakeet-tdt-0.6b-v3"},
        )

    setattr(fake_parakeet, "transcribe", fake_transcribe)
    monkeypatch.setitem(sys.modules, "app.parakeet_transcriber", fake_parakeet)

    segments, info = transcribe_file(
        Path("/tmp/sample.wav"),
        provider="parakeet-tdt-0.6b-v3",
        language="de",
        keyword_bias=["ignored"],
    )

    assert segments[0]["text"] == "Parakeet transcript"
    assert info["provider"] == "parakeet-tdt-0.6b-v3"
    assert calls == [(Path("/tmp/sample.wav"), "de", ["ignored"])]


def test_parakeet_audio_chunking_limits_inference_window(main_module):
    from app.parakeet_transcriber import _iter_audio_chunks

    chunks = list(_iter_audio_chunks(list(range(25)), 10, chunk_seconds=1.0, overlap_seconds=0.2))

    assert [(start, end, len(chunk)) for start, end, chunk in chunks] == [
        (0.0, 1.0, 10),
        (0.8, 1.8, 10),
        (1.6, 2.5, 9),
    ]


def test_invalid_asr_backend_returns_400(client, tmp_path):
    sample_path = _make_sample_wav(tmp_path / "sample.wav")
    with sample_path.open("rb") as handle:
        response = client.post(
            "/transcribe",
            data={"asr_backend": "not-real"},
            files={"file": (sample_path.name, handle.read(), "audio/wav")},
        )

    assert response.status_code == 400
    assert "Invalid ASR backend" in response.json()["detail"]


def test_granite_prompt_builder_keeps_keyword_format():
    from app.granite_transcriber import build_granite_prompt

    assert build_granite_prompt(["IBM", "Watson"], provider="granite-2b") == (
        "<|audio|>transcribe the speech with proper punctuation and capitalization. "
        "Keywords: IBM, Watson"
    )
    assert build_granite_prompt(["OpenShift"], provider="granite-2b-plus") == (
        "<|audio|> can you transcribe the speech into a written format? Keywords: OpenShift"
    )
