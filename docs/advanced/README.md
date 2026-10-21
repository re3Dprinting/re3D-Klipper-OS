# Advanced: system overview

[Wiki home](../README.md)

**Status:** Draft - repository overview for support staff and developers.

Operator guides belong in the main wiki. Keep shell commands, deployment details,
and implementation notes here so customers do not need them for everyday use.

## Main components

| Component | Role |
|---|---|
| CustomPiOS / FullPageOS | Image-building foundation |
| Klipper | Printer control software and MCU firmware |
| Moonraker | API and service integration for Klipper |
| Mainsail | Main browser-based printer interface |
| Configurator | re:3D setup, calibration, diagnostics, and recovery UI |
| Crowsnest | Webcam streaming integration |
| Chromium / labwc | Browser kiosk and Wayland desktop session |
| KlipperScreen | Alternative touchscreen interface |

## Repository map

| Source | Purpose |
|---|---|
| [Image workflow](../../.github/workflows/main.yml) | GitHub Actions image build |
| [Build setup](../../src/modules/fullpageos/start_chroot_script) | Packages, component installation, and system setup |
| [Filesystem overlay](../../src/modules/fullpageos/filesystem/) | Files installed into the image |
| [Printer configuration source](../../src/modules/fullpageos/filesystem/home/pi/printer_data/config/src/) | Machine templates and configuration generation |
| [Configurator source](../../src/modules/fullpageos/filesystem/opt/mconfig/) | Web tabs, CGI endpoints, and recovery monitor |
| [Kiosk scripts](../../src/modules/fullpageos/filesystem/opt/custompios/scripts/) | Display/session startup |
| [Service definitions](../../src/modules/fullpageos/filesystem/root_init/etc/systemd/system/) | systemd units |
| [Runtime updater](../../tools/re3d-os-update.sh) | Deployment to an installed printer |
| [Tools](../../tools/) | Regression tests and maintenance utilities |

## Authoring boundaries

- Distinguish repository paths from paths on an installed Raspberry Pi.
- Document whether a change persists across configuration generation and updates.
- State whether commands restart firmware, stop services, or change machine state.
- Test maintenance procedures away from production printers first.
- Never include credentials, private customer files, or unreviewed hardware
  recovery commands in examples.

## Planned advanced articles

- Reproducing an image build and identifying its component revisions.
- Configuration generation, local overrides, and persistence.
- Service startup and first-boot troubleshooting.
- Test coverage and adding a regression test.
- Release preparation and hardware-validation checklist.
