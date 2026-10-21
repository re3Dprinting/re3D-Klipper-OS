# Troubleshooting

[Wiki home](README.md) | [Updates and recovery](updates-and-recovery.md)

**Status:** Draft - initial triage and support checklist.

## Start with evidence

Record the exact error and what happened immediately before it. Avoid repeated
reboots, reflashing, or calibration changes before capturing the available logs.
For a physical hazard, follow the machine's documented emergency procedure
instead of this software checklist.

| Symptom | First checks | Useful evidence |
|---|---|---|
| Touchscreen does not show printer controls | Note whether a splash, desktop, error, or blank screen appears; try the known printer address from another device on the same network | Screen photo, boot stage, recent display/update changes |
| Printer unavailable over the network | If the local UI works, review **Network** for interface status and current address | Hostname, connection type, relevant network error |
| MCU or firmware error | Capture the exact message and identify the installed controller; do not erase it based only on a generic connection error | Error text, controller model, recent firmware changes |
| Wrong machine or travel configuration | Review **Machine** without commanding movement | Selected model and installed options |
| Update appears stuck | Keep power on; capture the displayed stage and log instead of launching another update | Update log, elapsed time, source/target versions if known |
| Interrupted print | Preserve the original G-code and available recovery information | Filename, failure time, screenshot, recovery state if available |
| Unexpected print quality | Record material, nozzle, slicer profile, and the visible defect before changing settings | Photos, job file, calibration history |

## Collect a support bundle

The Configurator's **Troubleshooting** section provides log collection and
analysis tools. Record any error from those tools as well as the original issue.

Review files before sharing: logs, screenshots, job names, and configuration may
contain private network details or customer information. Do not post passwords,
API keys, or confidential part files to a public issue.

### Support request template

```text
Printer model / controller:
Installed hardware options:
OS version or revision (if known):
Connection: Ethernet / Wi-Fi / local touchscreen

What I expected:
What happened:
Exact error:
When it happened, including timezone:

Steps to reproduce:
Recent software, hardware, or configuration changes:
Checks already attempted and their results:

Attachments reviewed for sensitive information:
- Screenshot/photo
- Relevant logs
- Non-confidential reproduction file, if needed
```

Use your established re:3D support channel. For reproducible software issues,
the [repository issue tracker](https://github.com/re3Dprinting/re3D-Klipper-OS/issues)
is another option after removing sensitive information.

## Expand this section

Use the [troubleshooting article template](templates/troubleshooting.md) for
reviewed symptom-specific guides. Distinguish confirmed causes from possibilities.
