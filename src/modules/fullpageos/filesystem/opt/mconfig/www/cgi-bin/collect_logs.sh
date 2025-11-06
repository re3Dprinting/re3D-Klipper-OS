#!/bin/sh
# /cgi-bin/collect_logs.sh

set -e

# adjust these to your system
PRINTER_LOG_DIR="/home/pi/printer_data/logs"
CHROMIUM_LOG_DIR="/home/pi/.config/chromium"
USB_MOUNT="/media/usb0"

OUTNAME="printer-logs-$(date +%F-%H%M%S).tar.gz"

MODE="download"
if [ -n "$QUERY_STRING" ]; then
    case "$QUERY_STRING" in
        *dest=usb*) MODE="usb" ;;
    esac
fi

WORKDIR="$(mktemp -d /tmp/logbundle.XXXXXX)"
BUNDLE_DIR="${WORKDIR}/bundle"
mkdir -p "$BUNDLE_DIR"

# 1. printer logs
if [ -d "$PRINTER_LOG_DIR" ]; then
    mkdir -p "$BUNDLE_DIR/printer_data_logs"
    cp -r "$PRINTER_LOG_DIR"/. "$BUNDLE_DIR/printer_data_logs/" 2>/dev/null || true
fi

# 2. chromium logs
if [ -d "$CHROMIUM_LOG_DIR" ]; then
    mkdir -p "$BUNDLE_DIR/chromium"
    find "$CHROMIUM_LOG_DIR" -maxdepth 2 -type f \( -name "*.log" -o -name "Crash*" \) -print0 | \
        xargs -0 -I{} cp "{}" "$BUNDLE_DIR/chromium/" 2>/dev/null || true
fi

# 3. dmesg
dmesg > "$BUNDLE_DIR/dmesg.log" 2>/dev/null || true

# 4. journalctl
journalctl -xe --no-pager > "$BUNDLE_DIR/journalctl.log" 2>/dev/null || \
journalctl --no-pager > "$BUNDLE_DIR/journalctl.log" 2>/dev/null || true

# 5. systemctl
systemctl list-units --all > "$BUNDLE_DIR/systemctl-list.log" 2>/dev/null || true
systemctl status > "$BUNDLE_DIR/systemctl-status.log" 2>/dev/null || true

# pack it
tar -C "$WORKDIR" -czf "${WORKDIR}/${OUTNAME}" "bundle"

if [ "$MODE" = "usb" ]; then
    # check that usb is mounted and writable
    if [ -d "$USB_MOUNT" ] && [ -w "$USB_MOUNT" ]; then
        cp "${WORKDIR}/${OUTNAME}" "$USB_MOUNT/${OUTNAME}"
        echo "Content-Type: text/plain"
        echo
        echo "Saved log bundle to $USB_MOUNT/${OUTNAME}"
        rm -rf "$WORKDIR"
        exit 0
    else
        echo "Content-Type: text/plain"
        echo
        echo "USB not available or not writable at $USB_MOUNT"
        rm -rf "$WORKDIR"
        exit 1
    fi
else
    # browser download
    echo "Content-Type: application/gzip"
    echo "Content-Disposition: attachment; filename=${OUTNAME}"
    echo
    cat "${WORKDIR}/${OUTNAME}"
    rm -rf "$WORKDIR"
    exit 0
fi