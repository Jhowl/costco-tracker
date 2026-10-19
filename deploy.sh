#!/usr/bin/env bash
# Deploy Costco Tracker to the homeserver's docker folder.
#
#   HOST=192.168.1.10 ./deploy.sh              # user = your local username
#   HOST=192.168.1.10 SSH_USER=admin ./deploy.sh
#   APP_PORT=8123 ./deploy.sh                # force a port (saved in the server's .env)
#   OLLAMA_URL=http://host:11434 ./deploy.sh
#
# Re-run any time to ship updates. The database lives in ./data on the server and
# .env there is never overwritten (only APP_PORT / OLLAMA_URL when passed).
set -euo pipefail

HOST="${HOST:?set HOST to your server address, e.g. HOST=192.168.1.10 ./deploy.sh}"
SSH_USER="${SSH_USER:-$USER}"
REMOTE_DIR="${REMOTE_DIR:-docker/costco-tracker}"   # relative to the user's home
TARGET="$SSH_USER@$HOST"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "→ Copying project to $TARGET:~/$REMOTE_DIR"
ssh "$TARGET" "mkdir -p ~/$REMOTE_DIR/data"
rsync -az --delete --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r \
  --exclude .git --exclude .env --exclude data --exclude .venv --exclude __pycache__ --exclude .pytest_cache \
  "$HERE"/ "$TARGET:~/$REMOTE_DIR/"

echo "→ Building and starting"
PORT=$(ssh "$TARGET" REMOTE_DIR="$REMOTE_DIR" REQ_PORT="${APP_PORT:-}" OLLAMA_URL="${OLLAMA_URL:-}" 'bash -s' <<'REMOTE'
set -euo pipefail
cd ~/"$REMOTE_DIR"
if docker compose version >/dev/null 2>&1; then DC="docker compose"; else DC="docker-compose"; fi
[ -f .env ] || { cp .env.example .env; echo "  created .env" >&2; }
setenv() { if grep -qE "^$1=" .env; then sed -i "s#^$1=.*#$1=$2#" .env; else echo "$1=$2" >> .env; fi; }
setenv APP_UID "$(id -u)"; setenv APP_GID "$(id -g)"
[ -n "$OLLAMA_URL" ] && setenv OLLAMA_URL "$OLLAMA_URL"
port="${REQ_PORT:-$(grep -E '^APP_PORT=' .env | cut -d= -f2)}"; port="${port:-8096}"
ours=$(docker ps --filter "label=com.docker.compose.project=$(basename "$PWD")" --format '{{.Ports}}' | grep -o ":$port->" || true)
in_use() { timeout 1 bash -c "echo > /dev/tcp/127.0.0.1/$1" 2>/dev/null; }
if [ -z "$ours" ] && in_use "$port"; then
  [ -n "$REQ_PORT" ] && { echo "  port $port is in use" >&2; exit 1; }
  while in_use "$port"; do port=$((port + 1)); done
  echo "  port taken, using $port" >&2
fi
setenv APP_PORT "$port"
$DC up -d --build >&2
echo "$port"
REMOTE
)

echo "→ Waiting for the app"
for _ in $(seq 1 30); do
  if curl -fs "http://$HOST:$PORT/api/health" >/dev/null; then
    echo "✓ Costco Tracker is up: http://$HOST:$PORT"
    curl -s "http://$HOST:$PORT/api/health/llm"; echo
    exit 0
  fi
  sleep 2
done
echo "App did not answer on http://$HOST:$PORT — check: ssh $TARGET 'cd ~/$REMOTE_DIR && docker compose logs --tail 50'"
exit 1
