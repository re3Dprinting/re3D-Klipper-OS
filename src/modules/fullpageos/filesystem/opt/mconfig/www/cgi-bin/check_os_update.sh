#!/usr/bin/env bash
echo "Content-Type: text/plain"
echo

BRANCH="devel"
REPO_DIR="/opt/re3d-os-src"
REPO_URL="https://github.com/re3Dprinting/re3D-Klipper-OS.git"
KLIPPER_DIR="/home/pi/klipper"

# Allow git to operate on pi-owned repos when running as root
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0=safe.directory
export GIT_CONFIG_VALUE_0="${KLIPPER_DIR}"

# ---- Check OS / Configurator repo ----
OS_STATUS="UP_TO_DATE"

# Make sure repo exists (same logic as bootstrap, but lighter)
if [ ! -d "${REPO_DIR}/.git" ]; then
  OS_STATUS="UPDATE_AVAILABLE"
else
  cd "${REPO_DIR}" || { echo "ERROR could not cd into repo"; exit 0; }

  # Fetch in the background of this check (non-destructive)
  git fetch origin >/dev/null 2>&1 || {
    echo "ERROR git fetch failed"
    exit 0
  }

  LOCAL=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
  REMOTE=$(git rev-parse "origin/${BRANCH}" 2>/dev/null || echo "unknown")

  if [ "$LOCAL" != "$REMOTE" ]; then
    OS_STATUS="UPDATE_AVAILABLE"
  fi
fi

# ---- Check Klipper repo ----
KLIPPER_UPDATE="no"
if [ -d "${KLIPPER_DIR}/.git" ]; then
  cd "${KLIPPER_DIR}" 2>/dev/null && {
    git fetch origin >/dev/null 2>&1 || true
    K_LOCAL=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
    K_REMOTE=$(git rev-parse '@{u}' 2>/dev/null || git rev-parse origin/master 2>/dev/null || echo "unknown")
    if [ "${K_LOCAL}" != "${K_REMOTE}" ]; then
      KLIPPER_UPDATE="yes"
    fi
  }
fi

# ---- Output ----
if [ "${OS_STATUS}" = "UP_TO_DATE" ] && [ "${KLIPPER_UPDATE}" = "no" ]; then
  echo "UP_TO_DATE branch=${BRANCH}"
else
  echo "UPDATE_AVAILABLE branch=${BRANCH}"
  if [ "${OS_STATUS}" = "UPDATE_AVAILABLE" ]; then
    echo "  OS/Configurator: update available"
  fi
  if [ "${KLIPPER_UPDATE}" = "yes" ]; then
    echo "  Klipper: update available"
    echo "  KLIPPER_UPDATE=yes"
  fi
fi
