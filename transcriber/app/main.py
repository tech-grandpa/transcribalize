"""FastAPI transcription and analysis service — unified."""

import ipaddress
import json
import logging
import math
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Annotated, Literal

import aiofiles
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile, WebSocket
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .audio import extract_audio, get_audio_duration, is_supported_format
from .asr_providers import (
    DEFAULT_ASR_PROVIDER,
    list_asr_providers,
    parse_keyword_bias,
    transcribe_file,
    transcribe_file_stream,
    validate_asr_provider,
)
from .formatters import format_json, format_markdown, format_srt, format_text
from .transcriber import ESTIMATED_RTF
from .live_transcription import handle_transcription_session
from .llm import analyze_transcript, analyze_multiple_tasks, get_allowed_models, is_valid_model
from .prompts import get_all_tasks, TASKS

logger = logging.getLogger(__name__)

API_DESCRIPTION = """
Transcribalize turns audio/video files or existing transcript text into useful written output.

## What it can do

- Transcribe uploaded audio and video files with selectable ASR backends.
- Stream transcription progress with Server-Sent Events for long-running jobs.
- Analyze transcripts with built-in LLM tasks such as improved transcript, summary, key points, concepts, and action items.
- Accept existing transcript text when transcription is not needed.
- Handle large files through chunked upload endpoints before analysis.
- Provide live browser transcription over WebSocket.

## Common workflow

1. Use `GET /asr/providers`, `GET /tasks`, and `GET /models` to discover available options.
2. For small/medium files, call `POST /transcribe`, `POST /transcribe/stream`, `POST /analyze`, or `POST /analyze/stream` directly with `multipart/form-data`.
3. For large files, call `/upload/init`, send chunks with `/upload/chunk`, call `/upload/complete`, then pass the `upload_id` to `POST /analyze/stream`.
4. For already-transcribed text, use `POST /analyze/stream` with `transcript_text` or `POST /analyze/transcript`.

## Security

This FastAPI app does not currently enforce API keys, login, or CORS policy. Put public deployments behind TLS, authentication, and reverse-proxy upload/time-out controls.
""".strip()

OPENAPI_TAGS = [
    {"name": "Health", "description": "Service health and runtime status checks."},
    {"name": "Discovery", "description": "Discover available ASR backends, analysis tasks, model IDs, and upload limits."},
    {"name": "Transcription", "description": "Upload audio/video files and receive transcripts as text, Markdown, SRT, or JSON."},
    {"name": "Analysis", "description": "Transcribe and/or analyze content with built-in or custom LLM tasks."},
    {"name": "Uploads", "description": "Chunked upload flow for large media files, especially behind proxy upload limits."},
    {"name": "Live", "description": "Live transcription WebSocket and browser-live metadata endpoints."},
    {"name": "UI", "description": "Browser pages served by the application."},
]

app = FastAPI(
    title="Transcribalize API",
    summary="Self-hosted audio/video transcription, live transcription, and LLM transcript analysis.",
    description=API_DESCRIPTION,
    version="0.1.0",
    contact={"name": "tech-grandpa", "url": "https://github.com/tech-grandpa/transcribalize"},
    license_info={"name": "Apache-2.0", "identifier": "Apache-2.0"},
    openapi_tags=OPENAPI_TAGS,
)

UI_CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "media-src 'self' blob:",
        "font-src 'self' data:",
        "worker-src 'self' blob:",
        "frame-src 'none'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    )
)
UI_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "microphone=(self)",
}
DNS_LABEL_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")

# Supported output formats
OutputFormat = Literal["json", "text", "srt", "markdown"]
VALID_FORMATS = {"json", "text", "srt", "markdown"}

# Supported languages
VALID_LANGUAGES = {"en", "de", "auto"}

# Upload constraints
CLOUDFLARE_UPLOAD_CAP_BYTES = 100 * 1024 * 1024
MAX_DIRECT_UPLOAD_BYTES = 95 * 1024 * 1024
CHUNK_UPLOAD_SIZE_BYTES = 8 * 1024 * 1024
UPLOADS_DIR = Path(tempfile.gettempdir()) / "transcriber-uploads"
UPLOAD_ID_PATTERN = re.compile(r"[0-9a-f]{32}\Z")


def get_valid_tasks() -> set:
    """Get built-in valid task IDs."""
    return set(TASKS.keys())


def _validate_ui_host(authority: str) -> str:
    """Validate an HTTP Host authority before embedding it in a CSP source."""
    if not authority:
        raise ValueError("empty authority")

    port = None
    if authority.startswith("["):
        raise ValueError("IPv6 UI authorities are not supported by the CSP policy")
    else:
        if authority.count(":") > 1:
            raise ValueError("IPv6 addresses must be bracketed")
        hostname, separator, port_text = authority.partition(":")
        if not hostname or (separator and not port_text):
            raise ValueError("missing hostname or port")
        port = port_text if separator else None
        if re.fullmatch(r"[0-9.]+", hostname):
            ipaddress.IPv4Address(hostname)
        else:
            dns_name = hostname[:-1] if hostname.endswith(".") else hostname
            if not dns_name or len(dns_name) > 253:
                raise ValueError("invalid DNS name length")
            if any(not DNS_LABEL_PATTERN.fullmatch(label) for label in dns_name.split(".")):
                raise ValueError("invalid DNS label")

    if port is not None:
        if not re.fullmatch(r"[0-9]{1,5}", port):
            raise ValueError("invalid port")
        if not 1 <= int(port) <= 65535:
            raise ValueError("port out of range")
    return authority


def _ui_file_response(path: str, request: Request) -> FileResponse:
    """Serve an application UI page with browser security boundaries."""
    try:
        host = _validate_ui_host(request.headers.get("host", ""))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Host header") from None
    csp = (
        f"{UI_CONTENT_SECURITY_POLICY}; "
        f"connect-src 'self' ws://{host} wss://{host}"
    )
    headers = {**UI_SECURITY_HEADERS, "Content-Security-Policy": csp}
    return FileResponse(path, headers=headers)


def _ensure_uploads_dir() -> Path:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    return UPLOADS_DIR


def _direct_upload_limit_error() -> HTTPException:
    return HTTPException(
        status_code=413,
        detail=(
            f"Direct uploads are limited to {MAX_DIRECT_UPLOAD_BYTES // (1024 * 1024)} MB. "
            "Please use chunked upload for larger files."
        ),
    )


async def _read_direct_upload(file: UploadFile) -> bytes:
    """Read at most one byte beyond the configured direct-upload limit."""
    content = await file.read(MAX_DIRECT_UPLOAD_BYTES + 1)
    if len(content) > MAX_DIRECT_UPLOAD_BYTES:
        raise _direct_upload_limit_error()
    return content


async def _save_direct_upload(file: UploadFile, destination: Path) -> None:
    """Stream an upload to disk while enforcing the configured direct limit."""
    total_bytes = 0
    async with aiofiles.open(destination, "wb") as output:
        while chunk := await file.read(1024 * 1024):
            total_bytes += len(chunk)
            if total_bytes > MAX_DIRECT_UPLOAD_BYTES:
                raise _direct_upload_limit_error()
            await output.write(chunk)


def _upload_dir(upload_id: str) -> Path:
    if not UPLOAD_ID_PATTERN.fullmatch(upload_id):
        raise HTTPException(status_code=400, detail="Invalid upload ID")
    return _ensure_uploads_dir() / upload_id


def _upload_meta_path(upload_id: str) -> Path:
    return _upload_dir(upload_id) / "meta.json"


def _upload_parts_dir(upload_id: str) -> Path:
    return _upload_dir(upload_id) / "parts"


def _upload_file_path(upload_id: str, filename: str) -> Path:
    safe_name = Path(filename).name or "upload.bin"
    return _upload_dir(upload_id) / f"assembled-{safe_name}"


def _write_upload_meta(upload_id: str, meta: dict) -> None:
    upload_dir = _upload_dir(upload_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    _upload_meta_path(upload_id).write_text(json.dumps(meta), encoding="utf-8")


def _read_upload_meta(upload_id: str) -> dict:
    meta_path = _upload_meta_path(upload_id)
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="Upload session not found")
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="Upload session metadata is corrupted") from exc


def _cleanup_upload(upload_id: str) -> None:
    shutil.rmtree(_upload_dir(upload_id), ignore_errors=True)


def _validate_supported_filename(filename: str) -> None:
    if not is_supported_format(filename):
        raise HTTPException(status_code=400, detail="Unsupported file format.")


# ---------------------------------------------------------------------------
# Helper: local transcription (replaces the old HTTP proxy to transcriber)
# ---------------------------------------------------------------------------

def _validate_asr_request(asr_backend: str | None) -> str:
    try:
        return validate_asr_provider(asr_backend)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _transcribe_file_local(
    file_content: bytes,
    filename: str,
    language: str = "auto",
    asr_backend: str = DEFAULT_ASR_PROVIDER,
    keyword_bias: list[str] | None = None,
) -> str:
    """Transcribe file content locally. Returns text format."""
    suffix = Path(filename).suffix.lower() or ".mp3"
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        input_path = tmpdir_path / f"input{suffix}"
        wav_path = tmpdir_path / "audio.wav"

        with open(input_path, "wb") as f:
            f.write(file_content)

        extract_audio(input_path, wav_path)
        segments, info = transcribe_file(
            wav_path,
            provider=asr_backend,
            language=language,
            keyword_bias=keyword_bias,
        )

    return format_text(segments)


async def _transcribe_path_streaming_local(
    input_path: Path,
    filename: str,
    language: str = "auto",
    asr_backend: str = DEFAULT_ASR_PROVIDER,
    keyword_bias: list[str] | None = None,
):
    """
    Transcribe a local input path with streaming progress events.

    Yields dicts:
        {"type": "stage", "stage": str, "message": str}
        {"type": "progress", "percent": int, "text": str}
        {"type": "done", "result": str}
        {"type": "error", "message": str}
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        wav_path = Path(tmpdir) / "audio.wav"

        yield {"type": "stage", "stage": "extract", "message": "Extracting audio…"}
        try:
            extract_audio(input_path, wav_path)
        except RuntimeError as e:
            yield {"type": "error", "message": str(e)}
            return

        duration = get_audio_duration(wav_path)
        all_segments = []
        provider_name = next(
            (provider["name"] for provider in list_asr_providers() if provider["id"] == asr_backend),
            asr_backend,
        )
        yield {"type": "stage", "stage": "transcribe", "message": f"Running transcription with {provider_name}…"}
        try:
            for segment, info in transcribe_file_stream(
                wav_path,
                provider=asr_backend,
                language=language,
                keyword_bias=keyword_bias,
            ):
                if info is not None:
                    if duration is None and info.get("duration"):
                        duration = info["duration"]
                elif segment is not None:
                    all_segments.append(segment)
                    if duration:
                        percent = min(int((segment["end"] / duration) * 100), 99)
                        yield {"type": "progress", "percent": percent, "text": segment["text"].strip()}
        except Exception as e:
            yield {"type": "error", "message": str(e)}
            return

        yield {"type": "done", "result": format_text(all_segments)}


async def _transcribe_file_streaming_local(
    file_content: bytes,
    filename: str,
    language: str = "auto",
    asr_backend: str = DEFAULT_ASR_PROVIDER,
    keyword_bias: list[str] | None = None,
):
    """Save uploaded file content to a temp file, then transcribe it with progress events."""
    suffix = Path(filename).suffix.lower() or ".mp3"
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = Path(tmpdir) / f"input{suffix}"
        with open(input_path, "wb") as f:
            f.write(file_content)

        async for event in _transcribe_path_streaming_local(
            input_path,
            filename,
            language,
            asr_backend,
            keyword_bias,
        ):
            yield event


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get(
    "/health",
    tags=["Health"],
    summary="Check service health",
    description="Returns a minimal status payload when the Transcribalize API process is running.",
)
def health():
    """Health check endpoint."""
    return {"status": "ok"}


@app.get(
    "/asr/providers",
    tags=["Discovery"],
    summary="List ASR backends",
    description="Returns the selectable file-transcription backends, including display names, descriptions, experimental flags, keyword-hint support, and streaming support.",
)
def asr_providers():
    """List file-transcription ASR backends."""
    return list_asr_providers()


# ---------------------------------------------------------------------------
# Transcription endpoints
# ---------------------------------------------------------------------------

@app.post(
    "/transcribe",
    tags=["Transcription"],
    summary="Transcribe an uploaded media file",
    description="Upload an audio or video file and receive the final transcript as Markdown, plain text, SRT, or JSON. Use `language`, `format`, `asr_backend`, and `keyword_bias` form fields to control processing.",
)
async def transcribe_audio(
    file: UploadFile = File(...),
    language: str = Form("auto"),
    format: str = Form("markdown"),
    asr_backend: str = Form(DEFAULT_ASR_PROVIDER),
    keyword_bias: str = Form(""),
):
    """Transcribe audio or video file."""
    if language not in VALID_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Invalid language: {language}. Valid: {', '.join(VALID_LANGUAGES)}")
    if format not in VALID_FORMATS:
        raise HTTPException(status_code=400, detail=f"Invalid format: {format}. Valid: {', '.join(VALID_FORMATS)}")
    asr_backend = _validate_asr_request(asr_backend)
    keywords = parse_keyword_bias(keyword_bias)

    filename = file.filename or "audio.mp3"
    if not is_supported_format(filename):
        raise HTTPException(status_code=400, detail="Unsupported file format.")

    suffix = Path(filename).suffix.lower()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        input_path = tmpdir_path / f"input{suffix}"
        wav_path = tmpdir_path / "audio.wav"

        await _save_direct_upload(file, input_path)

        try:
            extract_audio(input_path, wav_path)
        except RuntimeError as e:
            raise HTTPException(status_code=422, detail=str(e))

        try:
            segments, info = transcribe_file(
                wav_path,
                provider=asr_backend,
                language=language,
                keyword_bias=keywords,
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")

    if format == "text":
        return PlainTextResponse(format_text(segments))
    elif format == "srt":
        return PlainTextResponse(format_srt(segments), media_type="text/plain",
                                 headers={"Content-Disposition": "attachment; filename=transcription.srt"})
    elif format == "markdown":
        return PlainTextResponse(format_markdown(segments), media_type="text/markdown",
                                 headers={"Content-Disposition": "attachment; filename=transcription.md"})
    else:
        return JSONResponse(format_json(segments, info))


@app.post(
    "/transcribe/stream",
    tags=["Transcription"],
    summary="Stream transcription progress for an uploaded media file",
    description="Upload an audio or video file and receive Server-Sent Events with start, progress, done, or error events. The final done event contains the formatted transcript result.",
)
async def transcribe_stream_endpoint(
    file: UploadFile = File(...),
    language: str = Form("auto"),
    format: str = Form("markdown"),
    asr_backend: str = Form(DEFAULT_ASR_PROVIDER),
    keyword_bias: str = Form(""),
):
    """Transcribe with SSE streaming progress updates."""
    if language not in VALID_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Invalid language: {language}. Valid: {', '.join(VALID_LANGUAGES)}")
    if format not in VALID_FORMATS:
        raise HTTPException(status_code=400, detail=f"Invalid format: {format}. Valid: {', '.join(VALID_FORMATS)}")
    asr_backend = _validate_asr_request(asr_backend)
    keywords = parse_keyword_bias(keyword_bias)

    filename = file.filename or "audio.mp3"
    if not is_supported_format(filename):
        raise HTTPException(status_code=400, detail="Unsupported file format.")

    suffix = Path(filename).suffix.lower()
    file_content = await _read_direct_upload(file)

    def generate_sse():
        start_time = time.time()
        segments = []
        transcription_info = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            input_path = tmpdir_path / f"input{suffix}"
            wav_path = tmpdir_path / "audio.wav"

            with open(input_path, "wb") as f:
                f.write(file_content)

            duration = get_audio_duration(input_path)

            try:
                extract_audio(input_path, wav_path)
            except RuntimeError as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
                return

            if duration is None:
                duration = get_audio_duration(wav_path)

            estimated_seconds = duration * ESTIMATED_RTF if duration else None
            yield f"data: {json.dumps({'type': 'start', 'duration': duration, 'estimated_seconds': estimated_seconds})}\n\n"

            try:
                for segment, info in transcribe_file_stream(
                    wav_path,
                    provider=asr_backend,
                    language=language,
                    keyword_bias=keywords,
                ):
                    if info is not None:
                        transcription_info = info
                        if duration is None and info.get("duration"):
                            duration = info["duration"]
                    elif segment is not None:
                        segments.append(segment)
                        if duration:
                            percent = min(int((segment["end"] / duration) * 100), 99)
                            yield f"data: {json.dumps({'type': 'progress', 'percent': percent, 'text': segment['text'].strip()})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
                return

            if format == "text":
                result = format_text(segments)
            elif format == "srt":
                result = format_srt(segments)
            elif format == "markdown":
                result = format_markdown(segments)
            else:
                result = format_json(segments, transcription_info)

            elapsed = time.time() - start_time
            yield f"data: {json.dumps({'type': 'done', 'elapsed_seconds': round(elapsed, 2), 'result': result})}\n\n"

    return StreamingResponse(generate_sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# Live transcription (WebSocket) — unchanged
# ---------------------------------------------------------------------------

@app.websocket(
    "/ws/transcribe",
    name="Live transcription WebSocket",
)
async def websocket_transcribe(websocket: WebSocket, language: str = Query("auto")):
    """WebSocket endpoint for live audio transcription."""
    if language not in VALID_LANGUAGES:
        await websocket.close(code=4000, reason=f"Invalid language: {language}")
        return
    await handle_transcription_session(websocket, language)


@app.get(
    "/live/health",
    tags=["Live"],
    summary="Check live transcription status",
    description="Returns status metadata for the live transcription subsystem used by the browser microphone UI.",
)
def live_health():
    return {"status": "ok", "service": "live-transcription"}


@app.get(
    "/live/languages",
    tags=["Live"],
    summary="List live transcription languages",
    description="Returns the language choices supported by the live transcription WebSocket endpoint.",
)
def live_languages():
    return {"languages": [
        {"code": "auto", "name": "Auto-detect"},
        {"code": "en", "name": "English"},
        {"code": "de", "name": "German"},
    ]}


# ---------------------------------------------------------------------------
# Analysis endpoints (merged from analyzer service)
# ---------------------------------------------------------------------------

@app.get(
    "/tasks",
    tags=["Discovery"],
    summary="List built-in analysis tasks",
    description="Returns built-in LLM analysis task definitions, including prompt text and default precondition dependencies.",
)
def list_tasks():
    """List available analysis tasks with full details including prompts."""
    return get_all_tasks()


@app.get(
    "/models",
    tags=["Discovery"],
    summary="List allowed LLM models",
    description="Returns model IDs that are accepted by analysis endpoints. Model IDs are validated server-side before LiteLLM/OpenRouter calls are made.",
)
def list_models():
    """List allowed LLM models."""
    return get_allowed_models()


@app.get(
    "/upload/config",
    tags=["Uploads", "Discovery"],
    summary="Get upload limits",
    description="Returns direct-upload and chunk-size limits so clients can decide when to use the chunked upload flow.",
)
def upload_config():
    """Expose upload limits so the UI can avoid proxy-level failures."""
    return {
        "cloudflare_cap_bytes": CLOUDFLARE_UPLOAD_CAP_BYTES,
        "max_direct_upload_bytes": MAX_DIRECT_UPLOAD_BYTES,
        "chunk_upload_size_bytes": CHUNK_UPLOAD_SIZE_BYTES,
    }


@app.post(
    "/upload/init",
    tags=["Uploads"],
    summary="Start a chunked upload",
    description="Creates a chunked upload session for a large audio/video file and returns the upload ID, chunk size, and expected chunk count.",
)
async def upload_init(
    filename: str = Form(...),
    size: int = Form(...),
    content_type: str = Form("application/octet-stream"),
):
    """Initialize a chunked upload session for files that exceed direct proxy limits."""
    if size <= 0:
        raise HTTPException(status_code=400, detail="File is empty")
    _validate_supported_filename(filename)

    upload_id = uuid.uuid4().hex
    total_chunks = max(1, math.ceil(size / CHUNK_UPLOAD_SIZE_BYTES))
    meta = {
        "upload_id": upload_id,
        "filename": Path(filename).name,
        "size": size,
        "content_type": content_type,
        "chunk_size": CHUNK_UPLOAD_SIZE_BYTES,
        "total_chunks": total_chunks,
        "status": "initialized",
        "created_at": time.time(),
    }
    _write_upload_meta(upload_id, meta)
    _upload_parts_dir(upload_id).mkdir(parents=True, exist_ok=True)
    return meta


@app.post(
    "/upload/chunk",
    tags=["Uploads"],
    summary="Upload one file chunk",
    description="Stores one zero-indexed chunk for a previously initialized upload session. Each chunk must be no larger than the configured chunk size.",
)
async def upload_chunk(
    upload_id: str = Form(...),
    index: int = Form(...),
    chunk: UploadFile = File(...),
):
    """Store one chunk for a previously initialized upload session."""
    meta = _read_upload_meta(upload_id)
    if meta.get("status") == "ready":
        raise HTTPException(status_code=409, detail="Upload already completed")
    total_chunks = int(meta["total_chunks"])
    if index < 0 or index >= total_chunks:
        raise HTTPException(status_code=400, detail="Chunk index out of range")

    data = await chunk.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty chunk")
    if len(data) > CHUNK_UPLOAD_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="Chunk exceeds allowed size")

    part_path = _upload_parts_dir(upload_id) / f"{index:06d}.part"
    part_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(part_path, "wb") as f:
        await f.write(data)

    return {"upload_id": upload_id, "index": index, "size": len(data)}


@app.post(
    "/upload/complete",
    tags=["Uploads"],
    summary="Complete a chunked upload",
    description="Verifies all chunks are present, assembles the uploaded file, and marks the upload as ready for `/analyze/stream` via `upload_id`.",
)
async def upload_complete(upload_id: str = Form(...)):
    """Assemble all uploaded chunks into a single file ready for transcription."""
    meta = _read_upload_meta(upload_id)
    total_chunks = int(meta["total_chunks"])
    parts_dir = _upload_parts_dir(upload_id)
    missing = [i for i in range(total_chunks) if not (parts_dir / f"{i:06d}.part").exists()]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing upload chunks: {missing[:5]}")

    assembled_path = _upload_file_path(upload_id, meta["filename"])
    async with aiofiles.open(assembled_path, "wb") as out:
        for i in range(total_chunks):
            part_path = parts_dir / f"{i:06d}.part"
            async with aiofiles.open(part_path, "rb") as src:
                while chunk := await src.read(1024 * 1024):
                    await out.write(chunk)

    meta["status"] = "ready"
    meta["assembled_path"] = str(assembled_path)
    _write_upload_meta(upload_id, meta)
    return {
        "upload_id": upload_id,
        "filename": meta["filename"],
        "size": meta["size"],
        "status": meta["status"],
    }


@app.post(
    "/analyze",
    tags=["Analysis"],
    summary="Transcribe and analyze an uploaded media file",
    description="Runs the complete non-streaming pipeline: upload media, transcribe it with the selected ASR backend, then run one or more built-in LLM analysis tasks.",
)
async def analyze(
    file: UploadFile = File(...),
    language: str = Form("auto"),
    output_language: str = Form("source"),
    model: str = Form(""),
    asr_backend: str = Form(DEFAULT_ASR_PROVIDER),
    keyword_bias: str = Form(""),
    tasks: Annotated[list[str], Form()] = ["summary", "keypoints"],
):
    """Full pipeline: transcribe audio/video then analyze with LLM."""
    valid_tasks = get_valid_tasks()
    invalid = set(tasks) - valid_tasks
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid tasks: {invalid}. Valid: {valid_tasks}")
    if not tasks:
        raise HTTPException(status_code=400, detail="At least one task required")

    if model and not is_valid_model(model):
        raise HTTPException(status_code=400, detail=f"Model not allowed: {model}")
    asr_backend = _validate_asr_request(asr_backend)
    keywords = parse_keyword_bias(keyword_bias)

    file_content = await _read_direct_upload(file)
    filename = file.filename or "audio.mp3"
    target_language = output_language if output_language != "source" else None

    try:
        transcript = await _transcribe_file_local(file_content, filename, language, asr_backend, keywords)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")

    try:
        results = await analyze_multiple_tasks(transcript, tasks, target_language, model or None)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")

    return {"transcript": transcript, "analyses": results}


def _build_dag_and_sort(task_ids: list[str], preconditions: dict[str, str | None]) -> list[str]:
    """
    Build dependency DAG, auto-include missing precondition tasks,
    detect cycles, and return topologically sorted task list.
    Raises ValueError on circular dependency.
    """
    # Expand: auto-include precondition tasks not in the list
    all_tasks = set(task_ids)
    queue = list(task_ids)
    while queue:
        tid = queue.pop()
        pre = preconditions.get(tid)
        if pre and pre not in all_tasks:
            all_tasks.add(pre)
            queue.append(pre)

    # Build adjacency: pre -> tid (pre must run before tid)
    graph: dict[str, list[str]] = {t: [] for t in all_tasks}
    in_degree: dict[str, int] = {t: 0 for t in all_tasks}
    for tid in all_tasks:
        pre = preconditions.get(tid)
        if pre and pre in all_tasks:
            graph[pre].append(tid)
            in_degree[tid] += 1

    # Kahn's algorithm
    queue = [t for t in all_tasks if in_degree[t] == 0]
    sorted_tasks: list[str] = []
    while queue:
        queue.sort()  # deterministic order
        node = queue.pop(0)
        sorted_tasks.append(node)
        for dep in graph[node]:
            in_degree[dep] -= 1
            if in_degree[dep] == 0:
                queue.append(dep)

    if len(sorted_tasks) != len(all_tasks):
        raise ValueError("Circular dependency detected among tasks")

    return sorted_tasks


@app.post(
    "/analyze/stream",
    tags=["Analysis"],
    summary="Stream transcription and analysis workflow events",
    description="Streams the full workflow over Server-Sent Events. Provide exactly one source: `file`, `transcript_text`, or a completed chunked-upload `upload_id`. Supports built-in tasks, custom tasks, and task preconditions.",
)
async def analyze_stream(
    file: UploadFile = File(None),
    transcript_text: str = Form(None),
    upload_id: str = Form(None),
    language: str = Form("auto"),
    output_language: str = Form("source"),
    model: str = Form(""),
    asr_backend: str = Form(DEFAULT_ASR_PROVIDER),
    keyword_bias: str = Form(""),
    tasks: Annotated[list[str], Form()] = [],
    custom_tasks: str = Form("{}"),
    preconditions: str = Form("{}"),
):
    """Full pipeline with SSE streaming progress. Accepts file, transcript_text, or chunked upload_id."""
    try:
        custom_task_defs = json.loads(custom_tasks) if custom_tasks else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid custom_tasks JSON")

    try:
        precondition_map = json.loads(preconditions) if preconditions else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid preconditions JSON")

    valid_tasks = get_valid_tasks()
    for task_id in tasks:
        if task_id not in valid_tasks and task_id not in custom_task_defs:
            raise HTTPException(status_code=400, detail=f"Unknown task: {task_id}")

    if tasks and not model:
        raise HTTPException(status_code=400, detail="Model selection required for analysis tasks")
    if model and not is_valid_model(model):
        raise HTTPException(status_code=400, detail=f"Model not allowed: {model}")
    asr_backend = _validate_asr_request(asr_backend)
    keywords = parse_keyword_bias(keyword_bias)

    # Accept exactly one source: file upload, raw transcript text, or chunked upload_id
    provided_sources = sum(1 for source in (file, transcript_text, upload_id) if source)
    if provided_sources == 0:
        raise HTTPException(status_code=400, detail="Provide file, transcript_text, or upload_id")
    if provided_sources > 1:
        raise HTTPException(status_code=400, detail="Provide only one of file, transcript_text, or upload_id")

    # Build resolved precondition map: merge built-in defaults with overrides
    resolved_preconditions: dict[str, str | None] = {}
    for task_id in tasks:
        # Priority: explicit precondition_map > custom_task_defs > built-in TASKS
        if task_id in precondition_map:
            resolved_preconditions[task_id] = precondition_map[task_id] or None
        elif task_id in custom_task_defs and "precondition" in custom_task_defs[task_id]:
            resolved_preconditions[task_id] = custom_task_defs[task_id]["precondition"] or None
        elif task_id in TASKS:
            resolved_preconditions[task_id] = TASKS[task_id].get("precondition")
        else:
            resolved_preconditions[task_id] = None

    # Also resolve preconditions for auto-included tasks
    def _resolve_pre(tid: str) -> str | None:
        if tid in precondition_map:
            return precondition_map[tid] or None
        if tid in custom_task_defs and "precondition" in custom_task_defs[tid]:
            return custom_task_defs[tid]["precondition"] or None
        if tid in TASKS:
            return TASKS[tid].get("precondition")
        return None

    # Expand auto-includes and resolve their preconditions
    expanded = set(tasks)
    queue = list(tasks)
    while queue:
        tid = queue.pop()
        if tid not in resolved_preconditions:
            resolved_preconditions[tid] = _resolve_pre(tid)
        pre = resolved_preconditions.get(tid)
        if pre and pre not in expanded:
            expanded.add(pre)
            resolved_preconditions[pre] = _resolve_pre(pre)
            queue.append(pre)

    # Build sorted execution order
    try:
        sorted_tasks = _build_dag_and_sort(list(expanded), resolved_preconditions)
    except ValueError as exc:
        error_message = str(exc)

        async def error_sse():
            yield f"data: {json.dumps({'type': 'error', 'message': error_message})}\n\n"

        return StreamingResponse(error_sse(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})

    originally_selected = set(tasks)

    file_content = None
    filename = None
    file_size = None
    uploaded_meta = None
    cleanup_upload_id = None
    if file:
        filename = file.filename or "audio.mp3"
        _validate_supported_filename(filename)
        file_content = await _read_direct_upload(file)
        file_size = len(file_content)
    elif upload_id:
        uploaded_meta = _read_upload_meta(upload_id)
        if uploaded_meta.get("status") != "ready":
            raise HTTPException(status_code=400, detail="Upload is not complete yet")
        filename = uploaded_meta.get("filename") or "audio.mp3"
        file_size = uploaded_meta.get("size")
        cleanup_upload_id = upload_id

    target_language = output_language if output_language != "source" else None

    async def generate_sse():
        transcript = None
        results = {}
        try:
            # If we have raw transcript text, skip transcription
            if transcript_text:
                transcript = transcript_text
                yield f"data: {json.dumps({'type': 'transcript', 'content': transcript})}\n\n"
            else:
                # Transcription flow for file upload
                yield f"data: {json.dumps({'type': 'transcribe_start', 'filename': filename, 'file_size': file_size})}\n\n"

                try:
                    if uploaded_meta:
                        assembled_path = Path(uploaded_meta["assembled_path"])
                        async for event in _transcribe_path_streaming_local(
                            assembled_path,
                            filename,
                            language,
                            asr_backend,
                            keywords,
                        ):
                            if event["type"] == "stage":
                                yield f"data: {json.dumps({'type': 'transcribe_stage', 'stage': event['stage'], 'message': event['message']})}\n\n"
                            elif event["type"] == "progress":
                                yield f"data: {json.dumps({'type': 'transcribe_progress', 'percent': event['percent'], 'text': event.get('text', '')})}\n\n"
                            elif event["type"] == "done":
                                transcript = event["result"]
                                yield f"data: {json.dumps({'type': 'transcribe_complete'})}\n\n"
                            elif event["type"] == "error":
                                yield f"data: {json.dumps({'type': 'error', 'message': event['message']})}\n\n"
                                return
                    else:
                        async for event in _transcribe_file_streaming_local(
                            file_content,
                            filename,
                            language,
                            asr_backend,
                            keywords,
                        ):
                            if event["type"] == "stage":
                                yield f"data: {json.dumps({'type': 'transcribe_stage', 'stage': event['stage'], 'message': event['message']})}\n\n"
                            elif event["type"] == "progress":
                                yield f"data: {json.dumps({'type': 'transcribe_progress', 'percent': event['percent'], 'text': event.get('text', '')})}\n\n"
                            elif event["type"] == "done":
                                transcript = event["result"]
                                yield f"data: {json.dumps({'type': 'transcribe_complete'})}\n\n"
                            elif event["type"] == "error":
                                yield f"data: {json.dumps({'type': 'error', 'message': event['message']})}\n\n"
                                return
                except Exception as e:
                    yield f"data: {json.dumps({'type': 'error', 'message': f'Transcription failed: {str(e)}'})}\n\n"
                    return

                if not transcript:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'No transcript received'})}\n\n"
                    return

                yield f"data: {json.dumps({'type': 'transcript', 'content': transcript})}\n\n"

            if sorted_tasks:
                total_tasks = len(sorted_tasks)
                for i, task_id in enumerate(sorted_tasks):
                    if task_id in custom_task_defs:
                        task_info = custom_task_defs[task_id]
                    else:
                        task_info = TASKS.get(task_id, {})

                    task_name = task_info.get('name', task_id)
                    custom_prompt = custom_task_defs.get(task_id, {}).get('prompt') if task_id in custom_task_defs else None
                    is_auto = task_id not in originally_selected

                    yield f"data: {json.dumps({'type': 'task_start', 'task': task_id, 'name': task_name, 'index': i, 'total': total_tasks, 'auto': is_auto})}\n\n"

                    # Determine input: precondition output or raw transcript
                    pre = resolved_preconditions.get(task_id)
                    if pre and pre in results:
                        input_text = results[pre]
                    else:
                        input_text = transcript

                    try:
                        result = await analyze_transcript(input_text, task_id, target_language, model, custom_prompt)
                        results[task_id] = result
                        yield f"data: {json.dumps({'type': 'task_complete', 'task': task_id, 'result': result, 'auto': is_auto})}\n\n"
                    except Exception as e:
                        logger.exception("Analysis task failed", extra={"task_id": task_id, "model": model or "", "filename": filename or "", "upload_id": upload_id or ""})
                        yield f"data: {json.dumps({'type': 'task_error', 'task': task_id, 'message': str(e)})}\n\n"

            yield f"data: {json.dumps({'type': 'done', 'results': results})}\n\n"
        finally:
            if cleanup_upload_id:
                _cleanup_upload(cleanup_upload_id)

    return StreamingResponse(generate_sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


@app.post(
    "/analyze/transcript",
    tags=["Analysis"],
    summary="Analyze an existing transcript",
    description="Runs built-in LLM analysis tasks against provided transcript text without a transcription step. Uses the server default model configuration.",
)
async def analyze_transcript_only(
    transcript: str = Form(...),
    tasks: Annotated[list[str], Form()] = ["summary", "keypoints"],
):
    """Analyze a provided transcript (no transcription step)."""
    valid_tasks = get_valid_tasks()
    invalid = set(tasks) - valid_tasks
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid tasks: {invalid}")
    if not transcript.strip():
        raise HTTPException(status_code=400, detail="Transcript is empty")

    try:
        results = await analyze_multiple_tasks(transcript, tasks)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")

    return {"analyses": results}


# ---------------------------------------------------------------------------
# Static files & pages
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get(
    "/",
    tags=["UI"],
    summary="Open the main browser UI",
    description="Serves the main upload, pasted-transcript, transcription, and analysis browser interface.",
    include_in_schema=False,
)
async def root(request: Request):
    """Serve the main analyzer UI."""
    return _ui_file_response("static/index.html", request)


@app.get(
    "/live",
    tags=["UI"],
    summary="Open the live transcription UI",
    description="Serves the browser microphone/live transcription interface.",
    include_in_schema=False,
)
async def live(request: Request):
    """Serve the live transcription UI."""
    return _ui_file_response("static/live.html", request)


@app.get(
    "/settings",
    tags=["UI"],
    summary="Open the settings UI",
    description="Serves the browser settings and analysis task configuration interface.",
    include_in_schema=False,
)
async def settings_page(request: Request):
    """Serve the Settings UI."""
    return _ui_file_response("static/settings.html", request)
