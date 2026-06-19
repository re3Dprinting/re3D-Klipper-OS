#!/bin/bash
# /opt/mconfig/www/cgi-bin/set_machine_type.sh
# CGI wrapper: read POST body, URL-decode, call set_machine_type, then patch
# optional feature flags (crammer, heater_bed, mesh_compensation) if provided.

set -euo pipefail

echo "Content-Type: text/plain"
echo

CONFIG_FILE="/home/pi/printer_data/config/.master.cfg"

# Read POST body safely
BODY=""
if [[ "${REQUEST_METHOD:-}" == "POST" ]]; then
  read -r -N "${CONTENT_LENGTH:-0}" BODY || true
else
  echo "Expected POST." ; exit 1
fi

# URL-decode helper
urldecode() {
  local data="${1//+/ }"
  printf '%b' "${data//%/\\x}"
}

# Extract a named param from the POST body
get_param() {
  printf '%s' "$BODY" | tr '&' '\n' | awk -F= -v k="$1" '$1==k{print $2; exit}'
}

raw_type="$(get_param type)"
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

# Patch optional feature flags if they were submitted.
# The setter rewrites the config, so we sed-update after it runs.
patch_flag() {
  local key="$1" raw_val param_val
  raw_val="$(get_param "$key")"
  param_val="$(urldecode "${raw_val:-}")"
  if [[ "$param_val" == "true" ]]; then
    sed -i "s/^${key}_enabled=.*/${key}_enabled=true/" "$CONFIG_FILE"
  elif [[ "$param_val" == "false" ]]; then
    sed -i "s/^${key}_enabled=.*/${key}_enabled=false/" "$CONFIG_FILE"
  fi
  # If the param is absent, leave the default written by set_machine_type.
}

if [[ -f "$CONFIG_FILE" ]]; then
  patch_flag "crammer"
  patch_flag "heater_bed"
  patch_flag "mesh_compensation"
fi
