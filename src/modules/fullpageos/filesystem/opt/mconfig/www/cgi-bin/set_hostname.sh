#!/bin/bash
# /cgi-bin/set_hostname.sh  (make executable: chmod 755)
set -euo pipefail

# --- If not root, try to re-exec via sudo (for CGI); requires a NOPASSWD rule.
if [[ ${EUID:-$(id -u)} -ne 0 ]] && command -v sudo >/dev/null 2>&1; then
  exec sudo -n "$0" "$@"
fi

LOG_DIR="/home/pi"
LOG_FILE="$LOG_DIR/shell_log.txt"
TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S %z')"

# --- Mini URL decode (handles %xx and +)
urldecode() {
  local data="${1//+/ }"
  printf '%b' "${data//%/\\x}"
}

# --- Read hostname from: 1) $1 (CLI), 2) QUERY_STRING, 3) POST body
RAW_HOST="${1-}"

if [[ -z "${RAW_HOST}" && -n "${QUERY_STRING-}" ]]; then
  # hostname in query: /cgi-bin/set_hostname.sh?hostname=foo
  RAW_HOST="$(printf '%s' "$QUERY_STRING" | awk -F'hostname=' 'NF>1{print $2}' | awk -F'&' '{print $1}')"
fi

if [[ -z "${RAW_HOST}" && "${REQUEST_METHOD-}" == "POST" ]]; then
  # Read application/x-www-form-urlencoded body
  read -r -N "${CONTENT_LENGTH:-0}" POST_DATA || true
  RAW_HOST="$(printf '%s' "$POST_DATA" | awk -F'hostname=' 'NF>1{print $2}' | awk -F'&' '{print $1}')"
fi

RAW_HOST="$(urldecode "${RAW_HOST:-}")"

# --- Basic validation per RFC-952/1123 hostnames
#   - lower case alnum + hyphen
#   - 1..63 chars, cannot start/end with hyphen
NEW_HOST="$(printf '%s' "$RAW_HOST" | tr '[:upper:]' '[:lower:]')"

if [[ -z "$NEW_HOST" ]] || [[ ${#NEW_HOST} -gt 63 ]] || \
   [[ ! "$NEW_HOST" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]]; then
  # CGI header if under HTTP
  [[ -n "${REQUEST_METHOD-}" ]] && printf 'Content-Type: text/plain\r\n\r\n'
  echo "Error: invalid hostname. Use 1–63 chars: a–z, 0–9, hyphen; cannot start/end with '-'."
  exit 1
fi

OLD_HOST="$(hostname)"

# --- Apply hostname (static + pretty). Transient will follow after reboot.
hostnamectl set-hostname "$NEW_HOST"

# --- Ensure /etc/hosts has 127.0.1.1 mapping to the new hostname
if grep -qE '^[[:space:]]*127\.0\.1\.1[[:space:]]' /etc/hosts; then
  sed -i -E "s|^[[:space:]]*127\.0\.1\.1[[:space:]].*|127.0.1.1 $NEW_HOST|" /etc/hosts
else
  printf "\n127.0.1.1 %s\n" "$NEW_HOST" >> /etc/hosts
fi

# --- Log
mkdir -p "$LOG_DIR"
{
  echo "Timestamp: $TIMESTAMP"
  echo "Hostname changed: ${OLD_HOST} -> ${NEW_HOST}"
  echo
} >> "$LOG_FILE"

# --- CGI response (or plain CLI output)
if [[ -n "${REQUEST_METHOD-}" ]]; then
  printf 'Content-Type: text/plain\r\n\r\n'
fi

sudo rm -rf /home/pi/.config/chromium/Singleton*

echo "Hostname has been changed to: ${NEW_HOST}"
echo "Interface will now resolve at: ${NEW_HOST}.local (mDNS)"
echo "Reboot required for all services to pick up the change."
