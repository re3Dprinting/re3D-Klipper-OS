#!/bin/bash
# Serve /var/log/flash_once.log over HTTP for remote clients
FILE="/var/log/flash_once.log"
echo "Content-Type: text/plain"
echo

if [ -f "$FILE" ]; then
  cat "$FILE"
else
  echo "(no flash log available)"
fi
