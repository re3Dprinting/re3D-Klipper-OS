# Calibration guide

[Wiki home](README.md) | [Troubleshooting](troubleshooting.md)

**Status:** Draft - navigation and planning only; not a calibration procedure.

## Choose the right tool

| Goal | Configurator section | What the finished guide must establish |
|---|---|---|
| Check bed alignment | Tuning | Approved homing, clearances, and leveling method |
| Tune heater temperature control | Tuning | Correct heater, material conditions, and test temperature |
| Adjust extrusion flow | Calibration | Correct tool, nozzle, material, and measurement method |
| Tune pressure advance | Calibration | Suitable test conditions and interpretation of the result |
| Measure resonance | Input Shaper | Supported sensor, mounting, axes, and motion clearances |

Calibration can move the machine, heat components, or restart firmware. Use only
the reviewed procedure applicable to your hardware, with no active or paused job.

## Record results

For each calibration, capture:

- Printer model, controller, installed options, and OS revision.
- Material and nozzle/tool configuration, where relevant.
- Previous settings and the reason for changing them.
- Test conditions, observations, and resulting values.
- Where the result is stored and whether a restart is required.
- A verification print or measurement showing the outcome.

Do not transfer results between different machines or hardware configurations
without confirming that they apply.

## Planned how-to articles

Create each article from the [how-to template](templates/how-to.md):

- Bed-leveling walkthrough for each applicable machine configuration.
- Heater-specific PID tuning.
- Flow calibration and result submission.
- Pressure-advance calibration and result submission.
- Input-shaper sensor setup, measurement, and result review.
- Dual-nozzle alignment and verification.

Each article needs hardware-reviewed prerequisites, screenshots, expected
results, and a recovery path before it can be marked verified.
