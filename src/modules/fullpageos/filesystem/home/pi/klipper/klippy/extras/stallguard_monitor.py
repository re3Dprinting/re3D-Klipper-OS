# stallguard_monitor.py
#
# Klipper extra: monitor TMC StallGuard values during motion to detect
# collisions and abnormal load before damage occurs.
#
# Reads DRVSTATUS.SG_RESULT directly from each configured TMC driver on a
# fast reactor timer.  When a motor is moving (DRVSTATUS.STST == 0) and
# SG_RESULT stays below `collision_threshold` for `consecutive_triggers`
# consecutive polls the configured action is executed.
#
# GCode commands exposed:
#   SG_STATUS                     - print current SG_RESULT for all motors
#   SG_START                      - enable monitoring
#   SG_STOP                       - disable monitoring
#   SG_SET_THRESHOLD THRESHOLD=N  - change threshold at runtime
#   SG_RESET                      - clear collision latch so printing can resume
#
# Place this file at ~/klipper/klippy/extras/stallguard_monitor.py
# Add [stallguard_monitor] to printer.cfg (see stallguard_monitor.cfg)

import logging
import math

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
            'consecutive_triggers', 3, minval=1, maxval=50)
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

        # ── Runtime state ────────────────────────────────────────────────────
        self.tmc_drivers        = {}   # motor_name -> tmc object
        self.sg_values          = {}   # motor_name -> last SG_RESULT int or None
        self.trigger_counts     = {}   # motor_name -> consecutive below-threshold count
        self._reg_names         = {}   # motor_name -> {reg, sg, stst, stst_reg}
        self.monitoring         = False
        self._timer_handle      = None
        self._collision_latch   = False  # set on first collision; cleared by SG_RESET
        self._homing_active     = False  # True while G28 is running
        self._calibrating       = False  # True during SG_CALIBRATE move
        self._calibrate_samples = {}     # motor -> [sg_val, ...] during calibration

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

    # ── Initialisation ────────────────────────────────────────────────────────

    def _handle_ready(self):
        """Discover TMC driver objects once the printer is fully ready."""
        for motor in self.motor_names:
            tmc = self._find_tmc_driver(motor)
            if tmc is not None:
                self.tmc_drivers[motor]    = tmc
                self.sg_values[motor]      = None
                self.trigger_counts[motor] = 0
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

        if self.enabled and not self.homing_only:
            self._start_monitoring()

    def _handle_homing_begin(self, homing_state, rails):
        """Auto-arm monitoring at the start of any G28 homing move."""
        if not self.enabled:
            return
        self._homing_active   = True
        self._collision_latch = False
        for motor in self.trigger_counts:
            self.trigger_counts[motor] = 0
        if not self.monitoring:
            self._start_monitoring()
        self.logger.info("stallguard_monitor: homing started — monitoring armed")

    def _handle_homing_end(self, homing_state, rails):
        """Disarm monitoring when G28 homing completes."""
        self._homing_active = False
        if self.homing_only:
            self._stop_monitoring()
            self._collision_latch = False
            for motor in self.trigger_counts:
                self.trigger_counts[motor] = 0
            self.logger.info("stallguard_monitor: homing complete — monitoring disarmed")

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
                         "(threshold=%d, action=%s, poll=%.2fs)",
                         self.collision_threshold, self.action, self.poll_interval)

    def _stop_monitoring(self):
        self.monitoring = False
        if self._timer_handle is not None:
            self.reactor.unregister_timer(self._timer_handle)
            self._timer_handle = None
        self.logger.info("stallguard_monitor: monitoring stopped")

    # ── Polling ───────────────────────────────────────────────────────────────

    def _poll_callback(self, eventtime):
        if not self.monitoring:
            return self.reactor.NEVER

        try:
            for motor, tmc in self.tmc_drivers.items():
                raw = self._read_drv_status(tmc, motor, eventtime)
                if raw is None:
                    continue

                sg_val      = raw & _SG_RESULT_MASK
                standstill  = bool(raw & _STST_BIT)

                if standstill:
                    # Motor is stopped – reset counter, no collision possible.
                    # Do NOT overwrite sg_values with standstill 0; keep the
                    # last known movement value so displays stay meaningful.
                    self.trigger_counts[motor] = 0
                    continue

                # SG_RESULT=0 is ambiguous: it can mean the motor is below the
                # TMC's minimum velocity for a valid SG measurement (which
                # occurs during deceleration BEFORE the STST bit latches), or
                # a fully-stalled motor.  Treat it as "no data": do not
                # increment the trigger counter, but also do not reset it.
                # A real mechanical stall drops through positive low values
                # before hitting 0, so threshold detection still fires.
                if sg_val == 0:
                    continue

                self.sg_values[motor] = sg_val

                # Collect samples for SG_CALIBRATE if active
                if self._calibrating and motor in self._calibrate_samples:
                    self._calibrate_samples[motor].append(sg_val)

                if self._collision_latch:
                    # Already triggered; wait for SG_RESET before re-arming
                    continue

                threshold = self._motor_thresholds.get(motor, self.collision_threshold)
                if sg_val < threshold:
                    self.trigger_counts[motor] += 1
                    if self.trigger_counts[motor] >= self.consecutive_triggers:
                        self._handle_collision(motor, sg_val)
                else:
                    self.trigger_counts[motor] = 0

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
        if sg_name and stst_name and hasattr(tmc, 'fields') and hasattr(tmc.fields, 'get_field'):
            try:
                sg   = tmc.fields.get_field(sg_name)
                stst = tmc.fields.get_field(stst_name)
                if isinstance(sg, int) and isinstance(stst, int):
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
        self._collision_latch = True
        # Reset all counters so we don't spam the action
        for m in self.trigger_counts:
            self.trigger_counts[m] = 0

        msg = ("!! stallguard_monitor: COLLISION on {} "
               "(SG_RESULT={}, threshold={})".format(
                   motor, sg_val,
                   self._motor_thresholds.get(motor, self.collision_threshold)))
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
            flag  = " <<< LOW" if (sg_val < thresh and not standstill) else ""
            state = "standstill" if standstill else "moving"
            lines.append("  {:<16} {:>10}  {:>9}  {}{}".format(
                motor, sg_val, thresh, state, flag))

        gcmd.respond_info("\n".join(lines))

    def cmd_SG_START(self, gcmd):
        self._collision_latch = False
        for m in self.trigger_counts:
            self.trigger_counts[m] = 0
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
        for m in self.trigger_counts:
            self.trigger_counts[m] = 0

    def cmd_SG_RESET(self, gcmd):
        """Clear the collision latch so monitoring re-arms without restart."""
        self._collision_latch = False
        for m in self.trigger_counts:
            self.trigger_counts[m] = 0
        gcmd.respond_info("stallguard_monitor: collision latch cleared")

    def cmd_SG_DIAG(self, gcmd):
        """Probe the first configured TMC driver and report what is readable."""
        if not self.tmc_drivers:
            gcmd.respond_info("stallguard_monitor: no drivers configured")
            return

        motor = next(iter(self.tmc_drivers))
        tmc   = self.tmc_drivers[motor]
        et    = self.reactor.monotonic()
        names = self._reg_names.get(motor, {})
        lines = [
            "SG_DIAG for '{}' ({})".format(motor, type(tmc).__name__),
            "  probed: reg={reg}  sg={sg}  stst={stst}".format(
                reg=names.get('reg',  'NOT FOUND'),
                sg=names.get('sg',   'NOT FOUND'),
                stst=names.get('stst','NOT FOUND')),
        ]
        if hasattr(tmc, 'fields') and hasattr(tmc.fields, 'all_fields'):
            all_regs = sorted(tmc.fields.all_fields.keys())
            lines.append("  all_fields keys: {}".format(all_regs))

        # fields.get_reg
        if hasattr(tmc, 'fields'):
            lines.append("  has fields: yes  (type={})".format(type(tmc.fields).__name__))
            if hasattr(tmc.fields, 'get_reg'):
                try:
                    v = tmc.fields.get_reg("DRVSTATUS")
                    lines.append("  fields.get_reg('DRVSTATUS') -> {} ({})".format(v, type(v).__name__))
                except Exception as e:
                    lines.append("  fields.get_reg raised: {}".format(e))
            else:
                lines.append("  fields.get_reg: NOT PRESENT")
            if hasattr(tmc.fields, 'get_field'):
                for fname in ("SG_RESULT", "STST"):
                    try:
                        v = tmc.fields.get_field(fname)
                        lines.append("  fields.get_field('{}') -> {} ({})".format(fname, v, type(v).__name__))
                    except Exception as e:
                        lines.append("  fields.get_field('{}') raised: {}".format(fname, e))
            else:
                lines.append("  fields.get_field: NOT PRESENT")
        else:
            lines.append("  has fields: NO")

        # get_status
        for call_args in [(et,), ()]:
            try:
                st = tmc.get_status(*call_args)
                lines.append("  get_status{} keys: {}".format(
                    call_args, sorted(st.keys())))
                if 'drv_status' in st:
                    v = st['drv_status']
                    lines.append("  drv_status -> {} ({})".format(v, type(v).__name__))
                break
            except TypeError:
                continue
            except Exception as e:
                lines.append("  get_status{} raised: {}".format(call_args, e))
                break

        # mcu_tmc.get_register
        if hasattr(tmc, 'mcu_tmc'):
            lines.append("  has mcu_tmc: yes  (type={})".format(type(tmc.mcu_tmc).__name__))
            if hasattr(tmc.mcu_tmc, 'get_register'):
                try:
                    v = tmc.mcu_tmc.get_register("DRVSTATUS")
                    if isinstance(v, dict):
                        resp = v.get('response', [])
                        lines.append("  mcu_tmc.get_register -> dict, response={}".format(list(resp)))
                    else:
                        lines.append("  mcu_tmc.get_register -> {} ({})".format(v, type(v).__name__))
                except Exception as e:
                    lines.append("  mcu_tmc.get_register raised: {}".format(e))
            else:
                lines.append("  mcu_tmc.get_register: NOT PRESENT")
        else:
            lines.append("  has mcu_tmc: NO")

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

    def get_status(self, eventtime):
        """Expose values to Moonraker / macros via printer['stallguard_monitor']."""
        return {
            'enabled':             self.monitoring,
            'homing_only':         self.homing_only,
            'homing_active':       self._homing_active,
            'collision_threshold': self.collision_threshold,
            'motor_thresholds':    dict(self._motor_thresholds),
            'collision_detected':  self._collision_latch,
            'sg_values':           dict(self.sg_values),
        }


def load_config(config):
    return StallGuardMonitor(config)
