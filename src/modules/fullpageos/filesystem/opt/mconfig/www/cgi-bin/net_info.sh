#!/bin/sh
# /cgi-bin/net_info.sh
# Emits JSON: {"hostname":"...","interfaces":[{"if":"wlan0","mac":"..","ipv4":["a.b.c.d"],"state":"up"}]}

# ---- CGI headers
printf '%s\r\n\r\n' "Content-Type: application/json"

# ---- Make sure common system paths are available under CGI
export PATH="/usr/sbin:/sbin:/usr/bin:/bin"

HOST="$(hostname 2>/dev/null)"

# Prefer full-path ip if available
IPBIN="$(command -v ip 2>/dev/null || true)"
IFCONFIGBIN="$(command -v ifconfig 2>/dev/null || true)"

if [ -z "$IPBIN" ] && [ -z "$IFCONFIGBIN" ]; then
  # Last-resort: still return JSON so the frontend doesn’t choke
  printf '{"hostname":"%s","interfaces":[],"error":"no ip/ifconfig available in PATH"}\n' "$HOST"
  exit 0
fi

IFJSON=""

# Enumerate non-loopback interfaces
for IF in $(ls /sys/class/net 2>/dev/null | grep -v '^lo$'); do
  [ -e "/sys/class/net/$IF" ] || continue

  MAC="$(cat "/sys/class/net/$IF/address" 2>/dev/null)"
  STATE="$(cat "/sys/class/net/$IF/operstate" 2>/dev/null)"

  # Collect IPv4s per interface
  IPS=""
  if [ -n "$IPBIN" ]; then
    # ip -o -4 addr show dev IF → lines with "... <ip>/<cidr> ..."
    for CIDR in $("$IPBIN" -o -4 addr show dev "$IF" 2>/dev/null | awk '{print $4}'); do
      IP="${CIDR%%/*}"
      [ -n "$IP" ] || continue
      IPS="${IPS:+$IPS, }\"$IP\""
    done
  elif [ -n "$IFCONFIGBIN" ]; then
    # BusyBox ifconfig style: "inet addr:1.2.3.4" or "inet 1.2.3.4"
    LINE=$("$IFCONFIGBIN" "$IF" 2>/dev/null | grep -E 'inet (addr:)?[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' || true)
    for IP in $(echo "$LINE" | sed -E 's/.*inet (addr:)?([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+).*/\2/g'); do
      IPS="${IPS:+$IPS, }\"$IP\""
    done
  fi

  [ -n "$IPS" ] && IPS="[$IPS]" || IPS="[]"

  OBJ="{\"if\":\"$IF\",\"mac\":\"$MAC\",\"ipv4\":$IPS,\"state\":\"$STATE\"}"
  IFJSON="${IFJSON:+$IFJSON,}$OBJ"
done

printf '{"hostname":"%s","interfaces":[%s]}\n' "$HOST" "$IFJSON"
