require_network() {
  if [ -e /etc/re3d-airgap/enabled ] || [ -e /run/re3d-airgap/enabled ]; then
    printf 'Status: 403 Forbidden\r\nContent-Type: application/json\r\n\r\n'
    printf '{"error":"Network operations are disabled by air-gap policy. Use touchscreen Air-gap settings and reboot to restore access."}\n'
    exit 1
  fi
}