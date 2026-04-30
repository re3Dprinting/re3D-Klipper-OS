#!/bin/bash
# /opt/mconfig/www/cgi-bin/recovery_state.sh
# CGI: Read, write, or clear the persistent power-loss recovery state file.
#
# GET (no params)    — return state JSON, or {"exists":false} if no file
# GET ?action=clear  — delete the state file, return {"cleared":true}
# POST               — write JSON body to state file, return {"saved":true}
#
# State file: /home/pi/printer_data/recover_state.json

echo "Content-Type: application/json"
echo ""

STATE_FILE="/home/pi/printer_data/recover_state.json"
METHOD="${REQUEST_METHOD:-GET}"

# ── Parse action from query string ──────────────────────────────────────────
ACTION=""
IFS='&' read -ra KVS <<< "${QUERY_STRING:-}"
for kv in "${KVS[@]}"; do
  k="${kv%%=*}"; v="${kv#*=}"
  case "$k" in action) ACTION="$v" ;; esac
done

# ── Clear ────────────────────────────────────────────────────────────────────
if [[ "$ACTION" == "clear" ]]; then
  rm -f "$STATE_FILE"
  printf '{"cleared":true}\n'
  exit 0
fi

# ── Write (POST) ─────────────────────────────────────────────────────────────
if [[ "$METHOD" == "POST" ]]; then
  BODY=""
  CL="${CONTENT_LENGTH:-0}"
  if [[ "$CL" -gt 0 && "$CL" -le 4096 ]]; then
    BODY="$(dd bs=1 count="$CL" 2>/dev/null)"
  else
    read -r -t 2 BODY || true
  fi

  # Must be a JSON object
  if [[ "${BODY:0:1}" != "{" ]]; then
    printf '{"error":"Invalid body"}\n'
    exit 0
  fi

  # Reject any path traversal attempts within the JSON values
  if printf '%s' "$BODY" | grep -qE '\.\./|\.\.\\'; then
    printf '{"error":"Invalid content"}\n'
    exit 0
  fi

  mkdir -p "$(dirname "$STATE_FILE")"
  printf '%s\n' "$BODY" > "$STATE_FILE"
  printf '{"saved":true}\n'
  exit 0
fi

# ── Read (GET) ───────────────────────────────────────────────────────────────
if [[ -f "$STATE_FILE" ]]; then
  cat "$STATE_FILE"
else
  printf '{"exists":false}\n'
fi
