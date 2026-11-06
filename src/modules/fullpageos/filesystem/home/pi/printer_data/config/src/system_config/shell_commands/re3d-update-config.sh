#!/usr/bin/env bash

UPDATE_BRANCH_FILE="/etc/re3d-os-update/branch"
UPDATE_REPO_URL="https://github.com/re3Dprinting/re3D-Klipper-OS.git"

valid_update_branch() {
  case "$1" in
    ""|-*|HEAD) return 1 ;;
  esac
  git check-ref-format "refs/heads/$1" >/dev/null 2>&1
}

load_update_branch() {
  if [ "$#" -gt 0 ]; then
    BRANCH="$1"
  elif [ -e "${UPDATE_BRANCH_FILE}" ]; then
    BRANCH="$(cat "${UPDATE_BRANCH_FILE}")" || {
      echo "ERROR: Unable to read saved update branch." >&2
      return 1
    }
  else
    BRANCH="devel"
  fi
  if ! valid_update_branch "${BRANCH}"; then
    echo "ERROR: Invalid update branch: ${BRANCH}" >&2
    return 1
  fi
}
