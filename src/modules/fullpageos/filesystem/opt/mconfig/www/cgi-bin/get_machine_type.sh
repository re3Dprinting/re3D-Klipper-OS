#!/bin/bash
# /opt/mconfig/www/cgi-bin/get_machine_type.sh
# CGI: reads /home/pi/printer_data/config/.master.cfg and returns the machine name.

set -euo pipefail

echo "Content-Type: text/plain"
echo

config_file="/home/pi/printer_data/config/.master.cfg"

if [[ ! -f "$config_file" ]]; then
  echo "Gigabot 4"
  exit 0
fi

# Determine section (fff or fgf)
section=""
if grep -q '^\[fff\]' "$config_file"; then
  section="fff"
elif grep -q '^\[fgf\]' "$config_file"; then
  section="fgf"
else
  echo "Gigabot 4"
  exit 0
fi

# Read platform_type
platform="$(awk -F= '/^platform_type=/{print $2; exit}' "$config_file" | tr -d '[:space:]')"

# Map section + platform back to machine name
case "${section}_${platform}" in
  fff_regular)  echo "Gigabot 4"       ;;
  fff_xlt)      echo "Gigabot 4 XLT"   ;;
  fff_terabot)  echo "Terabot 4"       ;;
  fgf_regular)  echo "GigabotX 2"      ;;
  fgf_xlt)     echo "GigabotX XLT 2"  ;;
  fgf_terabot)  echo "TerabotX 2"      ;;
  *)            echo "Gigabot 4"       ;;
esac
