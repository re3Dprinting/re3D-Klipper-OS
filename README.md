# re:3D Klipper OS
### **Almost ready for production. Currently in beta.**

### **This will replace the [klipper_config](https://github.com/re3Dprinting/klipper_config) repository.**

A [CustomPiOS](https://github.com/guysoft/CustomPiOS) built on top of [FullPageOS](https://github.com/guysoft/FullPageOS) (Debian Bookworm) which includes Klipper firmware and additional dependencies needed to operate the re:3D Gigabot printers.

Every new commit an image is created, streamlining development and removing the need to go through the entire build chain. 

## Making custom edits is simple: 
* Fork this repository
* Commit changes
* A new image will be built through Github Actions. 


## Core Software Stack:
* [Klipper](https://github.com/Klipper3d/klipper): The 3D-printer firmware.
* [Moonraker](https://github.com/Arksine/moonraker): The API server for Klipper. 
* [Mainsail](https://github.com/mainsail-crew/mainsail): User interface for Klipper. 
* [Crowsnest](https://github.com/mainsail-crew/crowsnest): The webcam streamer wrapper.
* [Beacon3D](https://github.com/beacon3d/beacon_klipper): Klipper module for the Beacon Eddy Current Scanner. 
* [Chromium](https://github.com/chromium/chromium): The browser used to display Mainsail on the touchscreen. 
* (WIP)
## Additional Software:
* [Klipper shell commands](https://github.com/dw-0/kiauh/blob/master/resources/gcode_shell_command.py): Klipper plugin to allow shell commands to run from gcode. 
* [Moonraker timelapse](https://github.com/mainsail-crew/moonraker-timelapse): Create timelapses of prints.
* [Pi-usb-automount](https://github.com/fasteddy516/pi-usb-automount): Auto mounts USB drives with a symbolic link to /printer_data/gcodes.
* [Automated firmware flashing](https://github.com/re3Dprinting/re3D-Klipper-OS/blob/devel/src/modules/fullpageos/filesystem/home/pi/printer_data/config/src/system_config/shell_commands/flash_firmware.sh): Attempts to flash the microcontroller with Klipper firmware on first boot. 
* [Multi-machine configuration](https://github.com/re3Dprinting/re3D-Klipper-OS/blob/devel/src/modules/fullpageos/filesystem/home/pi/printer_data/config/src/reload.py): Checks for printer type and uses the appropriate configuration files.
* (WIP)


## Installation
(WIP) - follows similiar steps to [previous instructions](https://github.com/user-attachments/files/16969831/Klipper.Installation.Instructions.V0.5.0.pdf) 
