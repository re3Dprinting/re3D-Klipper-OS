# Getting started

[Wiki home](README.md) | [Next: everyday printing](everyday-printing.md)

**Status:** Draft - repository-reviewed; hardware validation pending.

## Goal

Reach the printer interface and confirm that its selected machine matches the
physical printer before using motion, heating, or calibration controls.

## Before you begin

- Identify the exact printer model and installed hardware.
- Obtain the correct image for your machine and the installation instructions.
- Back up an existing installation before replacing its storage.
- Have the machine's operating manual available.

## 1. Install and complete first boot

Follow the [installation instructions in the project README](../README.md#installation).
Writing an image erases the selected storage device; verify the target first.

The documented first-boot flow includes Archimajor firmware preparation. Do not
assume those board-specific instructions apply to a different controller. Follow
the on-screen prompts and the instructions approved for your hardware.

## 2. Find the Configurator

Once Mainsail is available, open its navigation menu and select the Configurator
entry. The project README includes a video showing this navigation.

## 3. Confirm the machine

The documented default is Gigabot 4. Do not assume that the default matches your
printer.

1. Open **Machine** in the Configurator.
2. Compare the selected model with the physical machine.
3. Confirm any displayed hardware options against installed accessories.
4. If a change is needed, apply it only while the printer is idle, then follow the
   restart or reboot prompt.
5. Reopen **Machine** afterward and confirm the selection.

Do not enable a probe, hotend option, crammer, or heater pad merely because it is
available in the interface.

## 4. Check network identity

Open **Network** to review the hostname, interface addresses, and connection
status. Record the address for access from another device on the same network.
Changing network settings may disconnect the browser.

## Ready for the next step

- [ ] First-boot prompts have completed.
- [ ] The selected machine and hardware options match the printer.
- [ ] No unresolved printer error is displayed.
- [ ] You know how to return to Printer Control.
- [ ] Required commissioning and calibration have been completed using approved
      machine-specific instructions.

If a check fails, use [Troubleshooting](troubleshooting.md) rather than testing the
machine with an arbitrary print.

## To complete before publication

- Add labeled screenshots from a recorded OS release.
- Add links to approved operating manuals and commissioning procedures.
- Verify first boot on each supported controller and machine combination.
