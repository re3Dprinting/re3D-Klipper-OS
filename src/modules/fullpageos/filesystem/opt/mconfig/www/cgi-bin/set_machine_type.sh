#!/bin/bash
# /opt/mconfig/www/cgi-bin/set_machine_type.sh
# CGI wrapper: read POST body (type=<name>), URL-decode, call set_machine_type, emit text/plain.

set -euo pipefail

echo "Content-Type: text/plain"
echo

# Read POST body safely
BODY=""
if [[ "${REQUEST_METHOD:-}" == "POST" ]]; then
  read -r -N "${CONTENT_LENGTH:-0}" BODY || true
else
  echo "Expected POST." ; exit 1
fi

# Extract "type=" param (handles multiple fields)
raw_type="$(printf '%s' "$BODY" | tr '&' '\n' | awk -F= '$1=="type"{print $2; exit}')"

# URL-decode
urldecode() {
  local data="${1//+/ }"
  printf '%b' "${data//%/\\x}"
}
TYPE="$(urldecode "${raw_type:-}")"

if [[ -z "${TYPE}" ]]; then
  echo "Missing 'type' field." ; exit 1
fi

# Call the core setter (accepts names or numbers)
if out="$(/usr/local/bin/set_machine_type "$TYPE" 2>&1)"; then
  echo "$out"
else
  echo "$out"
  exit 1
fi
