#!/usr/bin/env bash
# Drive the Code Explorer API for verification. Run from anywhere: bash .claude/skills/verify/verify.sh <cmd>
#   up [--offline]   start Redis (docker, :6390) + API (:8765) [+ worker unless --offline]
#   doctor           read-only: is our instance up, ours, and wired to our Redis?
#   token            print a JWT the API accepts (HS256 path of api/middleware/auth.py)
#   call NAME METHOD PATH [JSON]   authenticated request; saves req/resp to the run's evidence dir
#   down             stop only what `up` started; evidence is kept
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PY="$REPO/.venv/Scripts/python.exe"; [ -x "$PY" ] || PY="$REPO/.venv/bin/python"
PORT="${VERIFY_PORT:-8765}"
REDIS_PORT="${VERIFY_REDIS_PORT:-6390}"
REDIS_CTR="ce-verify-redis"
STATE="$REPO/.verify-runs/state"          # pids + mode; deleted by `down`
RUNS="$REPO/.verify-runs"                 # evidence lives in $RUNS/<run-id>/, never deleted by `down`
BASE="http://127.0.0.1:$PORT"

offline_env() {
  # Dummy creds: SUPABASE_URL on a dead port forces the HS256 fallback in auth.py.
  export SUPABASE_URL="http://127.0.0.1:9" SUPABASE_ANON_KEY=x SUPABASE_SERVICE_ROLE_KEY=x \
    SUPABASE_JWT_SECRET="verify-offline-secret-0123456789abcdef" PINECONE_API_KEY=x OPENAI_API_KEY=x \
    DATABASE_URL="postgresql+asyncpg://x:x@127.0.0.1:9/x"
}

win_pid_on_port() { netstat -ano 2>/dev/null | awk -v p=":$1" '$2 ~ p"$" && $4=="LISTENING"{print $5; exit}'; }

run_dir() { cat "$STATE/run_id" 2>/dev/null || { echo "no active run; call 'up' first" >&2; exit 1; }; }

cmd_up() {
  local mode=live; [ "${1:-}" = "--offline" ] && mode=offline
  [ -d "$STATE" ] && { echo "state exists at $STATE: an instance may be running. Run 'doctor' or 'down' first." >&2; exit 1; }
  [ -n "$(win_pid_on_port "$PORT")" ] && { echo "port $PORT busy (not ours). Set VERIFY_PORT." >&2; exit 1; }
  [ "$mode" = live ] && [ ! -f "$REPO/.env" ] && { echo "live mode needs $REPO/.env (see .env.example) or use --offline" >&2; exit 1; }
  docker info >/dev/null 2>&1 || { echo "docker daemon not running (start Docker Desktop)" >&2; exit 1; }

  local id; id="$(date +%Y%m%d-%H%M%S)-$mode"
  mkdir -p "$STATE" "$RUNS/$id"; echo "$id" > "$STATE/run_id"; echo "$mode" > "$STATE/mode"

  docker run -d --rm --name "$REDIS_CTR" -p "127.0.0.1:$REDIS_PORT:6379" redis:7-alpine >/dev/null
  echo started > "$STATE/redis"
  export REDIS_URL="redis://127.0.0.1:$REDIS_PORT/0" LOG_FORMAT=console CLONE_BASE_PATH="$RUNS/$id/clones"
  local cwd="$REPO"
  if [ "$mode" = offline ]; then offline_env; cwd="$RUNS/$id"; fi   # cwd without .env => real creds never load

  (cd "$cwd" && nohup "$PY" -m uvicorn code_explorer.main:app --host 127.0.0.1 --port "$PORT" \
      > "$RUNS/$id/api.log" 2>&1 &)
  if [ "$mode" = live ]; then
    (cd "$cwd" && nohup "$PY" -m code_explorer.workers.worker > "$RUNS/$id/worker.log" 2>&1 &)
  fi

  for _ in $(seq 1 60); do
    curl -sf "$BASE/health" >/dev/null 2>&1 && break; sleep 1
  done
  curl -sf "$BASE/health" >/dev/null || { echo "API never answered /health; see $RUNS/$id/api.log" >&2; exit 1; }
  win_pid_on_port "$PORT" > "$STATE/api.pid"
  if [ "$mode" = live ]; then
    # worker pid: the python whose command line runs code_explorer.workers.worker
    powershell -NoProfile -Command "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ? { \$_.CommandLine -like '*code_explorer.workers.worker*' }).ProcessId" \
      > "$STATE/worker.pid" 2>/dev/null || true
  fi
  echo "ready: $BASE  mode=$mode  evidence=$RUNS/$id"
}

cmd_doctor() {
  local ok=1
  [ -d "$STATE" ] || { echo "no state: nothing started by verify.sh"; exit 1; }
  local pid owner; pid="$(cat "$STATE/api.pid" 2>/dev/null)"; owner="$(win_pid_on_port "$PORT")"
  [ -n "$pid" ] && [ "$pid" = "$owner" ] && echo "api: pid $pid owns :$PORT (ours)" || { echo "api: port :$PORT owner '$owner' != recorded '$pid'"; ok=0; }
  curl -sf "$BASE/health" && echo || { echo "api: /health not answering"; ok=0; }
  curl -s "$BASE/health/ready"; echo
  docker exec "$REDIS_CTR" redis-cli ping 2>/dev/null || { echo "redis: container $REDIS_CTR not answering"; ok=0; }
  echo "mode=$(cat "$STATE/mode")  run=$(cat "$STATE/run_id")"
  [ "$ok" = 1 ]
}

cmd_token() {
  local secret
  if [ "$(cat "$STATE/mode" 2>/dev/null)" = offline ]; then secret="verify-offline-secret-0123456789abcdef"
  else secret="$(grep -E '^SUPABASE_JWT_SECRET=' "$REPO/.env" | cut -d= -f2-)"; fi
  local sub="${VERIFY_USER_ID:-00000000-0000-4000-8000-000000000001}"
  "$PY" -c "import jwt,time,sys; print(jwt.encode({'sub':sys.argv[2],'aud':'authenticated','role':'authenticated','exp':int(time.time())+3600},sys.argv[1],algorithm='HS256'))" "$secret" "$sub"
}

cmd_call() {
  local name="$1" method="$2" path="$3" body="${4:-}" dir; dir="$RUNS/$(run_dir)"
  local tok; tok="$(cmd_token)"
  local args=(-s -X "$method" -H "Authorization: Bearer $tok" -D "$dir/$name.headers" -o "$dir/$name.body" -w '%{http_code}')
  [ -n "$body" ] && args+=(-H 'Content-Type: application/json' -d "$body")
  printf '%s %s\n%s\n' "$method" "$path" "$body" > "$dir/$name.request"
  local code; code="$(curl "${args[@]}" "$BASE$path")"
  echo "$code" > "$dir/$name.status"
  echo "HTTP $code"; cat "$dir/$name.body"; echo
}

cmd_down() {
  [ -d "$STATE" ] || { echo "nothing to stop"; return 0; }
  for f in api worker; do
    local pid; pid="$(cat "$STATE/$f.pid" 2>/dev/null || true)"
    [ -n "$pid" ] && taskkill //PID "$pid" //T //F >/dev/null 2>&1 && echo "stopped $f pid $pid" || true
  done
  [ -f "$STATE/redis" ] && docker rm -f "$REDIS_CTR" >/dev/null 2>&1 && echo "removed $REDIS_CTR"
  local id; id="$(cat "$STATE/run_id")"
  rm -rf "$STATE" "$RUNS/$id/clones"
  echo "evidence kept: $RUNS/$id"
}

case "${1:-}" in
  up) shift; cmd_up "$@";; doctor) cmd_doctor;; token) cmd_token;;
  call) shift; cmd_call "$@";; down) cmd_down;;
  *) sed -n 2,7p "$0"; exit 2;;
esac
