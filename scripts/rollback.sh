#!/usr/bin/env bash
set -euo pipefail

# Required environment:
#   DEPLOY_HOST - SSH host or IP for the deployment target
#   DEPLOY_USER - SSH user for the deployment target
#   DEPLOY_PATH - repository path on the deployment target
# Optional environment:
#   COMPOSE_DIR  - docker compose directory; defaults to $DEPLOY_PATH/transcriber

: "${DEPLOY_HOST:?Set DEPLOY_HOST to the deployment target host}"
: "${DEPLOY_USER:?Set DEPLOY_USER to the deployment SSH user}"
: "${DEPLOY_PATH:?Set DEPLOY_PATH to the repository path on the deployment target}"

COMPOSE_DIR="${COMPOSE_DIR:-${DEPLOY_PATH}/transcriber}"
SSH_TARGET="${DEPLOY_USER}@${DEPLOY_HOST}"

ssh_target() {
  ssh "$SSH_TARGET" "$@"
}

echo "⏪ Rolling back Transcribalize on configured deployment target"

ssh_target "cd '${COMPOSE_DIR}' && \
  ROLLBACK=\$(docker images -q transcriber-rollback:latest 2>/dev/null) && \
  if [ -z \"\$ROLLBACK\" ]; then \
    echo '❌ No rollback image found!'; \
    exit 1; \
  fi && \
  CURRENT_IMAGE=\$(docker compose config --images | head -1) && \
  docker compose down && \
  docker tag transcriber-rollback:latest \$CURRENT_IMAGE && \
  docker compose up -d && \
  echo '✅ Rolled back to previous version'"
