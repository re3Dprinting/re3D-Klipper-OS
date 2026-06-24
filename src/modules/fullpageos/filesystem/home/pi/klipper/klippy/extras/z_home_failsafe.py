# z_home_failsafe.py
#
# Z-homing crash failsafe via TMC StallGuard.
#
# PURPOSE
# -------
# Protect against a single failure mode: the Z endstop / probe fails to trigger
# (stuck switch, disconnected wire, probe not deploying, etc.) so Klipper keeps
# driving Z until the bounded homing move finishes — grinding the axis into a
# hard stop the whole way.  This module watches the Z stepper(s) StallGuard load
# ONLY while Z is homing.  When the rotor is physically blocked (a hard stop)
# SG_RESULT collapses to ~0 while the driver is still being commanded to step
# (STST = 0).  That signature fires an immediate emergency stop, cutting motor
# power instead of grinding for the full homing distance.
#
# Why it does not false-fire on a NORMAL home:
#   * On a normal home the endstop trips and Klipper stops stepping, so the
#     driver reaches standstill (STST = 1) within ~1-2 full-step periods.  The
#     standstill flag clears the detection window before it can fire.
#   * SG_RESULT and STST come from the SAME DRV_STATUS register read, so they
#     are perfectly time-aligned — there is no velocity-feed lag to fool it.
#   * A real crash keeps Klipper sending step pulses (it still thinks it must
#     reach the endstop), so STST stays 0 and the low-SG streak survives.
#
# This is a FAILSAFE only — your real Z endstop/probe still performs homing.
#
# Per-driver requirements (in each [tmc5160 stepper_z] / [tmc5160 stepper_z1]):
#   driver_SGT:       0    # StallGuard sensitivity (-64..63, lower = sensitive)
#   driver_SFILT:     1    # 4-full-step hardware filtering (smoother SG)
#   driver_TCOOLTHRS: 500  # ensure StallGuard is active at homing speed

import logging
from collections import deque

_TMC_TYPES = [
    'tmc2130', 'tmc2160', 'tmc5160', 'tmc2208', 'tmc2209',
    'tmc2240', 'tmc2660', 'tmc5031',
]

# SG_RESULT lives in bits[9:0] of DRV_STATUS (0 = max load / blocked rotor).
_SG_RESULT_MASK = 0x000003FF
# STST (standstill) is bit[31] of DRV_STATUS (1 = no step pulses for 2^20 tCLK).
_STST_BIT = 1 << 31


class ZHomeFailsafe:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode   = self.printer.lookup_object('gcode')
        self.logger  = logging.getLogger('z_home_failsafe')

        # Z steppers to watch.  List every Z motor section name.
        motors = config.get('motors', 'stepper_z')
        self.motor_names = [m.strip() for m in motors.split(',') if m.strip()]
        self.motor_set   = set(self.motor_names)

        # How often to read DRV_STATUS while Z is homing (seconds).
        self.poll_interval = config.getfloat(
            'poll_interval', 0.01, minval=0.002, maxval=0.5)

        # SG_RESULT at/under this = blocked rotor.  Keep it LOW (10-30); normal
        # homing descent reads well above it, only a hard stop reaches it.
        self.block_threshold = config.getint(
            'block_threshold', 20, minval=0, maxval=1023)

        # Consecutive below-threshold, still-stepping samples required to fire.
        # Must outlast a normal endstop stop (which sets STST and clears the
        # window).  12 x 0.01 s = 120 ms; a real crash holds low SG for the
        # whole remaining homing distance, so this is trivially satisfied.
        self.consecutive_triggers = config.getint(
            'consecutive_triggers', 12, minval=1, maxval=200)

        # Samples ignored at the very start of the homing move while the axis
        # accelerates up to homing speed (SG is unreliable below cruise).
        self.start_blank_samples = config.getint(
            'start_blank_samples', 15, minval=0, maxval=500)

        self.action = config.getchoice('action', {
            'none':           'none',
            'm117':           'm117',
            'pause':          'pause',
            'emergency_stop': 'emergency_stop',
        }, 'emergency_stop')

        self.enabled = config.getboolean('enabled', True)

        # Optional per-motor threshold overrides.
        self._thresholds = {}
        for m in self.motor_names:
            self._thresholds[m] = config.getint(
                'block_threshold_%s' % m, self.block_threshold,
                minval=0, maxval=1023)

        # Runtime state.
        self.tmc_drivers   = {}    # motor -> tmc object
        self._reg_names    = {}    # motor -> {reg, sg, stst, stst_reg}
        self._windows      = {}    # motor -> deque(maxlen=consecutive_triggers)
        self.sg_values     = {}    # motor -> last SG_RESULT (diagnostics)
        self._timer_handle = None
        self._armed        = False   # currently inside a Z homing move
        self._latched      = False   # fired this homing move
        self._sample_count = 0

        self.printer.register_event_handler('klippy:ready', self._handle_ready)

        self.gcode.register_command(
            'ZFAILSAFE_STATUS', self.cmd_STATUS,
            desc="Show Z-home failsafe configuration and last SG values")
        self.gcode.register_command(
            'ZFAILSAFE_ENABLE', self.cmd_ENABLE,
            desc="Enable the Z-home crash failsafe")
        self.gcode.register_command(
            'ZFAILSAFE_DISABLE', self.cmd_DISABLE,
            desc="Disable the Z-home crash failsafe")
        self.gcode.register_command(
            'ZFAILSAFE_SET_THRESHOLD', self.cmd_SET_THRESHOLD,
            desc="Set block_threshold (THRESHOLD=N [MOTOR=stepper_z])")
        self.gcode.register_command(
            'ZFAILSAFE_RESET', self.cmd_RESET,
            desc="Clear the failsafe latch after a non-shutdown trigger")

    # -- Initialisation --------------------------------------------------------

    def _handle_ready(self):
        for m in self.motor_names:
            tmc = self._find_tmc_driver(m)
            if tmc is None:
                self.logger.error(
                    "z_home_failsafe: no TMC driver found for '%s' - skipping", m)
                continue
            self.tmc_drivers[m] = tmc
            self.sg_values[m]   = None
            self._windows[m]    = deque(maxlen=self.consecutive_triggers)
            self._reg_names[m]  = self._probe_names(tmc)

        if not self.tmc_drivers:
            self.logger.error(
                "z_home_failsafe: no Z drivers found; failsafe disabled")
            return

        self.printer.register_event_handler(
            "homing:home_rails_begin", self._handle_homing_begin)
        self.printer.register_event_handler(
            "homing:home_rails_end", self._handle_homing_end)

        self.logger.info(
            "z_home_failsafe ready: motors=%s action=%s threshold=%d "
            "consecutive=%d poll=%.3fs enabled=%s",
            list(self.tmc_drivers), self.action, self.block_threshold,
            self.consecutive_triggers, self.poll_interval, self.enabled)

    def _handle_homing_begin(self, homing_state, rails):
        """Arm the failsafe only when this homing move includes a Z stepper."""
        if not self.enabled:
            return
        if not self._rails_include_z(rails):
            return
        self._armed        = True
        self._latched      = False
        self._sample_count = 0
        for w in self._windows.values():
            w.clear()
        self._start_timer()
        self.logger.info("z_home_failsafe: Z homing started - failsafe armed")

    def _handle_homing_end(self, homing_state, rails):
        if not self._armed:
            return
        self._armed = False
        self._stop_timer()
        for w in self._windows.values():
            w.clear()
        self.logger.info("z_home_failsafe: Z homing complete - failsafe disarmed")

    def _rails_include_z(self, rails):
        try:
            for rail in rails:
                for stepper in rail.get_steppers():
                    if stepper.get_name() in self.motor_set:
                        return True
        except Exception:
            self.logger.exception("z_home_failsafe: error inspecting homing rails")
        return False

    # -- Timer control ---------------------------------------------------------

    def _start_timer(self):
        if self._timer_handle is not None:
            return
        self._timer_handle = self.reactor.register_timer(
            self._poll_callback, self.reactor.NOW)

    def _stop_timer(self):
        if self._timer_handle is not None:
            self.reactor.unregister_timer(self._timer_handle)
            self._timer_handle = None

    # -- Polling ---------------------------------------------------------------

    def _poll_callback(self, eventtime):
        if not self._armed or self._latched:
            return self.reactor.NEVER
        try:
            self._sample_count += 1
            blanking = self._sample_count <= self.start_blank_samples
            for motor, tmc in self.tmc_drivers.items():
                raw = self._read_drv_status(tmc, motor, eventtime)
                if raw is None:
                    continue
                sg   = raw & _SG_RESULT_MASK
                stst = bool(raw & _STST_BIT)
                self.sg_values[motor] = sg
                window = self._windows[motor]

                # Standstill -> not stepping (normal endstop stop or idle): the
                # decisive discriminator.  Clear any in-progress streak.
                if stst:
                    window.clear()
                    continue
                # Skip the initial acceleration ramp where SG is unreliable.
                if blanking:
                    window.clear()
                    continue
                thr = self._thresholds.get(motor, self.block_threshold)
                if sg >= thr:
                    # Moving normally toward the endstop.
                    window.clear()
                    continue
                # Below threshold while still being commanded to step -> the
                # rotor is blocked (hard stop) and the endstop never tripped.
                window.append(sg)
                if len(window) >= window.maxlen:
                    avg = int(round(sum(window) / len(window)))
                    window.clear()
                    self._trigger(motor, avg)
                    return self.reactor.NEVER
        except Exception:
            self.logger.exception("z_home_failsafe: error in poll callback")
        return eventtime + self.poll_interval

    # -- Trigger ---------------------------------------------------------------

    def _trigger(self, motor, sg_val):
        self._latched = True
        self._stop_timer()
        thr = self._thresholds.get(motor, self.block_threshold)
        msg = ("!! z_home_failsafe: Z CRASH during homing on {} "
               "(SG_RESULT={}, threshold={}) - endstop/probe failed to trigger"
               .format(motor, sg_val, thr))
        self.logger.error(msg)

        if self.action == 'emergency_stop':
            self.printer.invoke_async_shutdown(msg)
        elif self.action == 'pause':
            self.reactor.register_async_callback(
                lambda e: self.gcode.run_script("PAUSE\nM118 " + msg))
        elif self.action == 'm117':
            disp = "Z CRASH {} SG={}".format(motor, sg_val)
            self.reactor.register_async_callback(
                lambda e: self.gcode.run_script(
                    "M117 " + disp + "\nM118 " + msg))
        # 'none' -> log only

    # -- TMC discovery / register read -----------------------------------------

    def _find_tmc_driver(self, motor_name):
        for tmc_type in _TMC_TYPES:
            obj_name = "{} {}".format(tmc_type, motor_name)
            try:
                return self.printer.lookup_object(obj_name)
            except Exception:
                pass
        return None

    def _probe_names(self, tmc):
        """Discover this build's StallGuard / standstill field+register names."""
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

    def _read_drv_status(self, tmc, motor, eventtime):
        """Return DRV_STATUS (SG in bits[9:0], STST in bit[31]) or None."""
        names     = self._reg_names.get(motor, {})
        reg_name  = names.get('reg')
        sg_name   = names.get('sg')
        stst_name = names.get('stst')

        # 1) Fresh direct register read (SPI/UART).
        if reg_name and hasattr(tmc, 'mcu_tmc') and hasattr(tmc.mcu_tmc, 'get_register'):
            try:
                val = tmc.mcu_tmc.get_register(reg_name)
                if isinstance(val, int):
                    return val
                if isinstance(val, dict):
                    resp = val.get('response', [])
                    if len(resp) >= 5:
                        data = bytearray(resp[1:5])
                        return sum(b << ((3 - i) * 8) for i, b in enumerate(data))
            except Exception as e:
                self.logger.debug("z_home_failsafe: get_register(%s): %s", reg_name, e)

        # 2) Reconstruct from cached fields (only trust a non-zero SG).
        if sg_name and stst_name and hasattr(tmc, 'fields') and hasattr(tmc.fields, 'get_field'):
            try:
                sg   = tmc.fields.get_field(sg_name)
                stst = tmc.fields.get_field(stst_name)
                if isinstance(sg, int) and isinstance(stst, int) and sg > 0:
                    return (stst << 31) | (sg & _SG_RESULT_MASK)
            except Exception as e:
                self.logger.debug("z_home_failsafe: get_field(%s/%s): %s", sg_name, stst_name, e)

        # 3) Klipper field cache for the register.
        if reg_name and hasattr(tmc, 'fields') and hasattr(tmc.fields, 'get_reg'):
            try:
                val = tmc.fields.get_reg(reg_name)
                if isinstance(val, int):
                    return val
            except Exception as e:
                self.logger.debug("z_home_failsafe: fields.get_reg(%s): %s", reg_name, e)

        # 4) get_status() drv_status (last resort).
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

    # -- GCode commands --------------------------------------------------------

    def cmd_STATUS(self, gcmd):
        if not self.tmc_drivers:
            gcmd.respond_info("z_home_failsafe: no Z drivers configured")
            return
        state = ("ARMED" if self._armed else "disarmed")
        if self._latched:
            state += " (LATCHED)"
        lines = [
            "Z-Home Failsafe  enabled={}  state={}  action={}".format(
                self.enabled, state, self.action),
            "  threshold={}  consecutive={}  start_blank={}  poll={:.3f}s".format(
                self.block_threshold, self.consecutive_triggers,
                self.start_blank_samples, self.poll_interval),
            "  {:<16} {:>10}  {:>9}".format("motor", "SG_RESULT", "threshold"),
        ]
        for m in self.tmc_drivers:
            sg = self.sg_values.get(m)
            lines.append("  {:<16} {:>10}  {:>9}".format(
                m, "n/a" if sg is None else sg,
                self._thresholds.get(m, self.block_threshold)))
        gcmd.respond_info("\n".join(lines))

    def cmd_ENABLE(self, gcmd):
        self.enabled = True
        gcmd.respond_info("z_home_failsafe: enabled")

    def cmd_DISABLE(self, gcmd):
        self.enabled = False
        if self._armed:
            self._armed = False
            self._stop_timer()
        gcmd.respond_info("z_home_failsafe: disabled")

    def cmd_SET_THRESHOLD(self, gcmd):
        thr   = gcmd.get_int('THRESHOLD', minval=0, maxval=1023)
        motor = gcmd.get('MOTOR', None)
        if motor is not None:
            if motor not in self.motor_set:
                gcmd.respond_info(
                    "z_home_failsafe: unknown motor '{}'".format(motor))
                return
            self._thresholds[motor] = thr
            gcmd.respond_info(
                "z_home_failsafe: {} block_threshold={}".format(motor, thr))
        else:
            self.block_threshold = thr
            for m in self.motor_names:
                self._thresholds[m] = thr
            gcmd.respond_info(
                "z_home_failsafe: block_threshold={} (all motors)".format(thr))

    def cmd_RESET(self, gcmd):
        self._latched = False
        for w in self._windows.values():
            w.clear()
        gcmd.respond_info("z_home_failsafe: latch cleared")

    def get_status(self, eventtime):
        return {
            'enabled':         self.enabled,
            'armed':           self._armed,
            'latched':         self._latched,
            'action':          self.action,
            'block_threshold': self.block_threshold,
            'sg_values':       dict(self.sg_values),
        }


def load_config(config):
    return ZHomeFailsafe(config)
