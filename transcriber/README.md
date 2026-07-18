# GPU Transcription Service

Fast speech-to-text transcription using Whisper on GPU, with an experimental IBM Granite Speech file-transcription backend.

## Quick Start

```bash
# Build and run
docker compose up --build

# Access
open http://localhost:8000
```

## API

```bash
# Transcribe to SRT
curl -X POST http://localhost:8000/transcribe \
  -F "file=@video.mp4" \
  -F "format=srt"

# Stream with progress
curl -N -X POST http://localhost:8000/transcribe/stream \
  -F "file=@audio.mp3"

# Experimental Granite Speech 4.1 backend with keyword hints
curl -X POST http://localhost:8000/transcribe \
  -F "file=@audio.mp3" \
  -F "asr_backend=granite-2b" \
  -F "keyword_bias=Granite, Watson, OpenShift" \
  -F "format=text"
```

## Features

- **GPU-accelerated**: ~14x real-time on RTX 4000
- **Multiple formats**: JSON, SRT, Markdown, Text
- **Video support**: Extracts audio from MP4, MKV, etc.
- **Large files**: direct uploads with progress, plus chunked uploads for Cloudflare-safe large-file handling
- **Progress tracking**: upload, media-processing, transcription, and analysis stages are shown separately
- **Languages**: English, German (auto-detect)
- **ASR backends**: `whisper` by default, plus experimental file-only `granite-2b` and `granite-2b-plus`
- **Keyword biasing**: Granite receives prompt keywords directly; Whisper receives them as a best-effort `initial_prompt`

## Experimental Granite Speech

Granite Speech is available only for uploaded file transcription. Live transcription stays on Whisper.

Install optional dependencies locally:

```bash
pip install -r requirements-granite.txt
```

Build Docker with Granite dependencies:

```bash
docker compose build --build-arg INSTALL_GRANITE=true
```

The Granite integration is intentionally a first pass. It lazy-loads `transformers`, `torch`, and `torchaudio`, selects CUDA when available, and returns a clear runtime error if the optional dependencies or the required recent Transformers support are missing.

## Documentation

- [docs/API.md](docs/API.md) for technical API details
- [../docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md) for update and deployment steps

## Requirements

- Docker with NVIDIA Container Toolkit
- NVIDIA GPU with CUDA support
