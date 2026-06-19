#!/bin/bash
# /opt/mconfig/www/cgi-bin/get_machine_options.sh
# CGI: reads /home/pi/printer_data/config/.master.cfg and returns the
# optional feature flags as JSON.

set -euo pipefail

echo "Content-Type: application/json"
echo

config_file="/home/pi/printer_data/config/.master.cfg"

if [[ ! -f "$config_file" ]]; then
  echo '{"crammer":false,"heater_bed":false,"mesh_compensation":false}'
  exit 0
fi

crammer="$(awk -F= '/^crammer_enabled=/{print $2; exit}' "$config_file" | tr -d '[:space:]')"
heater="$(awk -F= '/^heater_bed_enabled=/{print $2; exit}' "$config_file" | tr -d '[:space:]')"
mesh="$(awk -F= '/^mesh_compensation_enabled=/{print $2; exit}' "$config_file" | tr -d '[:space:]')"

[[ "$crammer" == "true" ]] || crammer="false"
[[ "$heater"  == "true" ]] || heater="false"
[[ "$mesh"    == "true" ]] || mesh="false"

printf '{"crammer":%s,"heater_bed":%s,"mesh_compensation":%s}\n' \
  "$crammer" "$heater" "$mesh"
