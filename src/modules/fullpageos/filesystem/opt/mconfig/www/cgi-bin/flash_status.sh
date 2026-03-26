#!/bin/bash
# Serve /tmp/flash_status.json over HTTP for remote clients
FILE="/tmp/flash_status.json"
echo "Content-Type: application/json"
echo

if [ -f "$FILE" ]; then
  cat "$FILE"
else
  echo '{"state":"idle","progress":0,"message":"No flash in progress."}'
fi
