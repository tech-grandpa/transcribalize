# Transcribalize API Reference

Transcribalize is a self-hosted FastAPI service for turning audio/video or existing transcript text into usable written output.

It provides:

- **File transcription** for audio and video uploads.
- **Progress streaming** over Server-Sent Events (SSE) for long transcriptions and analysis jobs.
- **Live browser transcription** over WebSocket for microphone audio.
- **Transcript analysis** with built-in LLM tasks such as improved transcript, summary, key points, concepts, and action items.
- **Large-file upload support** through chunked upload endpoints for deployments behind proxy limits such as Cloudflare.
- **Browser UI pages** for upload/analysis, live transcription, and settings.

The API is designed for both the bundled browser UI and external backend-to-backend integrations.

> **Security note:** The application currently does not enforce API keys, user login, or CORS policy in the FastAPI app itself. If exposing it outside a private network, put it behind TLS, authentication, and appropriate reverse-proxy upload/time-out limits.

---

## Base URL

Local development examples use:

```text
http://localhost:8000
```

Replace that with your deployment origin, for example:

```text
https://transcriber.example.com
```

All upload endpoints use `multipart/form-data` unless stated otherwise.

---

## Quick start

### Health check

```bash
curl http://localhost:8000/health
```

Response:

```json
{"status":"ok"}
```

### Transcribe a file to Markdown

```bash
curl -X POST http://localhost:8000/transcribe \
  -F "file=@meeting.mp3" \
  -F "language=auto" \
  -F "format=markdown"
```

### Transcribe and analyze a file with streaming progress

```bash
curl -N -X POST http://localhost:8000/analyze/stream \
  -F "file=@meeting.mp3" \
  -F "language=auto" \
  -F "output_language=source" \
  -F "asr_backend=whisper" \
  -F "model=anthropic/claude-opus-4.8" \
  -F "tasks=summary" \
  -F "tasks=keypoints"
```

---

## Core concepts

### Input sources

Most transcription workflows start from a media file. The streaming analysis endpoint can also start from an existing transcript or a completed chunked upload.

| Source | Endpoint | Use when |
|---|---|---|
| Direct file upload | `POST /transcribe`, `POST /transcribe/stream`, `POST /analyze`, `POST /analyze/stream` | File is small enough for your reverse proxy/request size limits |
| Existing transcript text | `POST /analyze/stream`, `POST /analyze/transcript` | You already have transcript text and only want LLM analysis |
| Chunked upload | `/upload/*` + `POST /analyze/stream` | File is too large for direct upload; default direct limit is 95 MiB |
| Browser microphone stream | `WS /ws/transcribe` | Live transcription UI/client |

### Supported file types

The service accepts media formats supported by FFmpeg. Common examples:

| Type | Extensions |
|---|---|
| Audio | `.mp3`, `.wav`, `.flac`, `.ogg`, `.m4a`, `.aac`, `.wma`, `.opus`, `.webm` |
| Video | `.mp4`, `.mkv`, `.avi`, `.mov`, `.wmv`, `.flv`, `.mpeg`, `.mpg`, `.ts`, `.webm` |

Video files are converted to audio internally before transcription.

### Languages

| Value | Meaning |
|---|---|
| `auto` | Auto-detect language |
| `en` | Force English transcription |
| `de` | Force German transcription |

The public API currently validates only `auto`, `en`, and `de`.

### Output formats for transcription

| `format` | HTTP response | Use for |
|---|---|---|
| `markdown` | `text/markdown` | Readable paragraphs/document output |
| `text` | `text/plain` | Plain transcript text |
| `srt` | `text/plain` attachment | Subtitle files |
| `json` | `application/json` | Programmatic access to text, language metadata, and segments |

### ASR backends

Discover live backend metadata with `GET /asr/providers`.

Current backend IDs:

| `asr_backend` | Name | Keyword bias | Streaming | Notes |
|---|---|---:|---:|---|
| `whisper` | Whisper large-v3 turbo | Yes | Yes | Default faster-whisper backend. Also used by live WebSocket transcription. |
| `parakeet-tdt-0.6b-v3` | NVIDIA Parakeet TDT 0.6B v3 | No | No | File transcription backend via Transformers. |
| `granite-2b` | IBM Granite Speech 4.1 2B | Yes | No | Experimental file transcription backend. |
| `granite-2b-plus` | IBM Granite Speech 4.1 2B Plus | Yes | No | Experimental file transcription backend. |

`keyword_bias` is a comma- or newline-separated hint list. It is useful for proper nouns, product names, and technical terms. Backends that do not support keyword hints ignore or do not use them.

### LLM analysis models

Discover live model metadata with `GET /models`.

The default model is currently:

```text
anthropic/claude-opus-4.8
```

Allowed model IDs are validated server-side. Current configured IDs include:

- `anthropic/claude-opus-4.8`
- `anthropic/claude-opus-4.8-fast`
- `anthropic/claude-sonnet-4.6`
- `anthropic/claude-haiku-4.5`
- `google/gemini-3.5-flash`
- `google/gemini-3.1-pro-preview`
- `google/gemini-3.1-flash-lite`
- `qwen/qwen3.7-max`
- `minimax/minimax-m3`
- `moonshotai/kimi-k2.6`
- `openai/gpt-5.5-pro`
- `openai/gpt-5.5`

LLM analysis requires a LiteLLM/OpenAI-compatible provider configuration, commonly OpenRouter:

```env
OPENAI_API_KEY=your_openrouter_api_key_here
OPENAI_BASE_URL=https://openrouter.ai/api/v1
DEFAULT_MODEL=anthropic/claude-opus-4.8
```

---

## Endpoint overview

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Basic service health check |
| `GET` | `/asr/providers` | List file-transcription ASR backends |
| `POST` | `/transcribe` | Upload media and return final transcription |
| `POST` | `/transcribe/stream` | Upload media and stream transcription progress over SSE |
| `WS` | `/ws/transcribe?language=auto` | Live audio transcription WebSocket |
| `GET` | `/live/health` | Live transcription health check |
| `GET` | `/live/languages` | Live transcription language choices |
| `GET` | `/tasks` | List built-in LLM analysis tasks |
| `GET` | `/models` | List allowed LLM analysis models |
| `GET` | `/upload/config` | Return direct/chunked upload size settings |
| `POST` | `/upload/init` | Start a chunked upload session |
| `POST` | `/upload/chunk` | Upload one file chunk |
| `POST` | `/upload/complete` | Assemble uploaded chunks |
| `POST` | `/analyze` | Upload media, transcribe it, and return LLM analysis results |
| `POST` | `/analyze/stream` | Stream transcription and analysis progress over SSE |
| `POST` | `/analyze/transcript` | Analyze provided transcript text without transcription |
| `GET` | `/` | Browser upload/analysis UI |
| `GET` | `/live` | Browser live transcription UI |
| `GET` | `/settings` | Browser settings UI |

---

## Discovery and metadata

### `GET /health`

Returns basic service status.

```bash
curl http://localhost:8000/health
```

```json
{"status":"ok"}
```

### `GET /asr/providers`

Returns the ASR backends shown in the UI.

```bash
curl http://localhost:8000/asr/providers
```

Example response:

```json
[
  {
    "id": "whisper",
    "name": "Whisper large-v3 turbo",
    "description": "Default faster-whisper backend. Keyword hints are passed as hotwords.",
    "experimental": false,
    "supports_keywords": true,
    "supports_streaming": true
  },
  {
    "id": "parakeet-tdt-0.6b-v3",
    "name": "NVIDIA Parakeet TDT 0.6B v3",
    "description": "Experimental multilingual NVIDIA Parakeet backend via Transformers.",
    "experimental": true,
    "supports_keywords": false,
    "supports_streaming": false
  }
]
```

### `GET /tasks`

Returns built-in transcript analysis task definitions. The response is an object keyed by task ID.

```bash
curl http://localhost:8000/tasks
```

Built-in task IDs:

| Task ID | Name | Description | Default precondition |
|---|---|---|---|
| `improve` | Improved Transcript | Fix transcription errors and improve readability | none |
| `summary` | Summary | Executive summary of the content | `improve` |
| `keypoints` | Key Points | Bullet list of main points | `improve` |
| `concepts` | Concepts & Documentation | Documentation of concepts and information | `improve` |
| `tasks` | Action Items & Tasks | Extract action items with deadlines | `improve` |

For `/analyze/stream`, built-in preconditions are auto-included. For example, requesting `summary` also runs `improve` first unless overridden by custom precondition settings.

### `GET /models`

Returns allowed LLM model choices.

```bash
curl http://localhost:8000/models
```

Example response:

```json
[
  {"id":"anthropic/claude-opus-4.8","name":"Claude Opus 4.8"},
  {"id":"openai/gpt-5.5","name":"Gpt 5.5"}
]
```

---

## Transcription endpoints

### `POST /transcribe`

Uploads an audio/video file and returns the completed transcription in the requested format.

#### Form fields

| Field | Type | Required | Default | Description |
|---|---|---:|---|---|
| `file` | file | Yes | — | Audio/video file to transcribe |
| `language` | string | No | `auto` | `auto`, `en`, or `de` |
| `format` | string | No | `markdown` | `markdown`, `text`, `srt`, or `json` |
| `asr_backend` | string | No | `whisper` | Backend ID from `/asr/providers` |
| `keyword_bias` | string | No | empty | Comma/newline-separated keyword hints |

#### Examples

Plain transcript:

```bash
curl -X POST http://localhost:8000/transcribe \
  -F "file=@meeting.mp3" \
  -F "language=auto" \
  -F "format=text"
```

Subtitle file:

```bash
curl -X POST http://localhost:8000/transcribe \
  -F "file=@video.mp4" \
  -F "format=srt" \
  -o transcription.srt
```

Use keyword hints with Whisper:

```bash
curl -X POST http://localhost:8000/transcribe \
  -F "file=@meeting.mp3" \
  -F "asr_backend=whisper" \
  -F "keyword_bias=OpenRouter, Claude Opus, Transcribalize" \
  -F "format=json"
```

#### JSON response format

When `format=json`, the response is a JSON object:

```json
{
  "language": "en",
  "language_probability": 0.98,
  "duration": 180.5,
  "text": "Full transcription text...",
  "segments": [
    {"id": 0, "start": 0.0, "end": 4.5, "text": "First segment..."},
    {"id": 1, "start": 4.5, "end": 9.2, "text": "Second segment..."}
  ]
}
```

For `text`, `markdown`, and `srt`, the response body is the formatted transcript string.

### `POST /transcribe/stream`

Uploads an audio/video file and streams progress over Server-Sent Events.

Use this when the client needs progress feedback for long files. The final `done` event contains the same formatted result that `/transcribe` would return.

#### Form fields

Same as `POST /transcribe`.

#### Example

```bash
curl -N -X POST http://localhost:8000/transcribe/stream \
  -F "file=@meeting.mp3" \
  -F "language=auto" \
  -F "format=markdown"
```

#### SSE event format

Events are sent as:

```text
data: {JSON}\n\n
```

Event types:

```json
{"type":"start","duration":180.5,"estimated_seconds":12.6}
```

```json
{"type":"progress","percent":45,"text":"Current segment text..."}
```

```json
{"type":"done","elapsed_seconds":11.2,"result":"Final formatted transcript..."}
```

```json
{"type":"error","message":"Error description"}
```

When `format=json`, `result` is a JSON object. For other formats, `result` is a string.

---

## Analysis endpoints

Analysis uses LiteLLM and the configured OpenAI-compatible provider. If OpenRouter is configured through `OPENAI_BASE_URL`, model IDs from `/models` are sent through LiteLLM with OpenRouter routing.

### `POST /analyze`

Uploads a media file, transcribes it, then runs one or more built-in LLM analysis tasks. This is a non-streaming endpoint: the HTTP response arrives after transcription and all requested analysis tasks finish.

#### Form fields

| Field | Type | Required | Default | Description |
|---|---|---:|---|---|
| `file` | file | Yes | — | Audio/video file |
| `language` | string | No | `auto` | Transcription language: `auto`, `en`, or `de` |
| `output_language` | string | No | `source` | `source`, `en`, or `de`; controls analysis output language |
| `model` | string | No | server default | Must be an allowed model ID if provided |
| `asr_backend` | string | No | `whisper` | Backend ID from `/asr/providers` |
| `keyword_bias` | string | No | empty | Keyword hints for supported ASR backends |
| `tasks` | list[string] | No | `summary`, `keypoints` | Repeat the form field for multiple tasks |

#### Example

```bash
curl -X POST http://localhost:8000/analyze \
  -F "file=@meeting.mp3" \
  -F "language=auto" \
  -F "output_language=source" \
  -F "model=anthropic/claude-opus-4.8" \
  -F "tasks=summary" \
  -F "tasks=keypoints"
```

Response:

```json
{
  "transcript": "Full transcript text...",
  "analyses": {
    "summary": "Executive summary...",
    "keypoints": "- Key point one\n- Key point two"
  }
}
```

### `POST /analyze/stream`

Streams the full workflow over SSE. It can start from exactly one of:

- `file`
- `transcript_text`
- `upload_id` from the chunked upload flow

It can also run built-in tasks, custom one-off tasks, and task dependencies/preconditions.

#### Form fields

| Field | Type | Required | Default | Description |
|---|---|---:|---|---|
| `file` | file | No | — | Direct media upload source |
| `transcript_text` | string | No | — | Existing transcript source; skips transcription |
| `upload_id` | string | No | — | Completed chunked upload source |
| `language` | string | No | `auto` | Used when transcribing a file/upload |
| `output_language` | string | No | `source` | `source`, `en`, or `de` for LLM output |
| `model` | string | Required if `tasks` is non-empty | empty | Allowed LLM model ID |
| `asr_backend` | string | No | `whisper` | Backend ID from `/asr/providers` |
| `keyword_bias` | string | No | empty | Keyword hints for supported ASR backends |
| `tasks` | list[string] | No | empty | Repeat form field for multiple built-in/custom task IDs |
| `custom_tasks` | JSON string | No | `{}` | Custom task definitions keyed by task ID |
| `preconditions` | JSON string | No | `{}` | Override task dependency map |

Exactly one source must be supplied. Supplying none or multiple source fields returns `400`.

#### Example: file source + built-in tasks

```bash
curl -N -X POST http://localhost:8000/analyze/stream \
  -F "file=@meeting.mp3" \
  -F "language=auto" \
  -F "model=anthropic/claude-opus-4.8" \
  -F "tasks=summary" \
  -F "tasks=tasks"
```

#### Example: analyze existing transcript text

```bash
curl -N -X POST http://localhost:8000/analyze/stream \
  -F "transcript_text=We discussed the launch plan and assigned follow-up work..." \
  -F "model=anthropic/claude-opus-4.8" \
  -F "tasks=summary" \
  -F "tasks=keypoints"
```

#### Example: custom task

```bash
curl -N -X POST http://localhost:8000/analyze/stream \
  -F "transcript_text=Customer interview transcript..." \
  -F "model=anthropic/claude-opus-4.8" \
  -F "tasks=risks" \
  -F 'custom_tasks={"risks":{"name":"Risks","prompt":"Extract the top product risks from this transcript as a Markdown bullet list."}}'
```

#### SSE event types

Common event sequence for file/upload sources:

```json
{"type":"transcribe_start","filename":"meeting.mp3","file_size":12345678}
```

```json
{"type":"transcribe_stage","stage":"extract","message":"Extracting audio…"}
```

```json
{"type":"transcribe_stage","stage":"transcribe","message":"Running transcription with Whisper large-v3 turbo…"}
```

```json
{"type":"transcribe_progress","percent":42,"text":"Partial segment text..."}
```

```json
{"type":"transcribe_complete"}
```

```json
{"type":"transcript","content":"Full transcript text..."}
```

For each analysis task:

```json
{"type":"task_start","task":"summary","name":"Summary","index":0,"total":2,"auto":false}
```

```json
{"type":"task_complete","task":"summary","result":"Summary markdown...","auto":false}
```

Possible task-level error:

```json
{"type":"task_error","task":"summary","message":"LLM/provider error message"}
```

Final event:

```json
{"type":"done","results":{"summary":"Summary markdown..."}}
```

Global error event:

```json
{"type":"error","message":"Error description"}
```

### `POST /analyze/transcript`

Analyzes existing transcript text with built-in tasks only. This endpoint does not expose model selection or output-language selection in the current implementation; it uses the server default model and source-language behavior.

#### Form fields

| Field | Type | Required | Default | Description |
|---|---|---:|---|---|
| `transcript` | string | Yes | — | Transcript text to analyze |
| `tasks` | list[string] | No | `summary`, `keypoints` | Built-in task IDs; repeat field for multiple tasks |

#### Example

```bash
curl -X POST http://localhost:8000/analyze/transcript \
  -F "transcript=We reviewed the project timeline and agreed to ship next week." \
  -F "tasks=summary" \
  -F "tasks=tasks"
```

Response:

```json
{
  "analyses": {
    "summary": "Brief summary...",
    "tasks": "## Action Items\n\n[ ] ..."
  }
}
```

---

## Chunked uploads for large files

Direct uploads to `/analyze/stream` are limited by `MAX_DIRECT_UPLOAD_BYTES`, currently 95 MiB, so the UI can avoid Cloudflare's 100 MiB request cap. Use chunked uploads for larger files.

### `GET /upload/config`

Returns configured upload limits.

```bash
curl http://localhost:8000/upload/config
```

```json
{
  "cloudflare_cap_bytes": 104857600,
  "max_direct_upload_bytes": 99614720,
  "chunk_upload_size_bytes": 8388608
}
```

### Chunked upload flow

1. Initialize a session with `/upload/init`.
2. Upload every chunk with `/upload/chunk` using zero-based indexes.
3. Assemble the file with `/upload/complete`.
4. Pass the returned `upload_id` to `/analyze/stream`.

#### `POST /upload/init`

```bash
curl -X POST http://localhost:8000/upload/init \
  -F "filename=long-meeting.mp4" \
  -F "size=250000000" \
  -F "content_type=video/mp4"
```

Response:

```json
{
  "upload_id": "9e0f...",
  "filename": "long-meeting.mp4",
  "size": 250000000,
  "content_type": "video/mp4",
  "chunk_size": 8388608,
  "total_chunks": 30,
  "status": "initialized",
  "created_at": 1760000000.0
}
```

#### `POST /upload/chunk`

```bash
curl -X POST http://localhost:8000/upload/chunk \
  -F "upload_id=9e0f..." \
  -F "index=0" \
  -F "chunk=@chunk-000000.part"
```

Response:

```json
{"upload_id":"9e0f...","index":0,"size":8388608}
```

#### `POST /upload/complete`

```bash
curl -X POST http://localhost:8000/upload/complete \
  -F "upload_id=9e0f..."
```

Response:

```json
{"upload_id":"9e0f...","filename":"long-meeting.mp4","size":250000000,"status":"ready"}
```

#### Analyze completed upload

```bash
curl -N -X POST http://localhost:8000/analyze/stream \
  -F "upload_id=9e0f..." \
  -F "language=auto" \
  -F "model=anthropic/claude-opus-4.8" \
  -F "tasks=summary"
```

Completed chunked upload directories are cleaned up after `/analyze/stream` finishes when `upload_id` is used as the source.

---

## Live transcription WebSocket

### `WS /ws/transcribe?language=auto`

The WebSocket endpoint is used by the bundled live transcription page. It accepts live audio frames from a browser/client and sends transcription updates back over the same socket.

Supported `language` query values are `auto`, `en`, and `de`.

```text
ws://localhost:8000/ws/transcribe?language=auto
```

Related metadata endpoints:

```bash
curl http://localhost:8000/live/health
curl http://localhost:8000/live/languages
```

`GET /live/languages` returns:

```json
{
  "languages": [
    {"code":"auto","name":"Auto-detect"},
    {"code":"en","name":"English"},
    {"code":"de","name":"German"}
  ]
}
```

---

## Browser UI routes

These are normal HTML pages served by the FastAPI app:

| Path | Page |
|---|---|
| `/` | Main upload, pasted transcript, transcription, and analysis UI |
| `/live` | Compatibility redirect to the main UI's Live mode |
| `/settings` | Settings/task configuration UI |
| `/static/*` | Static assets |

---

## Error handling

Standard FastAPI validation errors use HTTP `422`. Application-level errors generally use the following statuses:

| Status | Common cause |
|---:|---|
| `400` | Invalid transcription language, format, ASR backend, task, chunk index, upload source combination, or unsupported file type |
| `409` | Upload session is already completed |
| `413` | Direct upload or chunk is too large |
| `422` | FFmpeg/audio extraction failed or FastAPI request validation failed |
| `500` | Transcription, analysis, model/provider, or server failure |

Typical JSON error response:

```json
{"detail":"Unsupported file format."}
```

SSE endpoints report workflow failures as events when possible:

```json
{"type":"error","message":"Transcription failed: ..."}
```

---

## Operational notes

- **Processing model:** `/transcribe` streams direct uploads to temporary storage. Streaming and analysis endpoints may read a direct upload into memory before writing it to temporary storage for FFmpeg and ASR.
- **GPU usage:** Whisper and local ASR backends are GPU-oriented in deployment. Performance depends on model, GPU, media duration, and cold-start state.
- **First request latency:** The first request may be slower while models load.
- **Long-running requests:** For long files, prefer `/transcribe/stream` or `/analyze/stream`, and configure reverse-proxy timeouts accordingly.
- **Chunked uploads:** Only `/analyze/stream` consumes an assembled `upload_id`. Individual chunks are size-limited, but the protocol has no total session-size ceiling, checksum, or automatic expiry for abandoned sessions. Add operational cleanup and proxy quotas for shared deployments.
- **Media validation:** File acceptance is based on the allowlisted filename extension. The submitted MIME type is not used to validate the media format; FFmpeg rejects content it cannot decode.
- **External services:** Backend-to-backend callers can use the REST/SSE endpoints directly. Browser clients hosted on a different origin may need CORS configured at the reverse proxy or in the FastAPI app.
- **Privacy:** Transcription runs in the service environment. LLM analysis sends transcript text to the configured LiteLLM/OpenAI-compatible provider.

---

## Minimal client examples

### Python: direct transcription

```python
import requests

with open("meeting.mp3", "rb") as f:
    response = requests.post(
        "http://localhost:8000/transcribe",
        files={"file": f},
        data={"language": "auto", "format": "json"},
        timeout=None,
    )
response.raise_for_status()
result = response.json()
print(result["text"])
```

### Python: consume SSE analysis events

```python
import json
import requests

with open("meeting.mp3", "rb") as f:
    response = requests.post(
        "http://localhost:8000/analyze/stream",
        files={"file": f},
        data=[
            ("language", "auto"),
            ("model", "anthropic/claude-opus-4.8"),
            ("tasks", "summary"),
            ("tasks", "keypoints"),
        ],
        stream=True,
        timeout=None,
    )

response.raise_for_status()
for line in response.iter_lines(decode_unicode=True):
    if not line or not line.startswith("data: "):
        continue
    event = json.loads(line[len("data: "):])
    print(event["type"], event)
```

### JavaScript: direct transcription from a browser form

```javascript
const formData = new FormData();
formData.append("file", fileInput.files[0]);
formData.append("language", "auto");
formData.append("format", "srt");

const response = await fetch("/transcribe", {
  method: "POST",
  body: formData,
});

if (!response.ok) {
  throw new Error(await response.text());
}

const srt = await response.text();
console.log(srt);
```
