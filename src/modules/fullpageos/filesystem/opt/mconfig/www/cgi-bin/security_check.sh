#!/bin/bash
set -euo pipefail
printf 'Content-Type: application/json\r\nCache-Control: no-store\r\n\r\n'
exec /usr/bin/python3 /opt/mconfig/security_policy.py --check
