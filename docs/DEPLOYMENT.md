# Transcriber Service Deployment

This repo contains the GPU transcription service in `transcriber/`.

## What changed recently

- LLM model list updated to current OpenRouter model IDs:
  - `anthropic/claude-opus-4.8`
  - `anthropic/claude-opus-4.8-fast`
  - `google/gemini-3.5-flash`
  - `google/gemini-3.1-pro-preview`
  - `google/gemini-3.1-flash-lite`
  - `qwen/qwen3.7-max`
  - `minimax/minimax-m3`
  - `moonshotai/kimi-k2.6`
  - `openai/gpt-5.5-pro`
  - `openai/gpt-5.5`
- Old model entries removed:
  - `anthropic/claude-opus-4.7`
  - `anthropic/claude-opus-4.6`
  - `google/gemini-3-pro-preview`
  - `google/gemini-3-flash-preview`
  - `google/gemini-2.5-flash`
  - `openai/gpt-5.4`
  - `openai/gpt-5.2`
  - `moonshotai/kimi-k2-thinking`
- Upload UX improved:
  - file size is shown before upload
  - direct upload progress is shown
  - processing and transcription are shown as separate stages
  - large files use chunked upload to avoid the Cloudflare 100 MB request cap
- Direct uploads are intentionally capped below the Cloudflare limit


## Private deployment configuration

The repository intentionally does not contain machine-specific deployment details.
Keep real hosts, usernames, runner labels, and filesystem paths in GitHub repository variables or in a local env file outside the repo.

### UI host compatibility

The browser UI emits an exact same-host WebSocket policy. Access it through a
DNS hostname or IPv4 address. Direct bracketed-IPv6 `Host` authorities are
rejected with HTTP 400 because browser support for IPv6 host sources in Content
Security Policy is inconsistent. IPv6 deployments should expose the service
through a DNS hostname at the reverse proxy.

### GitHub Actions deployment

The deployment workflow runs only after the `CI` workflow succeeds for a push
to `main`. It deploys that exact verified commit on an isolated self-hosted
runner; pull-request code never runs on the deployment runner.

Configure:

- repository variable `DEPLOY_RUNNER_LABELS` — a JSON array containing the
  dedicated runner labels, for example `["self-hosted", "deployment-runner"]`
- repository secret `DEPLOY_PATH` — the absolute checkout path on the deployment
  host
- GitHub environment `production` — add required reviewers and restrict the
  deployment branch to `main`

Do not use a general-purpose self-hosted runner label. Keep the deployment runner
isolated from other repositories and do not grant it organization-wide access.
`DEPLOY_PATH` may instead be a repository variable for non-sensitive demo
installations.

### Manual deployment scripts

The scripts require deployment settings from the environment:

```bash
export DEPLOY_HOST=deploy-host
export DEPLOY_USER=deploy
export DEPLOY_PATH=/srv/transcribalize
./scripts/deploy.sh
```

For rollback:

```bash
export DEPLOY_HOST=deploy-host
export DEPLOY_USER=deploy
export DEPLOY_PATH=/srv/transcribalize
./scripts/rollback.sh
```

Use `scripts/deploy.env.example` as a template, but keep the real env file outside the public repo.

## Local development

From the repo root:

```bash
cp .env.example .env
cd transcriber
docker compose up --build
```

Compose reads the repository-root `.env`. Keep real credentials only in that
ignored local file or in your deployment secret manager.

The service is exposed on `http://localhost:8000`.

## How to update the service

### 1. Pull the latest code

```bash
cd /path/to/transcribalize
git pull
```

### 2. Run automated tests

```bash
cd transcriber
python3 -m venv .venv-test
source .venv-test/bin/activate
pip install -U pip
pip install -r requirements-test.txt
pytest -q tests
```

### 3. Rebuild and restart the service

```bash
cd transcriber
docker compose up -d --build
```

### 4. Check logs

```bash
docker compose logs -f --tail=200
```

### 5. Verify health

```bash
curl -fsS http://localhost:8000/health
```

Expected response:

```json
{"status":"ok"}
```

## Recommended manual verification after deploy

### Health

```bash
curl -fsS http://localhost:8000/health
```

### Models list

```bash
curl -fsS http://localhost:8000/models
```

Confirm the response includes:

- `anthropic/claude-opus-4.8`
- `google/gemini-3.5-flash`
- `qwen/qwen3.7-max`
- `minimax/minimax-m3`
- `moonshotai/kimi-k2.6`
- `openai/gpt-5.5`

and no longer includes:

- `anthropic/claude-opus-4.7`
- `anthropic/claude-opus-4.6`
- `openai/gpt-5.4`
- `openai/gpt-5.2`
- `moonshotai/kimi-k2-thinking`

### Upload config

```bash
curl -fsS http://localhost:8000/upload/config
```

This should expose:

- Cloudflare cap: `100 MB`
- safe direct upload cap
- chunk upload size

## Operational notes

### Cloudflare upload cap

Cloudflare rejects oversized single-request uploads before the app can return a useful error. To avoid that:

- direct uploads are limited below the proxy cap
- larger files are uploaded in chunks via:
  - `POST /upload/init`
  - `POST /upload/chunk`
  - `POST /upload/complete`
  - then `POST /analyze/stream` with `upload_id`

### Where to edit model availability

Edit:

- `transcriber/app/llm.py`

Key fields:

- `DEFAULT_MODEL`
- `ALLOWED_MODELS`

### Where to edit upload limits

Edit:

- `transcriber/app/main.py`

Key constants:

- `CLOUDFLARE_UPLOAD_CAP_BYTES`
- `MAX_DIRECT_UPLOAD_BYTES`
- `CHUNK_UPLOAD_SIZE_BYTES`

### Frontend upload/transcription UX

Edit:

- `transcriber/static/index.html`

That file handles:

- selected file metadata display
- upload progress
- chunked upload flow
- transcription stage rendering
- error labeling by stage
