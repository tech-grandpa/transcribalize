# Transcriber service

This directory contains the FastAPI application, browser UI, Docker image, and tests for Transcribalize.

For the project overview, hardware requirements, quick start, privacy boundary, and user workflows, read the [root README](../README.md). For endpoint details, read the [API reference](docs/API.md).

## Run the published image

No repository checkout is required:

```bash
docker run -d \
  --name transcribalize \
  --restart unless-stopped \
  --gpus all \
  -p 8000:8000 \
  -v transcribalize-models:/models \
  ghcr.io/tech-grandpa/transcribalize:0.1.0
```

## Build with Docker Compose

From the repository root:

```bash
docker compose -f transcriber/docker-compose.yml up --build
```

The service listens on `http://localhost:8000`. The supplied Compose build:

- requests one NVIDIA GPU
- installs the default Whisper runtime and optional Granite/Parakeet dependencies
- pre-downloads the Whisper model during the image build
- stores Hugging Face models in a persistent Docker volume
- mounts a 4 GiB temporary filesystem at `/tmp`
- reads optional analysis and ASR settings from the repository-root `.env`

Granite and Parakeet model weights are downloaded when those backends are first used. Both integrations are experimental and file-only.

## Service modules

- `app/main.py`: API routes, upload handling, workflow orchestration, and UI responses
- `app/asr_providers.py`: file-transcription backend registry
- `app/transcriber.py`: faster-whisper file transcription
- `app/parakeet_transcriber.py`: NVIDIA Parakeet file transcription
- `app/granite_transcriber.py`: IBM Granite Speech file transcription
- `app/live_transcription.py`: WebSocket live transcription and VAD chunking
- `app/llm.py`: optional transcript analysis through LiteLLM
- `static/`: browser application and vendored Markdown/sanitizer assets

## Optional ASR dependencies

The Compose build enables `requirements-granite.txt`, which contains the shared Transformers dependencies for both Granite and Parakeet. For a local Python runtime, install it alongside the base requirements:

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-granite.txt
```

Whisper is the default backend and the only backend used for live transcription.

## Tests

The API test environment uses lightweight stubs, so it does not need a GPU, model downloads, or API credentials.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-test.txt ruff
pytest -q tests
ruff check .
python -m compileall -q app tests
docker compose config -q
```

## Documentation

- [Project README](../README.md)
- [API reference](docs/API.md)
- [Deployment guide](../docs/DEPLOYMENT.md)
- [Contributing guide](../CONTRIBUTING.md)
- [Security policy](../SECURITY.md)
