#!/usr/bin/env bash
# Pull Sprucer local secrets from Vaultwarden (via Nathan) into a .env file.
# Never prints secret values.
#
# Usage (personal fsb-01 checkout):
#   . /opt/fsb/config/agent/cli.env   # or load-lab-env.sh
#   bash scripts/sync-local-secrets-from-vault.sh
#   docker compose up -d
#
# Demo stack on fsb-03:
#   ENV_FILE=/opt/stacks/sprucer-demo/.env \
#   SPRUCER_SECRET_PROFILE=demo \
#   bash scripts/sync-local-secrets-from-vault.sh
#   cd /opt/stacks/sprucer-demo && docker compose up -d
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"
PROFILE="${SPRUCER_SECRET_PROFILE:-personal}"

if [[ -z "${NATHAN_API_KEY:-}" && -z "${NATHAN_KEY:-}" ]]; then
  if [[ -f /opt/fsb/data/repos/nathan/scripts/agent/load-lab-env.sh ]]; then
    # shellcheck disable=SC1091
    . /opt/fsb/data/repos/nathan/scripts/agent/load-lab-env.sh
  elif [[ -f /opt/fsb/config/agent/cli.env ]]; then
    # shellcheck disable=SC1091
    . /opt/fsb/config/agent/cli.env
  fi
fi
NATHAN_KEY="${NATHAN_KEY:-${NATHAN_API_KEY:-}}"
NATHAN_URL="${NATHAN_URL:-http://100.64.0.7:9102}"
if [[ -z "$NATHAN_KEY" ]]; then
  echo "NATHAN_API_KEY / NATHAN_KEY missing" >&2
  exit 1
fi

case "$PROFILE" in
  personal)
    DEV_HANDLE="lab/sprucer-dev-password"
    SESSION_HANDLE="lab/sprucer-session-secret"
    NEED_DEV=1
    ;;
  demo)
    DEV_HANDLE=""
    SESSION_HANDLE="lab/sprucer-demo-session-secret"
    NEED_DEV=0
    ;;
  *)
    echo "Unknown SPRUCER_SECRET_PROFILE=$PROFILE (personal|demo)" >&2
    exit 1
    ;;
esac

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE — copy .env.example first" >&2
  exit 1
fi

CFG=$(mktemp)
TMP=$(mktemp)
trap 'rm -f "$CFG" "$TMP"' EXIT
printf 'header = "Authorization: Bearer %s"\n' "$NATHAN_KEY" >"$CFG"

reveal() {
  local handle="$1"
  local out="$2"
  curl -fsS -m 120 -K "$CFG" -H 'Content-Type: application/json' \
    -d "{\"handle\":\"$handle\",\"confirm\":true}" \
    "$NATHAN_URL/v1/tools/secrets.reveal" \
    | python3 -c 'import sys,json; r=json.load(sys.stdin); p=(r.get("result") or {}).get("password") or "";
import pathlib; pathlib.Path(sys.argv[1]).write_text(p)' "$out"
  if [[ ! -s "$out" ]]; then
    echo "Reveal empty for $handle" >&2
    exit 1
  fi
  chmod 600 "$out"
}

upsert_env() {
  local key="$1"
  local val_file="$2"
  python3 - "$ENV_FILE" "$key" "$val_file" <<'PY'
import pathlib, sys
env_path = pathlib.Path(sys.argv[1])
key = sys.argv[2]
val = pathlib.Path(sys.argv[3]).read_text().rstrip("\n")
lines = env_path.read_text().splitlines()
out = []
found = False
for line in lines:
    if line.startswith(key + "=") or line.startswith(key + " ="):
        out.append(f"{key}={val}")
        found = True
    else:
        out.append(line)
if not found:
    out.append(f"{key}={val}")
env_path.write_text("\n".join(out) + "\n")
PY
}

echo "syncing profile=$PROFILE env=$ENV_FILE"

if [[ "$NEED_DEV" -eq 1 ]]; then
  reveal "$DEV_HANDLE" "$TMP"
  upsert_env "SPRUCER_DEV_PASSWORD" "$TMP"
  : >"$TMP"
  echo "updated SPRUCER_DEV_PASSWORD from $DEV_HANDLE"
fi

reveal "$SESSION_HANDLE" "$TMP"
upsert_env "SPRUCER_SESSION_SECRET" "$TMP"
: >"$TMP"
echo "updated SPRUCER_SESSION_SECRET from $SESSION_HANDLE"

# Length-only smoke (no values)
python3 - "$ENV_FILE" "$NEED_DEV" <<'PY'
from pathlib import Path
import sys
vals = {}
for line in Path(sys.argv[1]).read_text().splitlines():
    s = line.strip()
    if not s or s.startswith("#") or "=" not in s:
        continue
    k, v = s.split("=", 1)
    vals[k.strip()] = v.strip().strip('"').strip("'")
need_dev = sys.argv[2] == "1"
keys = ["SPRUCER_SESSION_SECRET"] + (["SPRUCER_DEV_PASSWORD"] if need_dev else [])
defaults = {
    "sprucer-dev",
    "change-me-dev-password",
    "change-me-long-random-session-secret",
}
for k in keys:
    v = vals.get(k)
    if v is None:
        print(f"{k}: MISSING")
    else:
        print(f"{k}: len={len(v)} looks_default={v in defaults}")
PY

echo "done. Recreate brain with a clean shell so Compose reads .env (not an exported SPRUCER_*):"
echo "  unset SPRUCER_DEV_PASSWORD SPRUCER_SESSION_SECRET"
echo "  docker compose up -d --force-recreate brain"
