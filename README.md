# re:3D Klipper OS

### **This has fully replaced the [klipper_config](https://github.com/re3Dprinting/klipper_config) repository.**

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
* re:3D Configurator


## Installation
### Slice Engineering / Micro Swiss Hotends

For Gigabot 4 and Gigabot 4 XLT, enable **Slice Engineering / Micro Swiss Hotends**
under **Additional Options** in the machine selector, then set the machine and
reboot. Alternatively, set `slice_microswiss_hotends_enabled=true` in the `[fff]`
section of `.master.cfg` before regenerating the printer configuration.

| Platform | X maximum | Y maximum/endstop | Z maximum |
| --- | --- | --- | --- |
| Regular | 587 | 587 | 582 |
| XLT | 585 | 740 | 886 |

X and Y rotation distances are 54. This option changes motion dimensions only,
not heater tuning. It defaults to `false`; disabling it and regenerating restores
the stock profile. Existing standalone overrides still take precedence, so remove
duplicate hotend dimensions from standalone configuration when using this option.
Terabot and FGF machines do not apply these profiles.

Regression tests: `python -B -m unittest discover -s tools -p test_hotend_selection.py`
(requires Jinja2 and Bash for the CGI checks).

### Image Installation

1. Download the latest re3D-Klipper-OS-x.x.x.img.gz file from the releases page.
2. Download and install [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
3. Insert a MicroSD card into your computer. (Must be atleast 32GB)
4. Use Raspberry Pi Imager to flash the image file to the MicroSD card.
<img width="509" height="335" alt="image" src="https://github.com/user-attachments/assets/d8ee5d33-2b84-4885-b01e-33901099c672" />



5. For Device, choose "Raspberry Pi 4".
6. For Operating System, scroll down and choose "Use custom" and use the re3D-Klipper-OS-x.x.x.img.gz file.
7. For Storage, select your MicroSD card.
8. Select "Write", then "I understand, erase and write".


9. Wait for the writing and verification process.
10. Remove the MicroSD card from your computer and insert into the Raspberry Pi in the top section of the electrical enclosure while powered off.
<img width="356" height="473" alt="image" src="https://github.com/user-attachments/assets/de936394-d959-4120-b31f-2627ba650e44" />

11. Turn the printer on and wait for the boot process. This can take a few minutes.
<img width="983" height="420" alt="image" src="https://github.com/user-attachments/assets/aa475cc0-94a8-43e2-bee4-b90e4c356a65" />

12. The preparing printer screen will come up and will detect which state the Archimajor board is in.
    * If the board is already erased it will automatically attempt flashing.
    * If the board isn't detected, check the USB cabling. Contact support if this is a persistant issue. 
    * (Most common) If the board is not erased, it will prompt you to erase the mainboard. This involves pressing and holding the "ERASE" button, then clicking the "RESET" button. After manually erasing the board, you should see the flash process progress on the touchscreen.
<img width="625" height="489" alt="image" src="https://github.com/user-attachments/assets/1e1e1411-8747-4293-8b17-210b0547db53" />

13. When the board has been flashed, you will be prompted to powercycle the machine. Turn the machine off, wait 10-15 seconds, and power back on.
14. This will now boot into the printer control interface (Mainsail).
15. By default the printer configuration is set for a Gigabot 4. If you have a different printer, you will need to select it in the configurator.
16. To navigate to the configurator, slect the three navigation lines in the top left hand corner and select the bottom option.
17. Select your machine, and reboot when prompted for changes to take effect. 
  
https://github.com/user-attachments/assets/7c31e5cf-c50b-4039-a07f-7ec4125e9f1a




   




