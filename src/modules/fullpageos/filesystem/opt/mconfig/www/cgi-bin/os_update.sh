#!/bin/bash
echo "Content-Type: text/plain"
echo

# Run as root (configure sudoers if invoked as www-data)
# Default branch (devel); change or add logic for main/stable if you want
LOG=$(/usr/local/bin/re3d-os-update 2>&1)
echo "$LOG"
