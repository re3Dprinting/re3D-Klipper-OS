# Updates, backups, and recovery

[Wiki home](README.md) | [Troubleshooting](troubleshooting.md)

**Status:** Draft - limitations and preparation checklist, not a complete
update or recovery procedure.

## Software updates

The **System** section checks connectivity and available updates. The update
flow can replace software, restart services, reboot, and request controller
firmware work.

Before using it:

- Finish or cancel active and paused jobs.
- Preserve configuration, calibration, and any important job data separately.
- Record the current version/revision and machine configuration.
- Arrange a maintenance window with stable power and network connectivity.
- Confirm the update and firmware instructions for the installed controller.

Do not assume the interface enforces all of these prerequisites. Do not start
another update because the browser disconnects during a restart. Capture errors
and use [Troubleshooting](troubleshooting.md) if the system does not return.

## History backup is not a full printer backup

The current history-save implementation archives G-code and the Moonraker SQLite
database file to USB. It does **not** include a complete printer-configuration
backup or OS image. Database consistency during live backup has not been
validated by this guide.

Before relying on a backup, verify its contents and test restoration on a
non-production installation. Preserve configuration and calibration separately.
A restore replaces data; it is not a way to merge two printers' histories.

## Print Recovery is experimental

The **Print Recovery** section is labeled beta. The software can use a saved
snapshot or a selected height to generate a recovery G-code file.

Generating a file does not establish that it is suitable to run. A successful
recovery depends on physical position, the remaining part, original G-code state,
tool selection, and machine-specific preparation. Do not start generated recovery
G-code without a reviewed procedure and qualified operator assessment.

Preserve the original file and recovery information when requesting support.
Do not treat recovery as a replacement for normal print monitoring or backups.

## To complete before publication

- Release-specific update walkthrough, including controller compatibility.
- Clearly scoped configuration and full-system backup instructions.
- Backup validation and restore rehearsal.
- Review of generated recovery G-code and supported slicer/machine combinations.
- Hardware-validated recovery instructions and explicit stop conditions.
