#!/bin/sh
# /opt/mconfig/www/cgi-bin/collect_logs.sh
# Collects printer logs and either streams them for download or writes the bundle to a USB mount.

set -e

# Adjust these to your actual paths
PRINTER_LOG_DIR="/home/pi/printer_data/logs"
CHROMIUM_LOG_DIR="/home/pi/.config/chromium"
OUTNAME="printer-logs-$(date +%F-%H%M%S).tar.gz"

# Where to look for USB (space-separated list)
USB_CANDIDATES="/media /mnt /media/pi /mnt/usb /run/media"

MODE="download"
if [ -n "${QUERY_STRING:-}" ]; then
    case "${QUERY_STRING}" in
        *dest=usb*) MODE="usb" ;;
    esac
fi

WORKDIR="$(mktemp -d /tmp/logbundle.XXXXXX)"
BUNDLE_DIR="${WORKDIR}/bundle"
mkdir -p "${BUNDLE_DIR}"

# 1. printer_data logs
if [ -d "${PRINTER_LOG_DIR}" ]; then
    mkdir -p "${BUNDLE_DIR}/printer_data_logs"
    cp -r "${PRINTER_LOG_DIR}/." "${BUNDLE_DIR}/printer_data_logs/" 2>/dev/null || true
fi

# 2. chromium logs (very install specific, so we grab common files)
if [ -d "${CHROMIUM_LOG_DIR}" ]; then
    mkdir -p "${BUNDLE_DIR}/chromium"
    find "${CHROMIUM_LOG_DIR}" -maxdepth 2 -type f \( -name "*.log" -o -name "Crash*" \) -print0 | \
        xargs -0 -I{} cp "{}" "${BUNDLE_DIR}/chromium/" 2>/dev/null || true
fi

# 3. dmesg
dmesg > "${BUNDLE_DIR}/dmesg.log" 2>/dev/null || true

# 4. journalctl (best-effort) - limit to last 300 lines to keep bundle size reasonable
journalctl -n 300 --no-pager > "${BUNDLE_DIR}/journalctl.log" 2>/dev/null || true

# 5. systemctl list/status
systemctl list-units --all > "${BUNDLE_DIR}/systemctl-list.log" 2>/dev/null || true
systemctl status > "${BUNDLE_DIR}/systemctl-status.log" 2>/dev/null || true

# Pack it
tar -C "${WORKDIR}" -czf "${WORKDIR}/${OUTNAME}" "bundle"

if [ "${MODE}" = "usb" ]; then
    # Try to find a writable mount
    for d in ${USB_CANDIDATES}; do
        # Expand glob-like entries if they exist
        for m in ${d}/* ${d}; do
            if [ -d "${m}" ]; then
                # Prefer writable subdirs
                if [ -w "${m}" ]; then
                    cp "${WORKDIR}/${OUTNAME}" "${m}/" 2>/dev/null && \
                      printf 'Content-Type: text/plain\n\nSaved log bundle to %s/%s\n' "${m}" "${OUTNAME}" && rm -rf "${WORKDIR}" && exit 0 || true
                fi
                # Try children
                for sub in "${m}"/*; do
                    if [ -d "${sub}" ] && [ -w "${sub}" ]; then
                        cp "${WORKDIR}/${OUTNAME}" "${sub}/" 2>/dev/null && \
                          printf 'Content-Type: text/plain\n\nSaved log bundle to %s/%s\n' "${sub}" "${OUTNAME}" && rm -rf "${WORKDIR}" && exit 0 || true
                    fi
                done
            fi
        done
    done

    # If we got here, we did not find a USB
    printf 'Content-Type: text/plain\n\nNo writable USB mount found. Tried: %s\nBundle is at %s/%s\n' "${USB_CANDIDATES}" "${WORKDIR}" "${OUTNAME}"
    rm -rf "${WORKDIR}"
    exit 1
else
    # Default: stream for download
    printf 'Content-Type: application/gzip\n'
    printf 'Content-Disposition: attachment; filename="%s"\n' "${OUTNAME}"
    printf '\n'
    cat "${WORKDIR}/${OUTNAME}"
    rm -rf "${WORKDIR}"
    exit 0
fi
