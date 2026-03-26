#!/bin/bash
# Serve /tmp/flash_devices.json over HTTP for remote clients
FILE="/tmp/flash_devices.json"
echo "Content-Type: application/json"
echo

if [ -f "$FILE" ]; then
  cat "$FILE"
else
  echo '[]'
fi
