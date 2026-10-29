#!/usr/bin/env bash
set -euo pipefail

install -d -m 0700 /etc/re3d-auth
if [[ ! -e /etc/re3d-auth/tls.key && ! -e /etc/re3d-auth/tls.crt ]]; then
  HOST="$(hostname)"
  SAN="DNS:${HOST},DNS:${HOST}.local,DNS:localhost,IP:127.0.0.1"
  for address in $(hostname -I); do
    SAN="${SAN},IP:${address}"
  done
  umask 077
  openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 825 \
    -keyout /etc/re3d-auth/tls.key -out /etc/re3d-auth/tls.crt \
    -subj "/CN=${HOST}.local" -addext "subjectAltName=${SAN}"
elif [[ ! -s /etc/re3d-auth/tls.key || ! -s /etc/re3d-auth/tls.crt ]]; then
  echo "Incomplete TLS key/certificate pair in /etc/re3d-auth; refusing to replace it." >&2
  exit 1
fi
