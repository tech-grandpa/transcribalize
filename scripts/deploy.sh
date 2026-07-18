#!/usr/bin/env bash
set -euo pipefail

# Required environment:
#   DEPLOY_HOST - SSH host or IP for the deployment target
#   DEPLOY_USER - SSH user for the deployment target
#   DEPLOY_PATH - repository path on the deployment target
# Optional environment:
#   COMPOSE_DIR  - docker compose directory; defaults to $DEPLOY_PATH/transcriber
#   HEALTH_URL   - health endpoint checked on the deployment target

: "${DEPLOY_HOST:?Set DEPLOY_HOST to the deployment target host}"
: "${DEPLOY_USER:?Set DEPLOY_USER to the deployment SSH user}"
: "${DEPLOY_PATH:?Set DEPLOY_PATH to the repository path on the deployment target}"

COMPOSE_DIR="${COMPOSE_DIR:-${DEPLOY_PATH}/transcriber}"
MAX_RETRIES="${MAX_RETRIES:-30}"
RETRY_DELAY="${RETRY_DELAY:-10}"
HEALTH_URL="${HEALTH_URL:-http://localhost:8000/health}"

SSH_TARGET="${DEPLOY_USER}@${DEPLOY_HOST}"

ssh_target() {
  ssh "$SSH_TARGET" "$@"
}

echo "🚀 Deploying Transcribalize to configured deployment target"

# Pull latest code
echo "📥 Pulling latest code..."
ssh_target "cd '${DEPLOY_PATH}' && git fetch origin main && git checkout main && git reset --hard origin/main"

# Tag current image as rollback
echo "🏷️  Tagging current image as rollback..."
ssh_target "cd '${COMPOSE_DIR}' && \
  IMAGE=\$(docker compose images -q transcriber 2>/dev/null | head -1) && \
  if [ -n \"\$IMAGE\" ]; then \
    docker tag \$IMAGE transcriber-rollback:latest; \
    echo 'Tagged rollback image'; \
  else \
    echo 'No existing image to tag as rollback'; \
  fi"

# Build new image
echo "🔨 Building new Docker image..."
ssh_target "cd '${COMPOSE_DIR}' && docker compose build"

# Deploy
echo "🚢 Starting new container..."
ssh_target "cd '${COMPOSE_DIR}' && docker compose up -d"

# Health check
echo "🏥 Running health checks (max ${MAX_RETRIES} attempts, ${RETRY_DELAY}s apart)..."
for i in $(seq 1 "$MAX_RETRIES"); do
  if ssh_target "curl -sf '${HEALTH_URL}'" > /dev/null 2>&1; then
    echo "✅ Health check passed (attempt $i)"
    # Clean up old images
    echo "🧹 Cleaning up dangling images..."
    ssh_target "docker image prune -f" || true
    echo "🎉 Deployment successful!"
    exit 0
  fi
  echo "⏳ Attempt $i/$MAX_RETRIES — retrying in ${RETRY_DELAY}s..."
  sleep "$RETRY_DELAY"
done

# Rollback
echo "❌ Health check failed after ${MAX_RETRIES} attempts. Rolling back..."
ssh_target "cd '${COMPOSE_DIR}' && \
  docker compose down && \
  CURRENT_IMAGE=\$(docker compose config --images | head -1) && \
  ROLLBACK=\$(docker images -q transcriber-rollback:latest 2>/dev/null) && \
  if [ -n \"\$ROLLBACK\" ]; then \
    docker tag transcriber-rollback:latest \$CURRENT_IMAGE && \
    docker compose up -d && \
    echo '⏪ Rolled back to previous version'; \
  else \
    echo '⚠️  No rollback image available!'; \
  fi"
exit 1
