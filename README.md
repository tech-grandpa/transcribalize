# Transcribalize

GPU-accelerated speech-to-text transcription and transcript analysis, packaged as a self-hosted FastAPI service with a browser UI and REST/WebSocket APIs.

Transcribalize is built around [faster-whisper](https://github.com/SYSTRAN/faster-whisper) for high-throughput local transcription. It also includes an experimental IBM Granite Speech backend for uploaded files. It supports audio and video uploads, streaming progress updates, live browser transcription, multiple export formats, chunked uploads for large files, and optional LLM-powered transcript analysis through LiteLLM-compatible providers.

## Features

- **Fast GPU transcription** using Whisper `large-v3-turbo` through faster-whisper
- **Experimental file ASR backends** for IBM Granite Speech 4.1 2B and 2B Plus
- **Keyword biasing** for uploaded files, passed as Granite prompt keywords or Whisper initial-prompt hints
- **Audio and video input** with FFmpeg-based audio extraction
- **Browser UI** for file upload, pasted transcripts, live transcription, settings, and analysis workflows
- **REST API** for synchronous transcription and Server-Sent Events progress streaming
- **WebSocket live transcription** for browser microphone input
- **Large upload support** with chunked upload endpoints for proxy-friendly deployments
- **Multiple output formats**: JSON, SRT, Markdown, and plain text
- **Transcript analysis** with built-in tasks such as summaries, key points, improvements, concepts, and action items
- **Configurable LLM backend** via LiteLLM/OpenAI-compatible environment variables
- **Docker Compose deployment** with NVIDIA GPU support and persisted model cache

## Architecture

```text
Browser UI / API client
        |
        v
FastAPI service
  |-- upload handling and chunk assembly
  |-- FFmpeg audio extraction
  |-- file ASR provider selection
  |-- faster-whisper / experimental Granite transcription
  |-- live WebSocket transcription
  |-- formatting: JSON / SRT / Markdown / text
  `-- optional LiteLLM transcript analysis
        |
        v
NVIDIA GPU + persisted Hugging Face model cache
```

The main service lives in [`transcriber/`](transcriber/):

- [`transcriber/app/main.py`](transcriber/app/main.py) — FastAPI routes and upload/transcription orchestration
- [`transcriber/app/transcriber.py`](transcriber/app/transcriber.py) — faster-whisper integration
- [`transcriber/app/asr_providers.py`](transcriber/app/asr_providers.py) — batch ASR provider registry and keyword parsing
- [`transcriber/app/granite_transcriber.py`](transcriber/app/granite_transcriber.py) — experimental Granite Speech integration
- [`transcriber/app/live_transcription.py`](transcriber/app/live_transcription.py) — live WebSocket transcription
- [`transcriber/app/llm.py`](transcriber/app/llm.py) — optional transcript analysis via LiteLLM
- [`transcriber/static/`](transcriber/static/) — browser UI
- [`transcriber/docs/API.md`](transcriber/docs/API.md) — detailed API reference

## Requirements

For Docker deployment:

- Linux host with an NVIDIA GPU
- Docker and Docker Compose
- NVIDIA Container Toolkit
- Enough disk space for Whisper model cache

For local Python development outside Docker:

- Python 3.12+
- FFmpeg available on `PATH`
- CUDA-capable PyTorch environment for GPU acceleration

## Quick start

```bash
git clone https://github.com/tech-grandpa/transcribalize.git
cd transcribalize/transcriber
docker compose up --build
```

Open the UI:

```bash
open http://localhost:8000
```

Or check the API:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status":"ok"}
```

## Configuration

The transcription service runs without an LLM key. Transcript analysis requires an OpenAI-compatible API key or other LiteLLM-supported configuration.

Create an environment file from the example, or export the same variables in your shell:

```bash
cp .env.example .env
```

Example:

```env
OPENAI_API_KEY=your_openrouter_api_key_here
OPENAI_BASE_URL=https://openrouter.ai/api/v1
DEFAULT_MODEL=anthropic/claude-opus-4.8
ASR_BACKEND=whisper
```

Docker Compose reads this repository-root `.env` file automatically. Keep real values local; `.env` files are ignored by Git and excluded from the Docker build context.

## Usage examples

### Transcribe a file

```bash
curl -X POST http://localhost:8000/transcribe \
  -F "file=@meeting.mp3" \
  -F "language=auto" \
  -F "format=srt"
```

Experimental Granite file transcription:

```bash
curl -X POST http://localhost:8000/transcribe \
  -F "file=@meeting.mp3" \
  -F "asr_backend=granite-2b" \
  -F "keyword_bias=Granite, Watson, OpenShift" \
  -F "format=text"
```

Supported `format` values:

- `json`
- `text`
- `srt`
- `markdown`

Supported `language` values:

- `auto`
- `en`
- `de`

Supported `asr_backend` values:

- `whisper` (default, also used for live transcription)
- `granite-2b` (experimental file transcription)
- `granite-2b-plus` (experimental file transcription)

### Stream transcription progress

```bash
curl -N -X POST http://localhost:8000/transcribe/stream \
  -F "file=@meeting.mp3" \
  -F "language=auto" \
  -F "format=markdown"
```

### Use the browser UI

After starting the service, visit:

- `http://localhost:8000/` — upload, transcribe, paste transcript, and analyze
- `http://localhost:8000/live` — live microphone transcription
- `http://localhost:8000/settings` — analysis task configuration

## API documentation

See [`transcriber/docs/API.md`](transcriber/docs/API.md) for the full API reference, including:

- transcription endpoints
- streaming events
- chunked uploads
- analysis endpoints
- response formats
- error handling

## Development

Install test dependencies and run the API test suite:

```bash
cd transcriber
python3 -m venv .venv-test
source .venv-test/bin/activate
pip install -U pip
pip install -r requirements-test.txt
pytest -q tests
```

Run the service locally with Uvicorn:

```bash
cd transcriber
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Deployment notes

The repository intentionally avoids checked-in machine-specific deployment details. Keep real hosts, usernames, runner labels, filesystem paths, and credentials in private deployment configuration.

The included GitHub Actions workflow can deploy from a self-hosted runner when configured with repository settings. See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for details.

## Security and privacy

Transcribalize is designed to be self-hosted. Uploaded media is processed locally by the service, and temporary upload files are stored under the system temp directory during processing.

If transcript analysis is enabled, transcript text is sent to the configured LLM provider. Choose a provider and retention policy appropriate for your data.

Before exposing the service publicly, put it behind authentication and TLS, and review upload limits for your infrastructure.

## Contributing

Contributions are welcome. Please keep changes focused, include tests for API behavior where practical, and avoid committing private deployment details, credentials, media files, or generated caches.

## License

Licensed under the [Apache License 2.0](LICENSE).
