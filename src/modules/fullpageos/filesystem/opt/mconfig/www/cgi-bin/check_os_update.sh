#!/usr/bin/env bash
echo "Content-Type: text/plain"
echo

BRANCH="devel"
REPO_DIR="/opt/re3d-os-src"
REPO_URL="https://github.com/re3Dprinting/re3D-Klipper-OS.git"

# Make sure repo exists (same logic as bootstrap, but lighter)
if [ ! -d "${REPO_DIR}/.git" ]; then
  echo "UPDATE_AVAILABLE (no local clone, will clone on first update run)"
  exit 0
fi

cd "${REPO_DIR}" || { echo "ERROR could not cd into repo"; exit 0; }

# Fetch in the background of this check (non-destructive)
git fetch origin >/dev/null 2>&1 || {
  echo "ERROR git fetch failed"
  exit 0
}

LOCAL=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
REMOTE=$(git rev-parse "origin/${BRANCH}" 2>/dev/null || echo "unknown")

if [ "$LOCAL" = "$REMOTE" ]; then
  echo "UP_TO_DATE branch=${BRANCH} local=${LOCAL}"
else
  echo "UPDATE_AVAILABLE branch=${BRANCH}"
  echo "  local:  ${LOCAL}"
  echo "  remote: ${REMOTE}"
fi
