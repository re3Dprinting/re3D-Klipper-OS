#!/usr/bin/env bash
# /cgi-bin/scan_wifi.sh
# Returns JSON array of visible WiFi networks via nmcli.
# Each entry: {"ssid":"...","signal":N,"security":"WPA2","freq":"2.4","in_use":bool}

printf 'Content-Type: application/json\r\n\r\n'

export PATH="/usr/sbin:/sbin:/usr/bin:/bin"

if ! command -v nmcli >/dev/null 2>&1; then
  printf '{"error":"nmcli not found","networks":[]}\n'
  exit 0
fi

# Trigger a fresh scan (best-effort; may fail if scan just ran)
nmcli device wifi rescan 2>/dev/null || true
# Give the radio a moment to complete the scan before listing
sleep 2

# Get the currently-connected SSID(s) reliably from active connections
# (wifi list's IN-USE can lag behind after a rescan)
# nmcli -t -f ... connection show --active gives profile names, not SSIDs.
# So we query each wifi interface's current SSID directly instead.
ACTIVE_SSIDS=""
for iface in $(nmcli -t -f DEVICE,TYPE device 2>/dev/null | awk -F: '$2=="wifi"{print $1}'); do
  ssid=$(iwgetid -r "$iface" 2>/dev/null || true)
  if [ -n "$ssid" ] && [ "$ssid" != "--" ]; then
    ACTIVE_SSIDS="${ACTIVE_SSIDS}${ssid}"$'\n'
  fi
done

# nmcli fields: IN-USE, SIGNAL, SECURITY, SSID, FREQ
# Using terse/fields mode for reliable parsing
# --terse uses : as delimiter, but SSIDs can contain colons—use \t instead
RAW=$(nmcli -t -f IN-USE,SIGNAL,SECURITY,SSID,FREQ device wifi list 2>/dev/null || true)

if [ -z "$RAW" ]; then
  printf '{"networks":[]}\n'
  exit 0
fi

# Build JSON array. Use awk for reliable parsing of the colon-delimited nmcli output.
# nmcli -t escapes literal colons in SSIDs as \: so we handle that.
printf '%s\n' "$RAW" | awk -F':' -v active="$ACTIVE_SSIDS" '
BEGIN { printf "[" ; first=1; split(active, a, "\n"); for (i in a) active_map[a[i]]=1 }
{
  # Field 1: IN-USE (* or empty) — may be unreliable right after rescan
  scan_in_use = ($1 == "*") ? 1 : 0

  # Field 2: SIGNAL (integer)
  signal = $2 + 0

  # Field 3: SECURITY
  security = $3

  # Fields 4..N-1 are SSID (may contain colons), last field is FREQ
  # Reconstruct: everything from field 4 to NF-1 is SSID, NF is FREQ
  ssid = ""
  for (i = 4; i <= NF-1; i++) {
    if (i > 4) ssid = ssid ":"
    ssid = ssid $i
  }
  freq_raw = $NF

  # Skip hidden/empty SSIDs
  if (ssid == "" || ssid == "--") next

  # Use active connection list as authoritative source; fall back to scan flag
  in_use = (ssid in active_map || scan_in_use) ? "true" : "false"

  # Determine band from frequency (MHz)
  freq_mhz = freq_raw + 0
  band = "?"
  if (freq_mhz >= 2400 && freq_mhz <= 2500) band = "2.4"
  else if (freq_mhz >= 5000 && freq_mhz <= 5900) band = "5"
  else if (freq_mhz >= 5925 && freq_mhz <= 7125) band = "6"

  # JSON-escape the SSID (backslash and double-quote)
  gsub(/\\/, "\\\\", ssid)
  gsub(/"/, "\\\"", ssid)
  gsub(/\\/, "\\\\", security)
  gsub(/"/, "\\\"", security)

  if (!first) printf ","
  first = 0
  printf "{\"ssid\":\"%s\",\"signal\":%d,\"security\":\"%s\",\"freq\":\"%s\",\"in_use\":%s}", ssid, signal, security, band, in_use
}
END { printf "]\n" }
'
