# stallguard_monitor.py
#
# Klipper extra: monitor TMC StallGuard values during motion to detect
# collisions and physical stalls before damage occurs.
#
# Inspired by Prusa MK3S/TMC2130 crash detection:
#   • Fixed absolute threshold — only reached when the rotor is truly blocked
#   • Window clears on any above-threshold sample (direction changes and speed
#     changes produce SG well above a near-zero stall threshold, so they reset
#     the detector rather than accumulating toward a false trigger)
#   • Hardware TCOOLTHRS gates StallGuard in the chip at low step rates
#   • Hardware SFILT (4-full-step filter) suppresses per-step noise
#
# For each configured TMC driver, add to its Klipper section:
#   driver_SGT:       0    # stall sensitivity: -64..63, lower = more sensitive
#   driver_TCOOLTHRS: 500  # hardware speed gate (~15 mm/s at 12 MHz, 100 s/mm)
#   driver_SFILT:     1    # 4-full-step hardware filtering during printing
#
# GCode commands exposed:
#   SG_STATUS                     - print current SG_RESULT for all motors
#   SG_START                      - enable monitoring
#   SG_STOP                       - disable monitoring
#   SG_SET_THRESHOLD THRESHOLD=N  - change threshold at runtime
#   SG_RESET                      - clear collision latch so printing can resume
#   SG_CALIBRATE                  - move each axis and report SG statistics
#   SG_DIAG                       - diagnose TMC register read path
#   SG_TRACE_START                - start CSV motion trace
#   SG_TRACE_STOP                 - stop CSV motion trace
#
# Place this file at ~/klipper/klippy/extras/stallguard_monitor.py
# Add [stallguard_monitor] to printer.cfg (see stallguard_standalone.cfg)

import logging
import math
from collections import deque

# TMC driver types searched in priority order
_TMC_TYPES = ['tmc5160', 'tmc2240', 'tmc2130', 'tmc2209', 'tmc2208', 'tmc2660']

# DRVSTATUS register bit masks (shared across TMC213x / TMC516x families)
_SG_RESULT_MASK = 0x000003FF  # bits  9:0  - StallGuard result (0=max load)
_STST_BIT       = 1 << 31     # bit  31    - Standstill indicator


class StallGuardMonitor:

    def __init__(self, config):
        self.printer  = config.get_printer()
        self.reactor  = self.printer.get_reactor()
        self.gcode    = self.printer.lookup_object('gcode')
        self.logger   = logging.getLogger('stallguard_monitor')

        # ── Configuration ────────────────────────────────────────────────────
        self.poll_interval = config.getfloat(
            'poll_interval', 0.1, minval=0.02, maxval=5.0)
        self.motor_names = config.getlist('motors')
        self.collision_threshold = config.getint(
            'collision_threshold', 100, minval=0, maxval=1023)
        self.consecutive_triggers = config.getint(
            'consecutive_triggers', 4, minval=1, maxval=50)
        self.accel_blank_samples = config.getint(
            'accel_blank_samples', 4, minval=0, maxval=50)
        self.action = config.getchoice(
            'collision_action',
            {'none': 'none', 'pause': 'pause', 'emergency_stop': 'emergency_stop'},
            'pause')
        self.enabled      = config.getboolean('enabled', True)
        self.homing_only  = config.getboolean('homing_only', True)

        # Per-motor threshold overrides (falls back to collision_threshold if absent)
        self._motor_thresholds = {}
        for motor in self.motor_names:
            key = 'collision_threshold_' + motor.replace(' ', '_')
            self._motor_thresholds[motor] = config.getint(
                key, self.collision_threshold, minval=0, maxval=1023)

        # Baseline calibration parameters
        self.calibrate_distance = config.getfloat(
            'calibrate_distance', 20., minval=5., maxval=200.)
        self.calibrate_speed = config.getfloat(
            'calibrate_speed', 50., minval=5., maxval=300.)

        # Software velocity gate — backup for hardware TCOOLTHRS.
        # Prefer setting driver_TCOOLTHRS in each TMC section (hardware gate).
        self.min_speed_mm_s = config.getfloat(
            'min_speed_mm_s', 0., minval=0., maxval=500.)
        # Consecutive SG=0 samples past velocity gate and accel blank needed to
        # declare a hard stall.  0 = disabled (safe default).
        self.stall_zero_count = config.getint(
            'stall_zero_count', 0, minval=0, maxval=200)
        # Optional CSV motion trace (set path to auto-start; use SG_TRACE_START at runtime)
        self.trace_file = config.get('trace_file', None)

        # ── Runtime state ────────────────────────────────────────────────────
        self.tmc_drivers        = {}   # motor_name -> tmc object
        self.sg_values          = {}   # motor_name -> last SG_RESULT int or None
        self._sg_windows        = {}   # motor_name -> deque(maxlen=consecutive_triggers)
        self._accel_blanks      = {}   # motor_name -> int countdown after standstill
        self._sg_zero_counts    = {}   # motor -> consecutive SG=0 count while moving
        self._reg_names         = {}   # motor_name -> {reg, sg, stst, stst_reg}
        self.monitoring         = False
        self._timer_handle      = None
        self._collision_latch   = False  # set on first collision; cleared by SG_RESET
        self._homing_active     = False  # True while G28 is running
        self._calibrating       = False  # True during SG_CALIBRATE move
        self._calibrate_samples = {}     # motor -> [sg_val, ...] during calibration
        self._toolhead          = None   # looked up in _handle_ready
        self._trace_fh          = None   # file handle for CSV motion trace
        self._trace_writer      = None   # csv.writer for motion trace

        # ── Klipper hooks ────────────────────────────────────────────────────
        self.printer.register_event_handler("klippy:ready", self._handle_ready)

        # ── GCode commands ───────────────────────────────────────────────────
        self.gcode.register_command(
            'SG_STATUS', self.cmd_SG_STATUS,
            desc="Report current StallGuard values for all monitored motors")
        self.gcode.register_command(
            'SG_START', self.cmd_SG_START,
            desc="Enable StallGuard collision monitoring")
        self.gcode.register_command(
            'SG_STOP', self.cmd_SG_STOP,
            desc="Disable StallGuard collision monitoring")
        self.gcode.register_command(
            'SG_SET_THRESHOLD', self.cmd_SG_SET_THRESHOLD,
            desc="Set StallGuard collision threshold (SG_SET_THRESHOLD THRESHOLD=100)")
        self.gcode.register_command(
            'SG_RESET', self.cmd_SG_RESET,
            desc="Clear StallGuard collision latch so monitoring resumes")
        self.gcode.register_command(
            'SG_DIAG', self.cmd_SG_DIAG,
            desc="Dump raw TMC driver attributes to diagnose READ ERR")
        self.gcode.register_command(
            'SG_CALIBRATE', self.cmd_SG_CALIBRATE,
            desc="Move each axis and report baseline SG statistics for belt/tension check")
        self.gcode.register_command(
            'SG_TRACE_START', self.cmd_SG_TRACE_START,
            desc="Start CSV motion trace log (SG_TRACE_START [FILE=/path/to/file.csv])")
        self.gcode.register_command(
            'SG_TRACE_STOP', self.cmd_SG_TRACE_STOP,
            desc="Stop CSV motion trace log and close file")

    # ── Initialisation ────────────────────────────────────────────────────────

    def _handle_ready(self):
        """Discover TMC driver objects once the printer is fully ready."""
        for motor in self.motor_names:
            tmc = self._find_tmc_driver(motor)
            if tmc is not None:
                self.tmc_drivers[motor]    = tmc
                self.sg_values[motor]      = None
                self._sg_windows[motor]     = deque(maxlen=self.consecutive_triggers)
                self._accel_blanks[motor]   = self.accel_blank_samples
                self._sg_zero_counts[motor] = 0
                names = self._probe_names(tmc)
                self._reg_names[motor]     = names
                self.logger.info(
                    "stallguard_monitor: '%s' reg=%s sg=%s stst=%s",
                    motor, names.get('reg'), names.get('sg'), names.get('stst'))
            else:
                self.logger.warning(
                    "stallguard_monitor: no TMC driver found for '%s' - skipping", motor)

        if not self.tmc_drivers:
            self.logger.error(
                "stallguard_monitor: no drivers found; monitoring disabled")
            return

        # Register homing events regardless of homing_only so SG_START / SG_STOP
        # still work manually when homing_only is False.
        self.printer.register_event_handler(
            "homing:home_rails_begin", self._handle_homing_begin)
        self.printer.register_event_handler(
            "homing:home_rails_end",   self._handle_homing_end)

        self._toolhead = self.printer.lookup_object('toolhead', None)
        self.printer.register_event_handler("klippy:disconnect", self._handle_disconnect)
        if self.trace_file:
            self._open_trace_file(self.trace_file)

        if self.enabled and not self.homing_only:
            self._start_monitoring()

    def _handle_homing_begin(self, homing_state, rails):
        """Auto-arm monitoring at the start of any G28 homing move."""
        if not self.enabled:
            return
        self._homing_active   = True
        self._collision_latch = False
        self._reset_windows()
        if not self.monitoring:
            self._start_monitoring()
        self.logger.info("stallguard_monitor: homing started — monitoring armed")

    def _handle_homing_end(self, homing_state, rails):
        """Disarm monitoring when G28 homing completes."""
        self._homing_active = False
        if self.homing_only:
            self._stop_monitoring()
            self._collision_latch = False
            self._reset_windows()
            self.logger.info("stallguard_monitor: homing complete — monitoring disarmed")

    def _handle_disconnect(self):
        """Flush and close the trace file on printer disconnect or shutdown."""
        if self._trace_fh is not None:
            try:
                self._trace_fh.flush()
                self._trace_fh.close()
            except Exception:
                pass
            self._trace_fh = None
            self._trace_writer = None

    def _open_trace_file(self, path):
        """Open (or reopen) the CSV motion trace file for writing."""
        import csv
        # Close any previously open file first
        if self._trace_fh is not None:
            try:
                self._trace_fh.flush()
                self._trace_fh.close()
            except Exception:
                pass
            self._trace_fh = None
            self._trace_writer = None
        try:
            self._trace_fh = open(path, 'w', newline='', buffering=1)  # line-buffered
            self._trace_writer = csv.writer(self._trace_fh)
            self._trace_writer.writerow([
                'time_s', 'motor', 'sg', 'threshold',
                'window_avg', 'window_n', 'vel_mms',
                'pos_x', 'pos_y', 'pos_z', 'state'])
            self.logger.info("stallguard_monitor: trace -> %s", path)
        except Exception as e:
            self.logger.warning("stallguard_monitor: trace open failed: %s", e)
            self._trace_fh = None
            self._trace_writer = None

    def _write_trace(self, eventtime, motor, sg_val, state, vel, pos):
        """
        Write one row to the motion trace CSV.
        state codes: M=moving(>=thresh), G=below_thresh, A=accel_blank,
                     V=vel_gated, C=collision
        Called from the reactor callback — all exceptions silently swallowed.
        """
        if self._trace_writer is None:
            return
        try:
            trig = self._motor_thresholds.get(motor, self.collision_threshold)
            w = self._sg_windows.get(motor)
            w_avg = round(sum(w) / len(w), 1) if w else ''
            self._trace_writer.writerow([
                round(eventtime, 3), motor, sg_val, trig,
                w_avg, len(w) if w is not None else 0,
                round(vel, 1) if vel is not None else '',
                round(pos[0], 2) if pos else '',
                round(pos[1], 2) if pos else '',
                round(pos[2], 2) if pos else '',
                state])
        except Exception:
            pass

    def _find_tmc_driver(self, motor_name):
        """Return the first TMC driver object found for motor_name, or None."""
        for tmc_type in _TMC_TYPES:
            obj_name = "{} {}".format(tmc_type, motor_name)
            try:
                return self.printer.lookup_object(obj_name)
            except Exception:
                pass
        return None

    def _probe_names(self, tmc):
        """
        Scan fields.all_fields to discover the actual register/field names used
        by this Klipper build for StallGuard result and standstill indicator.
        Returns a dict with keys: reg, sg, stst_reg, stst.
        """
        result = {}
        if not (hasattr(tmc, 'fields') and hasattr(tmc.fields, 'all_fields')):
            return result
        sg_patterns   = ('sg_result', 'sg4_result', 'sgresult')
        stst_patterns = ('stst', 'standstill')
        for reg_name, fields_dict in tmc.fields.all_fields.items():
            for field_name in fields_dict.keys():
                fl = field_name.lower()
                if 'reg' not in result and any(p in fl for p in sg_patterns):
                    result['reg'] = reg_name
                    result['sg']  = field_name
                if 'stst' not in result and any(p in fl for p in stst_patterns):
                    result['stst_reg'] = reg_name
                    result['stst']     = field_name
                if 'reg' in result and 'stst' in result:
                    return result
        return result

    # ── Monitoring control ────────────────────────────────────────────────────

    def _start_monitoring(self):
        if self.monitoring:
            return
        self.monitoring    = True
        self._timer_handle = self.reactor.register_timer(
            self._poll_callback, self.reactor.NOW)
        self.logger.info("stallguard_monitor: monitoring started "
                         "(threshold=%d, action=%s, poll=%.2fs, window=%d, blank=%d)",
                         self.collision_threshold, self.action, self.poll_interval,
                         self.consecutive_triggers, self.accel_blank_samples)

    def _stop_monitoring(self):
        self.monitoring = False
        if self._timer_handle is not None:
            self.reactor.unregister_timer(self._timer_handle)
            self._timer_handle = None
        self.logger.info("stallguard_monitor: monitoring stopped")

    # ── Window helpers ────────────────────────────────────────────────────────

    def _reset_windows(self):
        """Clear all detection windows and blanking counters."""
        for m in self._sg_windows:
            self._sg_windows[m].clear()
            self._accel_blanks[m]   = self.accel_blank_samples
            self._sg_zero_counts[m] = 0

    # ── Polling ───────────────────────────────────────────────────────────────

    def _poll_callback(self, eventtime):
        if not self.monitoring:
            return self.reactor.NEVER

        # Cache toolhead commanded velocity once per poll (covers all motors).
        # This reflects Klipper's trapezoid planner so it drops naturally during
        # both acceleration AND deceleration phases.
        _tvel = None
        if self.min_speed_mm_s > 0 and self._toolhead is not None:
            try:
                v = self._toolhead.get_status(eventtime).get('velocity')
                if v is not None:
                    _tvel = abs(float(v))
                # If 'velocity' is absent from toolhead status (older Klipper
                # builds do not expose it), _tvel stays None and gating is
                # skipped — accel_blank_samples handles the accel phase alone.
            except Exception:
                pass

        # Cache toolhead position once per poll for trace logging.
        _tpos = None
        if self._trace_writer is not None and self._toolhead is not None:
            try:
                _tpos = self._toolhead.get_position()
            except Exception:
                pass

        try:
            for motor, tmc in self.tmc_drivers.items():
                raw = self._read_drv_status(tmc, motor, eventtime)
                if raw is None:
                    continue

                sg_val     = raw & _SG_RESULT_MASK
                standstill = bool(raw & _STST_BIT)

                if standstill:
                    # Motor stopped or standstill indicator set.
                    #
                    # During homing (_homing_active=True): STST means the
                    # motor hit its endstop — an expected event, not a stall.
                    # Always clear the window so we don't fire on endstop load.
                    # Crash detection before the endstop still works: a crash
                    # fills the window with SG=0 before STST ever sets.
                    #
                    # During printing (_homing_active=False): only clear the
                    # window when it is empty (genuine inter-move standstill).
                    # If the window has below-threshold samples, a STST flip is
                    # almost certainly rotor oscillation during a hard stall
                    # (TMC drives pulses → rotor hits block → STST=1 → retries).
                    # Preserving the samples lets the next non-STST SG=0 sample
                    # complete the window in ~60 ms rather than seconds.
                    self._sg_zero_counts[motor] = 0
                    if self._homing_active or not self._sg_windows[motor]:
                        self._accel_blanks[motor] = self.accel_blank_samples
                        self._sg_windows[motor].clear()
                    # Either way, skip this sample (SG unreliable during STST).
                    continue

                # Velocity gate: skip and clear window when the toolhead's
                # commanded speed is below min_speed_mm_s (software backup for
                # hardware TCOOLTHRS).  Clears the window so detection requires
                # fresh consecutive samples after the low-speed phase.
                if _tvel is not None and _tvel < self.min_speed_mm_s:
                    self._sg_windows[motor].clear()
                    self._sg_zero_counts[motor] = 0
                    self._write_trace(eventtime, motor, sg_val, 'V', _tvel, _tpos)
                    continue

                # Sample-count blanking right after standstill clears.
                # Handles the brief ramp-up before the velocity gate takes over.
                if self._accel_blanks[motor] > 0:
                    self._accel_blanks[motor] -= 1
                    self._write_trace(eventtime, motor, sg_val, 'A', _tvel, _tpos)
                    continue

                # SG_RESULT=0: a blocked rotor at speed reliably reads 0.
                # If stall_zero_count > 0: use the dedicated consecutive-zero
                # path and skip the main window to avoid double-firing.
                # If stall_zero_count == 0 (default): fall through to the main
                # threshold comparison — 0 < threshold(15) so it accumulates in
                # the detection window and fires after consecutive_triggers
                # samples, just like any other below-threshold reading.
                if sg_val == 0 and self.stall_zero_count > 0:
                    if not self._collision_latch:
                        cnt = self._sg_zero_counts.get(motor, 0) + 1
                        self._sg_zero_counts[motor] = cnt
                        if cnt >= self.stall_zero_count:
                            self._sg_zero_counts[motor] = 0
                            self._write_trace(
                                eventtime, motor, 0, 'C', _tvel, _tpos)
                            self._handle_collision(motor, 0)
                    continue

                # Non-zero sample: reset consecutive-zero counter.
                self._sg_zero_counts[motor] = 0
                self.sg_values[motor] = sg_val

                # Collect samples for SG_CALIBRATE if active.
                if self._calibrating and motor in self._calibrate_samples:
                    self._calibrate_samples[motor].append(sg_val)

                thresh = self._motor_thresholds.get(motor, self.collision_threshold)
                window = self._sg_windows[motor]

                if sg_val >= thresh:
                    # Above threshold: motor running normally.
                    # Clear the window so detection requires consecutive_triggers
                    # NEW below-threshold samples to confirm a stall — equivalent
                    # to Prusa's hardware DIAG de-asserted state.  Any recovery
                    # spike (direction change, speed change, decel) clears the
                    # detector before it can fire, eliminating false triggers
                    # without EWMA baselines, guard zones, or pre-stall blanks.
                    window.clear()
                    self._write_trace(eventtime, motor, sg_val, 'M', _tvel, _tpos)
                    continue

                # Below threshold: potential stall — accumulate toward detection.
                # The threshold should be a LOW absolute value (5-15) that is
                # only reached when the rotor is physically blocked.  Normal
                # motion at any speed — including direction changes — produces
                # SG well above a near-zero threshold.
                self._write_trace(eventtime, motor, sg_val, 'G', _tvel, _tpos)

                if self._collision_latch:
                    continue

                window.append(sg_val)
                if len(window) == window.maxlen:
                    avg = sum(window) / window.maxlen
                    self._write_trace(eventtime, motor, int(round(avg)), 'C', _tvel, _tpos)
                    window.clear()
                    self._handle_collision(motor, int(round(avg)))

        except Exception:
            self.logger.exception("stallguard_monitor: error in poll callback")

        return eventtime + self.poll_interval

    # ── Register read ─────────────────────────────────────────────────────────

    def _read_drv_status(self, tmc, motor, eventtime):
        """
        Return a value with SG_RESULT in bits[9:0] and STST in bit[31],
        or None if nothing could be read.
        Uses names discovered at startup by _probe_names() so the code is
        not sensitive to which exact string this Klipper build uses.
        """
        names     = self._reg_names.get(motor, {})
        reg_name  = names.get('reg')
        sg_name   = names.get('sg')
        stst_name = names.get('stst')

        # 1) Direct SPI read with the probed register name – always fresh data.
        #    Some builds return the raw SPI params dict; decode bytes if so.
        if reg_name and hasattr(tmc, 'mcu_tmc') and hasattr(tmc.mcu_tmc, 'get_register'):
            try:
                val = tmc.mcu_tmc.get_register(reg_name)
                if isinstance(val, int):
                    return val
                if isinstance(val, dict):
                    resp = val.get('response', [])
                    if len(resp) >= 5:   # 1 status byte + 4 data bytes
                        data = bytearray(resp[1:5])
                        return sum(b << ((3 - i) * 8) for i, b in enumerate(data))
            except Exception as e:
                self.logger.debug("stallguard: get_register(%s): %s", reg_name, e)

        # 2) Reconstruct from individual cached fields using probed field names.
        # Guard: get_field() returns the last value the Klipper field cache
        # holds — this starts at 0 and stays 0 until an async monitoring cycle
        # runs.  Only trust it when sg > 0 to avoid silently masking path-1
        # failures with a stale zero (which the poll loop would then discard
        # via 'if sg_val == 0: continue', hiding the real read failure).
        if sg_name and stst_name and hasattr(tmc, 'fields') and hasattr(tmc.fields, 'get_field'):
            try:
                sg   = tmc.fields.get_field(sg_name)
                stst = tmc.fields.get_field(stst_name)
                if isinstance(sg, int) and isinstance(stst, int) and sg > 0:
                    return (stst << 31) | (sg & _SG_RESULT_MASK)
            except Exception as e:
                self.logger.debug("stallguard: get_field(%s/%s): %s", sg_name, stst_name, e)

        # 3) Klipper field cache for the probed register.
        if reg_name and hasattr(tmc, 'fields') and hasattr(tmc.fields, 'get_reg'):
            try:
                val = tmc.fields.get_reg(reg_name)
                if isinstance(val, int):
                    return val
            except Exception as e:
                self.logger.debug("stallguard: fields.get_reg(%s): %s", reg_name, e)

        # 4) get_status() – drv_status is None until motors have moved at least
        #    once, so this is a last resort only.
        for call_args in [(eventtime,), ()]:
            try:
                val = tmc.get_status(*call_args).get('drv_status')
                if isinstance(val, int):
                    return val
                break
            except TypeError:
                continue
            except Exception:
                break

        return None

    # ── Collision handling ────────────────────────────────────────────────────

    def _handle_collision(self, motor, sg_val):
        """React to a detected collision event."""
        if self._homing_active:
            # During homing the endstop hit loads the motor against the switch,
            # producing SG=0 samples that look identical to a stall.  Klipper
            # already handles missed endstops (probe fail / timeout), so suppress
            # stallguard collision firing for the entire homing sequence.
            return
        self._collision_latch = True
        thr_info = "threshold={}".format(
            self._motor_thresholds.get(motor, self.collision_threshold))

        # Clear the window so we don't immediately re-trigger after SG_RESET
        self._reset_windows()

        msg = ("!! stallguard_monitor: COLLISION on {} "
               "(SG_RESULT={}, {})".format(motor, sg_val, thr_info))
        self.logger.warning(msg)

        if self.action == 'emergency_stop':
            self.printer.invoke_async_shutdown(msg)
        elif self.action == 'pause':
            # Run PAUSE in the background to avoid reactor re-entrancy
            self.printer.get_reactor().register_async_callback(
                lambda e: self.gcode.run_script("PAUSE\nM118 " + msg))
        else:
            # action == 'none': log only
            self.printer.get_reactor().register_async_callback(
                lambda e: self.gcode.run_script("M118 " + msg))

    # ── GCode command handlers ────────────────────────────────────────────────

    def cmd_SG_STATUS(self, gcmd):
        if not self.tmc_drivers:
            gcmd.respond_info("stallguard_monitor: no drivers configured")
            return

        eventtime = self.reactor.monotonic()
        mode = ("HOMING" if self._homing_active
                else ("ON" if self.monitoring else "OFF"))
        lines = [
            "StallGuard Monitor  action={}  monitoring={}{}  default_threshold={}".format(
                self.action, mode,
                "  [homing-only]" if self.homing_only else "",
                self.collision_threshold),
            "  {:<16} {:>10}  {:>9}  {}".format("motor", "SG_RESULT", "threshold", "state")
        ]
        for motor in self.motor_names:
            tmc    = self.tmc_drivers.get(motor)
            thresh = self._motor_thresholds.get(motor, self.collision_threshold)
            if tmc is None:
                lines.append("  {:<16} {:>10}  {:>9}".format(motor, "NO DRIVER", thresh))
                continue
            raw = self._read_drv_status(tmc, motor, eventtime)
            if raw is None:
                lines.append(
                    "  {:<16} {:>10}  {:>9}  (run SG_DIAG for details)".format(
                        motor, "READ ERR", thresh))
                continue
            sg_val     = raw & _SG_RESULT_MASK
            standstill = bool(raw & _STST_BIT)
            self.sg_values[motor] = sg_val
            thresh_str = str(thresh)
            flag = " <<< LOW" if (sg_val < thresh and not standstill) else ""
            state = "standstill" if standstill else "moving"
            lines.append("  {:<16} {:>10}  {:>16}  {}{}".format(
                motor, sg_val, thresh_str, state, flag))

        gcmd.respond_info("\n".join(lines))

    def cmd_SG_START(self, gcmd):
        self._collision_latch = False
        self._reset_windows()
        self._start_monitoring()
        gcmd.respond_info(
            "stallguard_monitor: started (threshold={}, action={})".format(
                self.collision_threshold, self.action))

    def cmd_SG_STOP(self, gcmd):
        self._stop_monitoring()
        gcmd.respond_info("stallguard_monitor: stopped")

    def cmd_SG_SET_THRESHOLD(self, gcmd):
        threshold = gcmd.get_int('THRESHOLD', minval=0, maxval=1023)
        motor     = gcmd.get('MOTOR', None)
        if motor is not None:
            motor = motor.strip()
            if motor not in self._motor_thresholds:
                gcmd.respond_info(
                    "stallguard_monitor: unknown motor '{}'; configured: {}".format(
                        motor, ', '.join(self.motor_names)))
                return
            self._motor_thresholds[motor] = threshold
            gcmd.respond_info(
                "stallguard_monitor: threshold for {} -> {}".format(motor, threshold))
        else:
            # Apply to all motors
            self.collision_threshold = threshold
            for m in self._motor_thresholds:
                self._motor_thresholds[m] = threshold
            gcmd.respond_info(
                "stallguard_monitor: all thresholds -> {}".format(threshold))
        self._collision_latch = False
        self._reset_windows()

    def cmd_SG_RESET(self, gcmd):
        """Clear the collision latch so monitoring re-arms without restart."""
        self._collision_latch = False
        self._reset_windows()
        gcmd.respond_info("stallguard_monitor: collision latch cleared")

    def cmd_SG_DIAG(self, gcmd):
        """Probe the first configured TMC driver and report what is readable."""
        if not self.tmc_drivers:
            gcmd.respond_info("stallguard_monitor: no drivers configured")
            return

        motor     = next(iter(self.tmc_drivers))
        tmc       = self.tmc_drivers[motor]
        et        = self.reactor.monotonic()
        names     = self._reg_names.get(motor, {})
        reg_name  = names.get('reg')
        sg_name   = names.get('sg')
        stst_name = names.get('stst')
        lines = [
            "SG_DIAG for '{}' ({})".format(motor, type(tmc).__name__),
            "  probed: reg={}  sg={}  stst={}".format(
                reg_name  or 'NOT FOUND',
                sg_name   or 'NOT FOUND',
                stst_name or 'NOT FOUND'),
        ]
        if hasattr(tmc, 'fields') and hasattr(tmc.fields, 'all_fields'):
            lines.append("  all_fields keys: {}".format(
                sorted(tmc.fields.all_fields.keys())))

        # fields.get_reg (uses probed register name)
        if hasattr(tmc, 'fields'):
            lines.append("  has fields: yes  (type={})".format(type(tmc.fields).__name__))
            if hasattr(tmc.fields, 'get_reg'):
                if reg_name:
                    try:
                        v = tmc.fields.get_reg(reg_name)
                        lines.append("  fields.get_reg('{}') -> {} ({})".format(
                            reg_name, v, type(v).__name__))
                    except Exception as e:
                        lines.append("  fields.get_reg('{}') raised: {}".format(reg_name, e))
                else:
                    lines.append("  fields.get_reg: probed reg name not available")
            else:
                lines.append("  fields.get_reg: NOT PRESENT")
            # fields.get_field uses probed field names (lowercase on TMC5160)
            if hasattr(tmc.fields, 'get_field'):
                for fname in (sg_name, stst_name):
                    if not fname:
                        continue
                    try:
                        v = tmc.fields.get_field(fname)
                        lines.append("  fields.get_field('{}') -> {} ({})".format(
                            fname, v, type(v).__name__))
                    except Exception as e:
                        lines.append("  fields.get_field('{}') raised: {}".format(fname, e))
            else:
                lines.append("  fields.get_field: NOT PRESENT")
        else:
            lines.append("  has fields: NO")

        # get_status — drv_status is None at idle (only populated by async
        # temperature/error monitoring cycle, not on every status call)
        for call_args in [(et,), ()]:
            try:
                st = tmc.get_status(*call_args)
                lines.append("  get_status{} keys: {}".format(
                    call_args, sorted(st.keys())))
                if 'drv_status' in st:
                    v = st['drv_status']
                    if v is None:
                        lines.append(
                            "  drv_status -> None  "
                            "(normal at idle — async TMC monitoring cycle not yet run; "
                            "poll code uses mcu_tmc.get_register instead)")
                    else:
                        lines.append("  drv_status -> {} ({})".format(v, type(v).__name__))
                break
            except TypeError:
                continue
            except Exception as e:
                lines.append("  get_status{} raised: {}".format(call_args, e))
                break

        # mcu_tmc.get_register — uses probed register name; this is path-1 in
        # _read_drv_status and is what the poll callback actually uses
        if hasattr(tmc, 'mcu_tmc'):
            lines.append("  has mcu_tmc: yes  (type={})".format(type(tmc.mcu_tmc).__name__))
            if hasattr(tmc.mcu_tmc, 'get_register'):
                if reg_name:
                    try:
                        v = tmc.mcu_tmc.get_register(reg_name)
                        if isinstance(v, dict):
                            resp = v.get('response', [])
                            lines.append("  mcu_tmc.get_register('{}') -> dict, "
                                         "response={}".format(reg_name, list(resp)))
                            if len(resp) >= 5:
                                raw = sum(b << ((3 - i) * 8)
                                          for i, b in enumerate(bytearray(resp[1:5])))
                                lines.append("  live read: SG_RESULT={}  STST={}".format(
                                    raw & _SG_RESULT_MASK, (raw >> 31) & 1))
                        elif isinstance(v, int):
                            lines.append("  mcu_tmc.get_register('{}') -> 0x{:08X}".format(
                                reg_name, v))
                            lines.append("  live read: SG_RESULT={}  STST={}".format(
                                v & _SG_RESULT_MASK, (v >> 31) & 1))
                        else:
                            lines.append("  mcu_tmc.get_register('{}') -> {} ({})".format(
                                reg_name, v, type(v).__name__))
                    except Exception as e:
                        lines.append("  mcu_tmc.get_register('{}') raised: {}".format(
                            reg_name, e))
                else:
                    lines.append("  mcu_tmc.get_register: probed reg name not available")
            else:
                lines.append("  mcu_tmc.get_register: NOT PRESENT")
        else:
            lines.append("  has mcu_tmc: NO")

        # Toolhead velocity availability (needed for min_speed_mm_s gating)
        if self._toolhead is not None:
            try:
                st = self._toolhead.get_status(self.reactor.monotonic())
                v  = st.get('velocity')
                if v is not None:
                    lines.append("  toolhead velocity: {:.1f} mm/s (gating available)".format(
                        abs(float(v))))
                else:
                    lines.append(
                        "  toolhead velocity: NOT in status \u2014 min_speed_mm_s gating "
                        "will be skipped (set min_speed_mm_s: 0)")
            except Exception as e:
                lines.append("  toolhead velocity: ERROR ({})".format(e))
        else:
            lines.append("  toolhead: not found")

        gcmd.respond_info("\n".join(lines))

    def cmd_SG_CALIBRATE(self, gcmd):
        """Move each axis and report baseline SG statistics."""
        dist  = gcmd.get_float('DISTANCE', self.calibrate_distance, minval=5., maxval=200.)
        speed = gcmd.get_float('SPEED', self.calibrate_speed, minval=5., maxval=300.)

        toolhead = self.printer.lookup_object('toolhead')
        curpos   = list(toolhead.get_position())

        # Group configured motors by axis index (X=0, Y=1, Z=2)
        def _axis_of(name):
            n = name.lower()
            if '_x' in n or n.endswith('x'): return 0
            if '_y' in n or n.endswith('y'): return 1
            if '_z' in n or n.endswith('z'): return 2
            return None

        axis_motors = {}
        for motor in self.tmc_drivers:
            ax = _axis_of(motor)
            if ax is not None:
                axis_motors.setdefault(ax, []).append(motor)
            else:
                gcmd.respond_info(
                    "SG_CALIBRATE: cannot map '{}' to X/Y/Z, skipping".format(motor))

        if not axis_motors:
            gcmd.respond_info("SG_CALIBRATE: no motors with recognisable axis names")
            return

        was_monitoring = self.monitoring
        if not was_monitoring:
            self._start_monitoring()

        results = {}
        for ax_idx in sorted(axis_motors):
            motors    = axis_motors[ax_idx]
            axis_name = 'XYZ'[ax_idx]

            for m in motors:
                self._calibrate_samples[m] = []
            self._calibrating = True

            gcmd.respond_info(
                "SG_CALIBRATE: {} +{:.0f}mm then -{:.0f}mm @ {:.0f}mm/s".format(
                    axis_name, dist, dist, speed))
            try:
                fwd = list(curpos)
                fwd[ax_idx] += dist
                toolhead.move(fwd, speed)
                toolhead.wait_moves()
                toolhead.move(curpos, speed)
                toolhead.wait_moves()
            except Exception as exc:
                self._calibrating = False
                gcmd.respond_info(
                    "SG_CALIBRATE: move failed on {}: {}".format(axis_name, exc))
                continue

            self._calibrating = False
            for m in motors:
                results[m] = list(self._calibrate_samples.get(m, []))

        if not was_monitoring:
            self._stop_monitoring()

        # ── Report ──────────────────────────────────────────────────────────
        hdr = "── SG Calibration  distance={:.0f}mm  speed={:.0f}mm/s ──".format(
            dist, speed)
        col = "  {:<18} {:>6}  {:>6}  {:>6}  {:>6}  {:>6}  {:>9}  {}".format(
            "motor", "mean", "stddev", "min", "max", "n", "threshold", "margin / note")
        rows = [hdr, col]
        for motor in self.motor_names:
            if motor not in results:
                continue
            samples = [s for s in results[motor] if s > 0]
            thresh  = self._motor_thresholds.get(motor, self.collision_threshold)
            if not samples:
                rows.append("  {:<18} {:>6}  {:>6}  {:>6}  {:>6}  {:>6}  {:>9}  no data".format(
                    motor, '-', '-', '-', '-', 0, thresh))
                continue
            mean   = sum(samples) / len(samples)
            stddev = math.sqrt(sum((s - mean) ** 2 for s in samples) / len(samples))
            margin = mean - thresh
            if margin < 0:
                note = "!! THRESHOLD ABOVE BASELINE — will always trigger"
            elif margin < 15:
                note = "WARN: margin < 15, consider lowering threshold"
            else:
                note = "OK"
            rows.append(
                "  {:<18} {:>6.1f}  {:>6.1f}  {:>6}  {:>6}  {:>6}  {:>9}  {:.1f}  {}".format(
                    motor, mean, stddev, min(samples), max(samples),
                    len(samples), thresh, margin, note))
        gcmd.respond_info("\n".join(rows))

    def cmd_SG_TRACE_START(self, gcmd):
        """Start (or restart) CSV motion trace logging."""
        default = self.trace_file or '/tmp/stallguard_trace.csv'
        path = gcmd.get('FILE', default)
        self._open_trace_file(path)
        if self._trace_writer is not None:
            gcmd.respond_info(
                "stallguard_monitor: trace logging -> {}  "
                "(SG_TRACE_STOP to stop)".format(path))
        else:
            gcmd.respond_info(
                "stallguard_monitor: trace FAILED -- check path/permissions: {}".format(path))

    def cmd_SG_TRACE_STOP(self, gcmd):
        """Stop CSV motion trace logging and close the file."""
        if self._trace_fh is not None:
            try:
                self._trace_fh.flush()
                self._trace_fh.close()
            except Exception:
                pass
            self._trace_fh = None
            self._trace_writer = None
            gcmd.respond_info("stallguard_monitor: trace stopped")
        else:
            gcmd.respond_info("stallguard_monitor: trace not active")

    def get_status(self, eventtime):
        """Expose values to Moonraker / macros via printer['stallguard_monitor']."""
        return {
            'enabled':             self.monitoring,
            'homing_only':         self.homing_only,
            'homing_active':       self._homing_active,
            'collision_threshold': self.collision_threshold,
            'motor_thresholds':    dict(self._motor_thresholds),
            'collision_detected':  self._collision_latch,
            'trace_active':        self._trace_fh is not None,
            'sg_values':           dict(self.sg_values),
            'sg_zero_counts':      dict(self._sg_zero_counts),
        }


def load_config(config):
    return StallGuardMonitor(config)
