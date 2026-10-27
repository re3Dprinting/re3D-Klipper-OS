#!/bin/bash
# Schedule a reboot after returning the CGI response.
set -euo pipefail

if ! output="$(sudo /bin/systemd-run --quiet --on-active=3s /bin/systemctl reboot 2>&1)"; then
  printf 'Status: 500 Internal Server Error\r\nContent-Type: text/plain\r\n\r\nUnable to schedule reboot: %s\n' "$output"
  exit 1
fi

printf 'Content-Type: text/plain\r\n\r\nReboot scheduled.\n'