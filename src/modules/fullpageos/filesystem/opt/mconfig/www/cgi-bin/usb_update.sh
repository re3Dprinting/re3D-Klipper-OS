#!/usr/bin/env bash
set -euo pipefail
. "$(dirname "$0")/_cgi_common.sh"
require_network

reply() {
  printf 'Status: %s\r\nContent-Type: %s\r\nCache-Control: no-store\r\n\r\n%s\n' "$1" "$2" "$3"
}

case "${REQUEST_METHOD:-}" in
  GET) ACTION=inspect ;;
  POST) ACTION=start ;;
  *)
    reply "405 Method Not Allowed" "text/plain; charset=utf-8" "Expected GET or POST."
    exit 1
    ;;
esac

if OUTPUT="$(python3 /opt/mconfig/offline_update.py "${ACTION}" 2>&1)"; then
  if [ "${ACTION}" = inspect ]; then
    reply "200 OK" "application/json" "${OUTPUT}"
  else
    reply "202 Accepted" "text/plain; charset=utf-8" "${OUTPUT}"
  fi
else
  reply "422 Unprocessable Entity" "text/plain; charset=utf-8" "${OUTPUT}"
  exit 1
fi
