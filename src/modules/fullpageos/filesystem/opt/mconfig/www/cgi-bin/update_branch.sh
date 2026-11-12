#!/usr/bin/env bash
set -euo pipefail
. "$(dirname "$0")/_cgi_common.sh"
. /usr/local/bin/re3d-update-config.sh
require_network

reply() {
  printf 'Status: %s\r\nContent-Type: text/plain; charset=utf-8\r\nCache-Control: no-store\r\n\r\n%s\n' "$1" "$2"
}

case "${REQUEST_METHOD:-}" in
  GET)
    if ! MESSAGE="$(load_update_branch 2>&1 && printf '%s' "${BRANCH}")"; then
      reply "500 Internal Server Error" "${MESSAGE}"
      exit 1
    fi
    reply "200 OK" "${MESSAGE}"
    exit 0
    ;;
  POST) ;;
  *)
    reply "405 Method Not Allowed" "Expected GET or POST."
    exit 1
    ;;
esac

if [[ ! "${CONTENT_LENGTH:-}" =~ ^[0-9]{1,3}$ ]] || (( 10#${CONTENT_LENGTH} < 1 || 10#${CONTENT_LENGTH} > 255 )); then
  reply "400 Bad Request" "Branch must be between 1 and 255 bytes."
  exit 1
fi
BRANCH=""
if ! IFS= LC_ALL=C read -r -N "${CONTENT_LENGTH}" BRANCH || ! valid_update_branch "${BRANCH}"; then
  reply "400 Bad Request" "Invalid branch name."
  exit 1
fi

rc=0
GIT_TERMINAL_PROMPT=0 timeout 20 git ls-remote --exit-code --heads "${UPDATE_REPO_URL}" "refs/heads/${BRANCH}" >/dev/null 2>&1 || rc=$?
if [ "$rc" -eq 2 ]; then
  reply "422 Unprocessable Entity" "Branch not found in the re3D-Klipper-OS repository."
  exit 1
elif [ "$rc" -ne 0 ]; then
  reply "502 Bad Gateway" "Unable to verify branch with GitHub. Saved branch was not changed."
  exit 1
fi

if ! install -d -m 0755 "$(dirname "${UPDATE_BRANCH_FILE}")"; then
  reply "500 Internal Server Error" "Unable to create update settings directory."
  exit 1
fi
if ! TEMP_FILE="$(mktemp "${UPDATE_BRANCH_FILE}.XXXXXX")"; then
  reply "500 Internal Server Error" "Unable to create update settings file."
  exit 1
fi
trap 'rm -f -- "${TEMP_FILE}"' EXIT
if ! { printf '%s\n' "${BRANCH}" > "${TEMP_FILE}" &&
       chmod 0644 "${TEMP_FILE}" &&
       mv -f -- "${TEMP_FILE}" "${UPDATE_BRANCH_FILE}"; }; then
  reply "500 Internal Server Error" "Unable to save update branch."
  exit 1
fi
reply "200 OK" "${BRANCH}"
