#!/bin/sh
# /cgi-bin/check_update_connectivity.sh
# Truthfully answers: "Can this machine reach the update repo using git?"

echo "Content-Type: application/json"
echo ""

# Same repo your update script pulls from:
REPO_URL="${REPO_URL:-https://github.com/re3Dprinting/re3D-Klipper-OS.git}"

# Prefer to check using git, since updates use git.
if command -v git >/dev/null 2>&1; then
  # Avoid any interactive prompts
  export GIT_TERMINAL_PROMPT=0

  # Use timeout if available so we don't hang forever
  if command -v timeout >/dev/null 2>&1; then
    timeout 8 git ls-remote --heads "$REPO_URL" >/dev/null 2>&1
    rc=$?
  else
    git ls-remote --heads "$REPO_URL" >/dev/null 2>&1
    rc=$?
  fi

  if [ "$rc" -eq 0 ]; then
    # Repo reachable → user can actually get updates
    echo '{"can_update":true}'
    exit 0
  else
    # Git couldn’t see the remote; treat as “no internet for updates”
    echo '{"can_update":false}'
    exit 0
  fi
fi

# Fallback if git isn't available for some reason: use curl as a best effort.
TARGET="${UPDATE_TARGET_URL:-$REPO_URL}"
if command -v curl >/dev/null 2>&1; then
  if curl -fsS --max-time 5 -I "$TARGET" >/dev/null 2>&1; then
    echo '{"can_update":true}'
  else
    echo '{"can_update":false}'
  fi
else
  # No git and no curl: safest is to say we *can’t* update.
  echo '{"can_update":false}'
fi
