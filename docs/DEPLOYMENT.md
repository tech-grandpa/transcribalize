# Deploying Transcribalize

Transcribalize runs as one GPU-backed FastAPI service from `transcriber/`. The
supported container deployment uses Docker Compose, NVIDIA Container Toolkit,
and a persistent volume for downloaded models.

Start with the root [README](../README.md) for hardware requirements and local
setup. This guide covers deployment configuration, updates, rollback, and
operational checks.

## Published image deployment

The recommended deployment path does not require a repository checkout. Pull
the versioned public image and run it with NVIDIA Container Toolkit:

```bash
docker run -d \
  --name transcribalize \
  --restart unless-stopped \
  --gpus all \
  -p 8000:8000 \
  -v transcribalize-models:/models \
  ghcr.io/tech-grandpa/transcribalize:0.1.0
```

Add `--env-file /path/to/transcribalize.env` before the image name when LLM
analysis or non-default runtime settings are needed. Keep that file outside any
source checkout and restrict its permissions.

To update within the `0.1` release line:

```bash
docker pull ghcr.io/tech-grandpa/transcribalize:0.1
docker rm -f transcribalize
# Re-run the docker run command above with the 0.1 image tag.
```

Use the full version tag or registry digest when reproducibility is more
important than following patch releases automatically.


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

The deployment job is opt-in and skips unless `DEPLOY_RUNNER_LABELS` is set.
When the workflow is enabled and configured, it runs only after the `CI`
workflow succeeds for a push to `main`. It deploys that exact verified commit
on an isolated self-hosted runner; pull-request code never runs on the
deployment runner.

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
# Optional: needed for LLM analysis or non-default ASR settings.
cp .env.example .env
cd transcriber
docker compose up --build
```

Compose reads the repository-root `.env`. Keep real credentials only in that
ignored local file or in your deployment secret manager.

The service is exposed on `http://localhost:8000`.

## WebSocket fragment limit

Images built from this source select
`app.websocket_protocol:BoundedWebSocketsProtocol` explicitly. It extends
Uvicorn's non-deprecated `websockets-sansio` implementation and allows at most
4,096 data frames per message, including the initial frame and the final
continuation. Ping, pong, and close frames do not count. The next data fragment
normally closes the connection with code 1009 and releases the accumulated
fragments, even if the sender never finishes the message. If that fragment and
a protocol error or peer close arrive in the same TCP read, the SansIO parser
may select code 1002 or the peer's close code before Uvicorn dispatches the
fragment. Those paths still close the connection, release retained fragments,
and deliver no oversized message to ASGI. Normal message-size limits and
compression still apply.

For a direct Python deployment, run from `transcriber/` with:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 \
  --ws app.websocket_protocol:BoundedWebSocketsProtocol
```

Do not replace this with bare `uvicorn app.main:app`, `--ws auto`, or
`--ws websockets-sansio`. Those paths bypass this adapter. Existing published
images are unchanged; rebuild and restart to pick up this mitigation. Compose
inherits the Dockerfile command, but custom commands must retain `--ws` above.

Uvicorn 0.54.0 has no fragment-count setting. `--ws-max-size` only limits bytes,
so empty fragments bypass it. `--ws-max-queue` is a completed-message queue
setting for the legacy protocol, not a SansIO fragment limit. ASGI handlers see
only complete messages and cannot enforce this bound. The adapter uses Uvicorn's
custom protocol selection rather than monkey-patching or switching to the
deprecated legacy WebSocket implementation.

The overridden handlers and `frames` list are Uvicorn internals, not a stable
extension API. Production and test requirements pin Uvicorn exactly to 0.54.0.
Every Uvicorn update, including a patch release, must be an explicit dependency
change that inspects the SansIO handlers and runs `pytest -q transcriber/tests`
from the repository root before the pin moves. Replace the adapter with an
upstream fragment-count setting when one becomes available. The wire tests read
the protocol selection from the Dockerfile so accidentally removing the flag
fails CI.

This bounds retained per-message fragments across TCP reads, not all server
memory or CPU use. Keep TLS, authentication, connection limits, and rate limits
at the deployment boundary. Rolling back to an older image also removes this
mitigation; restrict WebSocket access during rollback. The pre-existing `stop`
response still ends without an explicit close frame, observed as code 1006.
This change does not alter that application behavior.

References: [Uvicorn settings](https://uvicorn.dev/settings/) and
[Uvicorn 0.54.0 SansIO implementation](https://github.com/Kludex/uvicorn/blob/0.54.0/uvicorn/protocols/websockets/websockets_sansio_impl.py).

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

Confirm that the configured `DEFAULT_MODEL` appears in the response. The
runtime allowlist is defined in `transcriber/app/llm.py`; clients should discover
it through this endpoint rather than relying on a copied model list.

### Upload config

```bash
curl -fsS http://localhost:8000/upload/config
```

The default response exposes a 100 MiB proxy cap, a 95 MiB direct-upload cap,
and an 8 MiB chunk size. Confirm that these match the reverse proxy and storage
limits for the deployment.

## Operational notes

### Cloudflare upload cap

Cloudflare rejects oversized single-request uploads before the app can return a useful error. To avoid that:

- direct uploads are limited below the proxy cap
- larger files are uploaded in chunks via:
  - `POST /upload/init`
  - `POST /upload/chunk`
  - `POST /upload/complete`
  - then `POST /analyze/stream` with `upload_id`

The supplied Compose service mounts a 4 GiB `tmpfs` at `/tmp`. Chunk parts, the assembled media file, and extracted WAV data can coexist there, so practical large-file capacity is lower than 4 GiB. The application does not expire abandoned upload sessions automatically; shared deployments should add temporary-storage monitoring and cleanup.

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
