# Everyday printing and interface guide

[Wiki home](README.md) | [Calibration](calibration.md)

**Status:** Draft - repository-reviewed; hardware validation pending.

## Where to find things

| Interface / section | Purpose |
|---|---|
| Printer Control (Mainsail) | Main print interface, files, and printer status |
| Configurator: Machine | Select machine type and applicable hardware options |
| Configurator: Network | Review network information and manage connections |
| Configurator: Tuning | Bed-leveling assistance and temperature-control tuning |
| Configurator: Input Shaper | Resonance measurement and shaping tools |
| Configurator: Calibration | Flow and pressure-advance calibration tools |
| Configurator: Troubleshooting | Log collection and diagnostic tools |
| Configurator: Print Recovery | Experimental interrupted-print recovery |
| Configurator: System | OS updates, display selection, and history backup/restore |

Availability depends on the installed software and hardware. This table is a
navigation aid, not an instruction to run every tool.

## Before each print

- [ ] The file was sliced for this machine, installed nozzle/tool configuration,
      and material.
- [ ] The machine has completed its required setup and calibration.
- [ ] The build area is prepared according to the machine's operating manual.
- [ ] Enough suitable material is loaded for the job.
- [ ] The printer reports no unresolved error.
- [ ] The operator has checked the job preview and estimated duration.

Use your approved machine-specific procedure to upload, start, and monitor the
job in Printer Control. Do not change machine type, update software, or restore
backups while printing or paused.

## Keep a repeatability record

For a successful job, record the G-code filename, slicer/profile version,
material, nozzle, relevant calibration values, and any observations. Use a
[material recipe](templates/material-recipe.md) when documenting a repeatable
setup. Recipes are documentation, not an automatic settings-import feature.

## To complete before publication

- Add an annotated Mainsail screenshot and a short upload/start walkthrough.
- Link approved material loading, print monitoring, and part-removal procedures.
- Document pause, resume, and cancel behavior separately for filament and pellet
  machines after hardware validation.
