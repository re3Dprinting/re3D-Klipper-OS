#!/usr/bin/env python3
"""
Patch script to add non-critical MCU support to vanilla Klipper.

Ported from Kalico PR #339: https://github.com/KalicoCrew/kalico/pull/339
Original authors: bwnance, rogerlz

This allows marking secondary MCUs (e.g. USB accelerometers, USB probes) as
non-critical so they can disconnect/reconnect without halting the printer.

Usage:
    python3 patch_noncritical_mcu.py [--klipper-dir /path/to/klipper] [--revert]

After patching, add 'is_non_critical: True' to any secondary [mcu xxx] section
in your printer.cfg. The primary [mcu] cannot be non-critical.
"""

import argparse
import os
import re
import shutil
import sys

BACKUP_SUFFIX = ".noncritical_backup"


def find_klipper_dir():
    """Try common Klipper installation paths."""
    candidates = [
        os.path.expanduser("~/klipper"),
        "/home/pi/klipper",
        "/home/klipper/klipper",
    ]
    for c in candidates:
        if os.path.isdir(c) and os.path.isfile(os.path.join(c, "klippy", "mcu.py")):
            return c
    return None


def backup_file(filepath):
    """Create a backup of a file before modifying it."""
    backup = filepath + BACKUP_SUFFIX
    if not os.path.exists(backup):
        shutil.copy2(filepath, backup)
        print(f"  Backed up: {filepath}")


def restore_file(filepath):
    """Restore a file from its backup."""
    backup = filepath + BACKUP_SUFFIX
    if os.path.exists(backup):
        shutil.copy2(backup, filepath)
        os.remove(backup)
        print(f"  Restored: {filepath}")
        return True
    return False


def read_file(filepath):
    with open(filepath, "r") as f:
        return f.read()


def write_file(filepath, content):
    with open(filepath, "w") as f:
        f.write(content)


# ---------- Line-based helpers ----------

def find_line(lines, pattern, start=0, end=None):
    """Find first line index matching regex pattern (case-sensitive)."""
    if end is None:
        end = len(lines)
    for i in range(start, end):
        if re.search(pattern, lines[i]):
            return i
    return -1


def insert_after(lines, idx, text):
    """Insert text (string) as new lines after lines[idx]."""
    new = text.split('\n')
    # Don't add trailing empty line from split
    if new and new[-1] == '':
        new = new[:-1]
    for i, ln in enumerate(new):
        lines.insert(idx + 1 + i, ln + '\n')


def insert_before(lines, idx, text):
    """Insert text (string) as new lines before lines[idx]."""
    new = text.split('\n')
    if new and new[-1] == '':
        new = new[:-1]
    for i, ln in enumerate(new):
        lines.insert(idx + i, ln + '\n')


def replace_lines(lines, start, end, text):
    """Replace lines[start:end] with text."""
    del lines[start:end]
    new = text.split('\n')
    if new and new[-1] == '':
        new = new[:-1]
    for i, ln in enumerate(new):
        lines.insert(start + i, ln + '\n')


def read_lines(filepath):
    with open(filepath, 'r') as f:
        return f.readlines()


def write_lines(filepath, lines):
    with open(filepath, 'w') as f:
        f.writelines(lines)


# ---------- Text-based helper (for simpler patches) ----------

def apply_replacement(filepath, old_text, new_text, description=""):
    """Replace old_text with new_text in filepath. Returns True on success."""
    content = read_file(filepath)
    if old_text not in content:
        if new_text in content:
            print(f"  SKIP (already applied): {description}")
            return True
        print(f"  FAIL: Could not find expected text for: {description}")
        print(f"    File: {filepath}")
        return False
    content = content.replace(old_text, new_text, 1)
    write_file(filepath, content)
    print(f"  OK: {description}")
    return True


def line_patch(filepath, operations, description=""):
    """Apply a sequence of line-based operations.
    Each op is (action, pattern_or_idx, text_or_None).
    Returns True on success."""
    lines = read_lines(filepath)
    content_before = ''.join(lines)
    try:
        pos = 0
        for op in operations:
            action = op[0]
            if action == 'find_insert_after':
                pattern, text = op[1], op[2]
                idx = find_line(lines, pattern, pos)
                if idx == -1:
                    # Check if already applied
                    if op[2].strip().split('\n')[0].strip() in content_before:
                        print(f"  SKIP (already applied): {description}")
                        return True
                    print(f"  FAIL: pattern '{pattern}' not found for: {description}")
                    return False
                insert_after(lines, idx, text)
                pos = idx + 1
            elif action == 'find_insert_before':
                pattern, text = op[1], op[2]
                idx = find_line(lines, pattern, pos)
                if idx == -1:
                    if op[2].strip().split('\n')[0].strip() in content_before:
                        print(f"  SKIP (already applied): {description}")
                        return True
                    print(f"  FAIL: pattern '{pattern}' not found for: {description}")
                    return False
                insert_before(lines, idx, text)
                pos = idx + len(text.split('\n'))
            elif action == 'find_replace_line':
                pattern, text = op[1], op[2]
                idx = find_line(lines, pattern, pos)
                if idx == -1:
                    if op[2].strip().split('\n')[0].strip() in content_before:
                        print(f"  SKIP (already applied): {description}")
                        return True
                    print(f"  FAIL: pattern '{pattern}' not found for: {description}")
                    return False
                replace_lines(lines, idx, idx + 1, text)
                pos = idx + 1
            elif action == 'reset_pos':
                pos = 0
        write_lines(filepath, lines)
        print(f"  OK: {description}")
        return True
    except Exception as e:
        print(f"  FAIL: Exception in {description}: {e}")
        return False


# ---------- Detect Klipper architecture ----------

def is_refactored_mcu(klipper_dir):
    """Check if mcu.py uses the refactored helper-class architecture."""
    filepath = os.path.join(klipper_dir, "klippy", "mcu.py")
    content = read_file(filepath)
    return "class MCUConnectHelper" in content


# =====================================================================
# Patch functions
# =====================================================================

def patch_clocksync(klipper_dir):
    """Add disconnect() method to ClockSync."""
    filepath = os.path.join(klipper_dir, "klippy", "clocksync.py")
    print(f"\nPatching {filepath}")
    backup_file(filepath)

    return apply_replacement(
        filepath,
        "    def connect(self, serial):\n"
        "        self.serial = serial\n",
        "    def disconnect(self):\n"
        "        self.reactor.update_timer(self.get_clock_timer, self.reactor.NEVER)\n"
        "\n"
        "    def connect(self, serial):\n"
        "        self.serial = serial\n",
        "clocksync: add disconnect() method",
    )


def patch_serialhdl(klipper_dir):
    """Patch serialhdl.py for non-critical MCU support."""
    filepath = os.path.join(klipper_dir, "klippy", "serialhdl.py")
    print(f"\nPatching {filepath}")
    backup_file(filepath)
    lines = read_lines(filepath)
    content = ''.join(lines)
    ok = True

    # Detect which __init__ signature we have
    if 'def __init__(self, reactor, mcu_name=' in content:
        # New-style: (reactor, mcu_name="")
        ok &= line_patch(filepath, [
            ('find_replace_line',
             r'def __init__\(self, reactor, mcu_name=',
             '    def __init__(self, reactor, mcu_name="", mcu=None):'),
            ('find_insert_after',
             r'self\.mcu_name = mcu_name',
             '        self.mcu = mcu'),
        ], "serialhdl: add mcu parameter to __init__ (new-style)")
    elif 'def __init__(self, reactor, warn_prefix=' in content:
        # Old-style: (reactor, warn_prefix="")
        ok &= line_patch(filepath, [
            ('find_replace_line',
             r'def __init__\(self, reactor, warn_prefix=',
             '    def __init__(self, reactor, warn_prefix="", mcu=None):'),
            ('find_insert_after',
             r'self\.warn_prefix = warn_prefix',
             '        self.mcu = mcu'),
        ], "serialhdl: add mcu parameter to __init__ (old-style)")
    else:
        if 'self.mcu = mcu' in content or 'mcu=None' in content:
            print("  SKIP (already applied): serialhdl: add mcu parameter")
        else:
            print("  FAIL: Cannot find SerialReader.__init__ signature")
            ok = False

    # Re-read after modification
    filepath_content = read_file(filepath)

    # 2) Add check_connect method before connect_canbus
    if 'def check_connect(self' not in filepath_content:
        ok &= apply_replacement(
            filepath,
            "    def connect_canbus(self, canbus_uuid, canbus_nodeid, canbus_iface=\"can0\"):\n",
            "    def check_connect(self, serialport, baud, rts=True):\n"
            "        serial_dev = serial.Serial(baudrate=baud, timeout=0, exclusive=False)\n"
            "        serial_dev.port = serialport\n"
            "        serial_dev.rts = rts\n"
            "        try:\n"
            "            serial_dev.open()\n"
            "        except Exception:\n"
            "            return False\n"
            "        serial_dev.close()\n"
            "        return True\n"
            "\n"
            "    def connect_canbus(self, canbus_uuid, canbus_nodeid, canbus_iface=\"can0\"):\n",
            "serialhdl: add check_connect() method",
        )
    else:
        print("  SKIP (already applied): serialhdl: add check_connect() method")

    # Re-read
    filepath_content = read_file(filepath)

    # 3) Add reconnect guard in connect_uart
    if 'already connected' not in filepath_content:
        # Find the connect_uart while loop - handle both "while True:" and "while 1:"
        lines = read_lines(filepath)
        # Find def connect_uart
        uart_idx = find_line(lines, r'def connect_uart\(self')
        if uart_idx >= 0:
            # Find the while loop after it
            while_idx = find_line(lines, r'^\s+while\s+(True|1)\s*:', uart_idx)
            if while_idx >= 0:
                # Find the next line (should be the monotonic timeout check)
                insert_after(lines, while_idx,
                    "            if (\n"
                    "                self.serialqueue is not None\n"
                    "            ):  # if we're already connected, don't recon\n"
                    "                break")
                write_lines(filepath, lines)
                print("  OK: serialhdl: add reconnect guard in connect_uart")
            else:
                print("  FAIL: Could not find while loop in connect_uart")
                ok = False
        else:
            print("  FAIL: Could not find def connect_uart")
            ok = False
    else:
        print("  SKIP (already applied): serialhdl: add reconnect guard in connect_uart")

    # Re-read
    filepath_content = read_file(filepath)

    # 4) Add _check_noncritical_disconnected and guards on raw_send
    if '_check_noncritical_disconnected' not in filepath_content:
        lines = read_lines(filepath)
        # Find "# Command sending" or "def raw_send"
        raw_idx = find_line(lines, r'def raw_send\(self, cmd, minclock, reqclock, cmd_queue\)')
        if raw_idx >= 0:
            # Insert the check method and guard before raw_send's body
            # First insert the helper method before "# Command sending" or raw_send
            comment_idx = find_line(lines, r'# Command sending', max(0, raw_idx - 3))
            if comment_idx >= 0 and comment_idx < raw_idx:
                insert_point = comment_idx
            else:
                insert_point = raw_idx
            insert_before(lines, insert_point,
                "    def _check_noncritical_disconnected(self):\n"
                "        if self.mcu is not None and self.mcu.non_critical_disconnected:\n"
                "            self._error(\"non-critical MCU is disconnected\")\n"
                "\n")
            # Re-find raw_send after insertion
            raw_idx = find_line(lines, r'def raw_send\(self, cmd, minclock, reqclock, cmd_queue\)')
            if raw_idx >= 0:
                # Insert guard lines after the def line
                # serialqueue check FIRST so background timers silently return
                # after disconnect instead of raising an exception
                insert_after(lines, raw_idx,
                    "        if self.serialqueue is None:\n"
                    "            return")
            # Find raw_send_wait_ack
            ack_idx = find_line(lines, r'def raw_send_wait_ack\(self, cmd, minclock, reqclock, cmd_queue\)')
            if ack_idx >= 0:
                insert_after(lines, ack_idx,
                    "        if self.serialqueue is None:\n"
                    "            self._check_noncritical_disconnected()\n"
                    "            return")
            write_lines(filepath, lines)
            print("  OK: serialhdl: add non-critical disconnect checks to raw_send/raw_send_wait_ack")
        else:
            print("  FAIL: Could not find def raw_send")
            ok = False
    else:
        print("  SKIP (already applied): serialhdl: non-critical disconnect checks")

    return ok


def patch_mcu_refactored(klipper_dir):
    """Patch the refactored mcu.py (with helper classes)."""
    filepath = os.path.join(klipper_dir, "klippy", "mcu.py")
    print(f"\nPatching {filepath} (refactored architecture)")
    backup_file(filepath)
    ok = True
    lines = read_lines(filepath)
    content = ''.join(lines)

    # ---- 1. MCU class: add non-critical state variables ----
    if 'is_non_critical' not in content:
        # Find MCU.__init__ - "class MCU:" then "def __init__"
        mcu_cls = find_line(lines, r'^class MCU:')
        mcu_init = find_line(lines, r'def __init__\(self, config, clocksync\)', mcu_cls)
        # Find the line before "# Low-level connection and helpers"
        helpers_line = find_line(lines, r'# Low-level connection and helpers', mcu_init)
        if helpers_line >= 0:
            insert_before(lines, helpers_line,
                "        # noncritical mcus\n"
                "        self.is_non_critical = config.getboolean('is_non_critical', False)\n"
                "        if self.is_non_critical and self._name == 'mcu':\n"
                "            raise error('Primary MCU cannot be marked as non-critical!')\n"
                "        self.non_critical_disconnected = False\n"
                "        self._non_critical_reconnect_event_name = (\n"
                "            'noncritical_mcu_%s:reconnected' % (self._name,))\n"
                "        self._non_critical_disconnect_event_name = (\n"
                "            'noncritical_mcu_%s:disconnected' % (self._name,))\n")
            write_lines(filepath, lines)
            print("  OK: mcu.MCU: add non-critical state variables")
        else:
            print("  FAIL: Could not find '# Low-level connection and helpers' in MCU.__init__")
            ok = False
        lines = read_lines(filepath)
    else:
        print("  SKIP (already applied): mcu.MCU: non-critical state variables")

    # ---- 2. MCU class: add event name getters ----
    content = ''.join(lines)
    if 'get_non_critical_reconnect_event_name' not in content:
        # Find "def get_name(self):" in MCU class
        mcu_cls = find_line(lines, r'^class MCU:')
        get_name_idx = find_line(lines, r'def get_name\(self\)', mcu_cls)
        if get_name_idx >= 0:
            # Find the return line
            ret_idx = find_line(lines, r'return self\._name', get_name_idx)
            if ret_idx >= 0:
                insert_after(lines, ret_idx,
                    "    def get_non_critical_reconnect_event_name(self):\n"
                    "        return self._non_critical_reconnect_event_name\n"
                    "    def get_non_critical_disconnect_event_name(self):\n"
                    "        return self._non_critical_disconnect_event_name")
                write_lines(filepath, lines)
                print("  OK: mcu.MCU: add event name getters")
            else:
                print("  FAIL: Could not find 'return self._name' after get_name")
                ok = False
        else:
            print("  FAIL: Could not find MCU.get_name()")
            ok = False
        lines = read_lines(filepath)
    else:
        print("  SKIP (already applied): mcu.MCU: event name getters")

    # ---- 3. MCUConnectHelper: pass mcu to SerialReader ----
    content = ''.join(lines)
    if 'mcu=mcu' not in content and 'mcu=self._mcu' not in content:
        # Find SerialReader creation in MCUConnectHelper
        sr_idx = find_line(lines, r'serialhdl\.SerialReader\(')
        if sr_idx >= 0:
            old_line = lines[sr_idx]
            # Add mcu parameter
            if 'mcu_name=' in old_line:
                new_line = old_line.rstrip('\n').rstrip(')')
                # Handle single-line vs multi-line
                if old_line.strip().endswith(')'):
                    new_line = old_line.replace(')', ', mcu=mcu)')
                else:
                    new_line = old_line  # multi-line, find closing paren
                lines[sr_idx] = new_line
                # If it was single-line and we already replaced
                if ', mcu=mcu)' in new_line:
                    write_lines(filepath, lines)
                    print("  OK: mcu.MCUConnectHelper: pass mcu to SerialReader")
                else:
                    # Multi-line - find the closing paren
                    for j in range(sr_idx + 1, min(sr_idx + 5, len(lines))):
                        if ')' in lines[j]:
                            lines[j] = lines[j].replace(')', ', mcu=mcu)', 1)
                            break
                    write_lines(filepath, lines)
                    print("  OK: mcu.MCUConnectHelper: pass mcu to SerialReader (multi-line)")
            elif 'warn_prefix=' in old_line:
                new_line = old_line.replace(')', ', mcu=self)')
                lines[sr_idx] = new_line
                write_lines(filepath, lines)
                print("  OK: mcu.MCUConnectHelper: pass mcu to SerialReader (old-style)")
            else:
                print("  FAIL: Unrecognized SerialReader() call format")
                ok = False
        else:
            print("  FAIL: Could not find serialhdl.SerialReader()")
            ok = False
        lines = read_lines(filepath)
    else:
        print("  SKIP (already applied): mcu.MCUConnectHelper: pass mcu to SerialReader")

    # ---- 4. MCUConnectHelper._handle_starting: guard for non-critical ----
    content = ''.join(lines)
    if 'is_non_critical' not in content or 'not self._is_shutdown and not' not in content:
        # Find _handle_starting in MCUConnectHelper
        idx = find_line(lines, r'def _handle_starting\(self, params\)')
        if idx >= 0:
            # Find "if not self._is_shutdown:" after it
            chk = find_line(lines, r'if not self\._is_shutdown:', idx)
            if chk >= 0 and chk < idx + 5:
                old = lines[chk]
                new = old.replace('if not self._is_shutdown:',
                                  'if not self._is_shutdown and not self._mcu.is_non_critical:')
                lines[chk] = new
                write_lines(filepath, lines)
                print("  OK: mcu.MCUConnectHelper._handle_starting: guard for non-critical")
            else:
                print("  FAIL: Could not find 'if not self._is_shutdown' in _handle_starting")
                ok = False
        else:
            print("  FAIL: Could not find _handle_starting()")
            ok = False
        lines = read_lines(filepath)
    else:
        print("  SKIP (already applied): mcu._handle_starting guard")

    # ---- 5. MCUConnectHelper: add handle_non_critical_disconnect and recon methods ----
    content = ''.join(lines)
    if 'handle_non_critical_disconnect' not in content:
        # Find MCUConnectHelper class and add methods before check_timeout
        chk_idx = find_line(lines, r'def check_timeout\(self')
        if chk_idx >= 0:
            insert_before(lines, chk_idx,
                "    def _check_serial_exists(self):\n"
                "        rts = self._restart_helper.lookup_attach_uart_rts()\n"
                "        return self._serial.check_connect(self._serialport, self._baud, rts)\n"
                "    def handle_non_critical_disconnect(self):\n"
                "        self._mcu.non_critical_disconnected = True\n"
                "        self._clocksync.disconnect()\n"
                "        self._serial.disconnect()\n"
                "        self._is_shutdown = False\n"
                "        self._is_timeout = False\n"
                "        gcode = self._printer.lookup_object('gcode')\n"
                "        gcode.respond_info(\"mcu: '%s' disconnected!\" % (self._name,), log=True)\n"
                "        self._printer.send_event(self._mcu._non_critical_disconnect_event_name)\n"
                "        recon_timer = getattr(self, '_non_critical_recon_timer', None)\n"
                "        if recon_timer is not None:\n"
                "            self._reactor.update_timer(recon_timer, self._reactor.NOW)\n"
                "    def _non_critical_recon_event(self, eventtime):\n"
                "        success = self._recon_mcu()\n"
                "        if success:\n"
                "            gcode = self._printer.lookup_object('gcode')\n"
                "            gcode.respond_info(\"mcu: '%s' reconnected!\" % (self._name,), log=True)\n"
                "            return self._reactor.NEVER\n"
                "        else:\n"
                "            return eventtime + self._reconnect_interval\n"
                "    def _recon_mcu(self):\n"
                "        if not self._check_serial_exists():\n"
                "            return False\n"
                "        try:\n"
                "            self._attach()\n"
                "            logging.info(self.log_info())\n"
                "            self._emergency_stop_cmd = self._mcu.lookup_command('emergency_stop')\n"
                "            self._serial.register_response(self._handle_shutdown, 'shutdown')\n"
                "            self._serial.register_response(self._handle_shutdown, 'is_shutdown')\n"
                "            self._serial.register_response(self._handle_starting, 'starting')\n"
                "        except Exception as e:\n"
                "            logging.info(\"Non-critical MCU '%s' reconnect failed: %s\",\n"
                "                         self._name, e)\n"
                "            try:\n"
                "                self._serial.disconnect()\n"
                "            except Exception:\n"
                "                pass\n"
                "            return False\n"
                "        self._mcu.non_critical_disconnected = False\n"
                "        self._is_shutdown = False\n"
                "        self._is_timeout = False\n"
                "        try:\n"
                "            self._mcu._config_helper.reset_to_initial_state()\n"
                "            self._mcu._config_helper._mcu_identify()\n"
                "            self._mcu._stats_helper._mcu_identify()\n"
                "            self._restart_helper._mcu_identify()\n"
                "            self._mcu._config_helper._connect()\n"
                "        except Exception as e:\n"
                "            logging.info(\"Non-critical MCU '%s' config after reconnect failed: %s\",\n"
                "                         self._name, e)\n"
                "            self._mcu.non_critical_disconnected = True\n"
                "            try:\n"
                "                self._serial.disconnect()\n"
                "            except Exception:\n"
                "                pass\n"
                "            return False\n"
                "        self._printer.send_event(self._mcu._non_critical_reconnect_event_name)\n"
                "        return True\n")
            write_lines(filepath, lines)
            print("  OK: mcu.MCUConnectHelper: add disconnect/reconnect methods")
        else:
            print("  FAIL: Could not find check_timeout() for insertion point")
            ok = False
        lines = read_lines(filepath)
    else:
        print("  SKIP (already applied): mcu.MCUConnectHelper disconnect/reconnect methods")

    # ---- 6. MCUConnectHelper.__init__: add reconnect timer ----
    content = ''.join(lines)
    if 'self._non_critical_recon_timer = None' not in content:
        # Find MCUConnectHelper.__init__ - look for "self._restart_helper = MCURestartHelper"
        rh_idx = find_line(lines, r'self\._restart_helper = MCURestartHelper')
        if rh_idx >= 0:
            insert_after(lines, rh_idx,
                "        self._reconnect_interval = 0\n"
                "        self._non_critical_recon_timer = None\n"
                "        if self._mcu.is_non_critical:\n"
                "            if self._canbus_iface is not None:\n"
                "                raise error(\"CAN MCUs can't be non-critical yet!\")\n"
                "            self._reconnect_interval = (\n"
                "                config.getfloat('reconnect_interval', 2.0) + 0.12)\n"
                "            self._non_critical_recon_timer = self._reactor.register_timer(\n"
                "                self._non_critical_recon_event)")
            write_lines(filepath, lines)
            print("  OK: mcu.MCUConnectHelper.__init__: add reconnect timer")
        else:
            print("  FAIL: Could not find MCURestartHelper construction")
            ok = False
        lines = read_lines(filepath)
    else:
        print("  SKIP (already applied): mcu.MCUConnectHelper reconnect timer")

    # ---- 7. MCUConnectHelper.check_timeout: handle non-critical ----
    content = ''.join(lines)
    # Check specifically within check_timeout method, NOT globally.
    # (is_non_critical appears in _handle_starting too, so a global
    #  check would falsely skip this patch.)
    chk_idx = find_line(lines, r'def check_timeout\(self')
    check_timeout_already_patched = False
    if chk_idx >= 0:
        for j in range(chk_idx, min(chk_idx + 15, len(lines))):
            if 'handle_non_critical_disconnect' in lines[j]:
                check_timeout_already_patched = True
                break
    if not check_timeout_already_patched:
        if chk_idx >= 0:
            # Find "self._is_timeout = True" after check_timeout
            timeout_set = find_line(lines, r'self\._is_timeout = True', chk_idx)
            if timeout_set >= 0:
                insert_before(lines, timeout_set,
                    "        if self._mcu.is_non_critical:\n"
                    "            self.handle_non_critical_disconnect()\n"
                    "            return")
                write_lines(filepath, lines)
                print("  OK: mcu.MCUConnectHelper.check_timeout: handle non-critical")
            else:
                print("  FAIL: Could not find 'self._is_timeout = True' in check_timeout")
                ok = False
        else:
            print("  FAIL: Could not find check_timeout()")
            ok = False
        lines = read_lines(filepath)
    else:
        print("  SKIP (already applied): mcu.check_timeout non-critical guard")

    # ---- 8. MCUConnectHelper._mcu_identify: add serial check ----
    content = ''.join(lines)
    if '_check_serial_exists' in content and \
       'is_non_critical and not self._check_serial_exists' not in content:
        # Find MCUConnectHelper._mcu_identify
        conn_cls = find_line(lines, r'^class MCUConnectHelper')
        mcu_id = find_line(lines, r'def _mcu_identify\(self\)', conn_cls)
        if mcu_id >= 0:
            # Insert the check at the beginning of the method
            insert_after(lines, mcu_id,
                "        if self._mcu.is_non_critical and not self._check_serial_exists():\n"
                "            self._mcu.non_critical_disconnected = True\n"
                "            logging.info(\"Non-critical MCU '%s' not found at startup\", self._name)\n"
                "            recon_timer = getattr(self, '_non_critical_recon_timer', None)\n"
                "            if recon_timer is not None:\n"
                "                self._reactor.update_timer(\n"
                "                    recon_timer,\n"
                "                    self._reactor.NOW + getattr(self, '_reconnect_interval', 2.12))\n"
                "            return")
            write_lines(filepath, lines)
            print("  OK: mcu.MCUConnectHelper._mcu_identify: add serial check")
        else:
            print("  FAIL: Could not find MCUConnectHelper._mcu_identify()")
            ok = False
        lines = read_lines(filepath)
    elif 'is_non_critical and not self._check_serial_exists' in content:
        print("  SKIP (already applied): mcu._mcu_identify serial check")
    else:
        print("  NOTE: _check_serial_exists not yet added, skipping _mcu_identify guard")
        ok = False

    # ---- 8b. MCUConnectHelper._handle_shutdown: guard for non-critical ----
    content = ''.join(lines)
    # Find _handle_shutdown in MCUConnectHelper and guard it
    conn_cls = find_line(lines, r'^class MCUConnectHelper')
    if conn_cls >= 0:
        hs_idx = find_line(lines, r'def _handle_shutdown\(self, params\)', conn_cls)
        if hs_idx >= 0:
            # Check if already guarded
            if 'is_non_critical' not in lines[hs_idx + 1] and \
               'is_non_critical' not in lines[hs_idx + 2]:
                # Find the invoke_async_shutdown line after it
                invoke_idx = find_line(lines, r'invoke_async_shutdown', hs_idx)
                if invoke_idx >= 0 and invoke_idx < hs_idx + 15:
                    insert_before(lines, invoke_idx,
                        "        if self._mcu.is_non_critical:\n"
                        "            self.handle_non_critical_disconnect()\n"
                        "            return")
                    write_lines(filepath, lines)
                    print("  OK: mcu.MCUConnectHelper._handle_shutdown: guard for non-critical")
                else:
                    print("  WARN: Could not find invoke_async_shutdown in _handle_shutdown")
            else:
                print("  SKIP (already applied): mcu._handle_shutdown non-critical guard")
        lines = read_lines(filepath)

    # ---- 9. MCUConfigHelper._mcu_identify: guard for non-critical ----
    content = ''.join(lines)
    cfg_cls = find_line(lines, r'^class MCUConfigHelper')
    if cfg_cls >= 0:
        cfg_mcu_id = find_line(lines, r'def _mcu_identify\(self\)', cfg_cls)
        if cfg_mcu_id >= 0:
            next_line = find_line(lines, r'self\._mcu_freq', cfg_mcu_id)
            if next_line >= 0 and next_line < cfg_mcu_id + 5:
                if 'non_critical_disconnected' not in lines[cfg_mcu_id + 1]:
                    insert_after(lines, cfg_mcu_id,
                        "        if self._mcu.non_critical_disconnected:\n"
                        "            return")
                    write_lines(filepath, lines)
                    print("  OK: mcu.MCUConfigHelper._mcu_identify: guard for non-critical")
                else:
                    print("  SKIP (already applied): mcu.MCUConfigHelper._mcu_identify guard")
            else:
                print("  WARN: MCUConfigHelper._mcu_identify layout unexpected")
        else:
            print("  WARN: Could not find MCUConfigHelper._mcu_identify")
        lines = read_lines(filepath)

    # ---- 10. MCUConfigHelper._connect: guard for non-critical ----
    content = ''.join(lines)
    cfg_cls = find_line(lines, r'^class MCUConfigHelper')
    if cfg_cls >= 0:
        cfg_connect = find_line(lines, r'def _connect\(self\)', cfg_cls)
        if cfg_connect >= 0:
            if 'non_critical_disconnected' not in lines[cfg_connect + 1]:
                insert_after(lines, cfg_connect,
                    "        if self._mcu.non_critical_disconnected:\n"
                    "            return")
                write_lines(filepath, lines)
                print("  OK: mcu.MCUConfigHelper._connect: guard for non-critical")
            else:
                print("  SKIP (already applied): mcu.MCUConfigHelper._connect guard")
        lines = read_lines(filepath)

    # ---- 11. MCUConfigHelper: add reset_to_initial_state and state caching ----
    content = ''.join(lines)
    if 'reset_to_initial_state' not in content:
        cfg_cls = find_line(lines, r'^class MCUConfigHelper')
        if cfg_cls >= 0:
            # Add cached state variables in __init__
            cfg_init = find_line(lines, r'def __init__\(self', cfg_cls)
            if cfg_init >= 0:
                # Find self._config_crc or self._init_cmds to insert after
                init_cmds = find_line(lines, r'self\._init_cmds\s*=', cfg_init)
                if init_cmds >= 0:
                    insert_after(lines, init_cmds,
                        "        self._cached_init_state = False\n"
                        "        self._oid_count_saved = 0\n"
                        "        self._config_cmds_saved = []\n"
                        "        self._init_cmds_saved = []\n"
                        "        self._restart_cmds_saved = []")
                    write_lines(filepath, lines)
                    lines = read_lines(filepath)

            # Add state caching at start of _finalize_config
            fin_idx = find_line(lines, r'def _finalize_config\(self\)', cfg_cls)
            if fin_idx >= 0:
                # Insert caching code after the def line
                insert_after(lines, fin_idx,
                    "        if not self._cached_init_state:\n"
                    "            self._oid_count_saved = self._oid_count\n"
                    "            self._config_cmds_saved = list(self._config_cmds)\n"
                    "            self._init_cmds_saved = list(self._init_cmds)\n"
                    "            self._restart_cmds_saved = list(self._restart_cmds)\n"
                    "            self._cached_init_state = True")
                write_lines(filepath, lines)
                lines = read_lines(filepath)

            # Add reset_to_initial_state method before _finalize_config
            fin_idx = find_line(lines, r'def _finalize_config\(self\)', cfg_cls)
            if fin_idx >= 0:
                insert_before(lines, fin_idx,
                    "    def reset_to_initial_state(self):\n"
                    "        if self._cached_init_state:\n"
                    "            self._oid_count = self._oid_count_saved\n"
                    "            self._config_cmds = list(self._config_cmds_saved)\n"
                    "            self._init_cmds = list(self._init_cmds_saved)\n"
                    "            self._restart_cmds = list(self._restart_cmds_saved)\n"
                    "            self._config_finalized = False\n"
                    "            self._config_crc = 0\n"
                    "        self._reserved_move_slots = 0\n")
                write_lines(filepath, lines)
                print("  OK: mcu.MCUConfigHelper: add reset_to_initial_state and caching")
            else:
                print("  FAIL: Could not find _finalize_config()")
                ok = False
        lines = read_lines(filepath)
    else:
        print("  SKIP (already applied): mcu.MCUConfigHelper reset_to_initial_state")

    # ---- 12. MCUStatsHelper._mcu_identify: guard for non-critical ----
    content = ''.join(lines)
    stats_cls = find_line(lines, r'^class MCUStatsHelper')
    if stats_cls >= 0:
        stats_mcu_id = find_line(lines, r'def _mcu_identify\(self\)', stats_cls)
        if stats_mcu_id >= 0:
            if 'non_critical_disconnected' not in lines[stats_mcu_id + 1]:
                insert_after(lines, stats_mcu_id,
                    "        if self._mcu.non_critical_disconnected:\n"
                    "            return")
                write_lines(filepath, lines)
                print("  OK: mcu.MCUStatsHelper._mcu_identify: guard for non-critical")
            else:
                print("  SKIP (already applied): mcu.MCUStatsHelper._mcu_identify guard")
        lines = read_lines(filepath)

    # ---- 13. MCUStatsHelper._ready: guard for non-critical ----
    content = ''.join(lines)
    stats_cls = find_line(lines, r'^class MCUStatsHelper')
    if stats_cls >= 0:
        stats_ready = find_line(lines, r'def _ready\(self\)', stats_cls)
        if stats_ready >= 0:
            ready_check = find_line(lines, r'is_fileoutput', stats_ready)
            if ready_check >= 0 and ready_check < stats_ready + 5:
                if 'non_critical_disconnected' not in lines[ready_check]:
                    old = lines[ready_check]
                    new = old.replace('is_fileoutput()',
                                      'is_fileoutput() or self._mcu.non_critical_disconnected')
                    lines[ready_check] = new
                    write_lines(filepath, lines)
                    print("  OK: mcu.MCUStatsHelper._ready: guard for non-critical")
        lines = read_lines(filepath)

    # ---- 14. MCURestartHelper._firmware_restart: guard for non-critical ----
    content = ''.join(lines)
    if 'non_critical_disconnected' not in content or \
       'non_critical_disconnected' in content:
        restart_cls = find_line(lines, r'^class MCURestartHelper')
        if restart_cls >= 0:
            fw_restart = find_line(lines, r'def _firmware_restart\(self', restart_cls)
            if fw_restart >= 0:
                # Find the existing early-return check
                bridge_check = find_line(lines, r'_is_mcu_bridge', fw_restart)
                if bridge_check >= 0 and bridge_check < fw_restart + 5:
                    if 'non_critical_disconnected' not in lines[bridge_check]:
                        # Find the return line
                        ret_line = find_line(lines, r'return$', bridge_check)
                        if ret_line >= 0 and ret_line < bridge_check + 3:
                            # Insert a non-critical check after the bridge return
                            insert_after(lines, ret_line,
                                "        if self._mcu.non_critical_disconnected:\n"
                                "            return")
                            write_lines(filepath, lines)
                            print("  OK: mcu.MCURestartHelper._firmware_restart: guard for non-critical")
                        else:
                            print("  WARN: Could not find return after _is_mcu_bridge check")
                    else:
                        print("  SKIP (already applied): firmware_restart guard")
                else:
                    # No _is_mcu_bridge, insert guard at start
                    insert_after(lines, fw_restart,
                        "        if self._mcu.non_critical_disconnected:\n"
                        "            return")
                    write_lines(filepath, lines)
                    print("  OK: mcu.MCURestartHelper._firmware_restart: guard for non-critical")
            lines = read_lines(filepath)

    # ---- 15. MCURestartHelper._mcu_identify: guard for non-critical ----
    content = ''.join(lines)
    restart_cls = find_line(lines, r'^class MCURestartHelper')
    if restart_cls >= 0:
        rst_mcu_id = find_line(lines, r'def _mcu_identify\(self\)', restart_cls)
        if rst_mcu_id >= 0:
            if 'non_critical_disconnected' not in lines[rst_mcu_id + 1]:
                insert_after(lines, rst_mcu_id,
                    "        if self._mcu.non_critical_disconnected:\n"
                    "            return")
                write_lines(filepath, lines)
                print("  OK: mcu.MCURestartHelper._mcu_identify: guard for non-critical")
            else:
                print("  SKIP (already applied): MCURestartHelper._mcu_identify guard")
        lines = read_lines(filepath)

    return ok


def patch_mcu_legacy(klipper_dir):
    """Patch the legacy-style mcu.py (single MCU class)."""
    filepath = os.path.join(klipper_dir, "klippy", "mcu.py")
    print(f"\nPatching {filepath} (legacy architecture)")
    backup_file(filepath)
    ok = True
    content = read_file(filepath)

    # Store config ref
    ok &= apply_replacement(
        filepath,
        "    def __init__(self, config, clocksync):\n"
        "        self._printer = printer = config.get_printer()\n",
        "    def __init__(self, config, clocksync):\n"
        "        self._config = config\n"
        "        self._printer = printer = config.get_printer()\n",
        "mcu: store config reference",
    )

    # Pass mcu to SerialReader
    content = read_file(filepath)
    if "warn_prefix=wp)" in content and "mcu=self" not in content:
        ok &= apply_replacement(
            filepath,
            "serialhdl.SerialReader(self._reactor, warn_prefix=wp)",
            "serialhdl.SerialReader(self._reactor, warn_prefix=wp, mcu=self)",
            "mcu: pass self to SerialReader",
        )

    # The rest of the legacy patches follow the original Kalico patterns
    # (These match the old single-class MCU structure)
    print("  WARN: Legacy MCU architecture - some patches may need manual adjustment")
    return ok


def patch_mcu(klipper_dir):
    """Patch mcu.py - detect architecture and dispatch."""
    if is_refactored_mcu(klipper_dir):
        return patch_mcu_refactored(klipper_dir)
    else:
        return patch_mcu_legacy(klipper_dir)


def patch_stepper(klipper_dir):
    """Patch stepper.py to skip query for disconnected non-critical MCU."""
    filepath = os.path.join(klipper_dir, "klippy", "stepper.py")
    print(f"\nPatching {filepath}")
    backup_file(filepath)

    content = read_file(filepath)
    if 'non_critical_disconnected' in content:
        print("  SKIP (already applied): stepper: non-critical guard")
        return True

    # Find _query_mcu_position and modify the fileoutput check
    lines = read_lines(filepath)
    idx = find_line(lines, r'def _query_mcu_position\(self\)')
    if idx >= 0:
        chk = find_line(lines, r'is_fileoutput\(\)', idx)
        if chk >= 0 and chk < idx + 5:
            old = lines[chk]
            new = old.replace('is_fileoutput():',
                              'is_fileoutput() or self._mcu.non_critical_disconnected:')
            lines[chk] = new
            write_lines(filepath, lines)
            print("  OK: stepper: skip query for disconnected non-critical MCU")
            return True
    print("  FAIL: Could not find _query_mcu_position with is_fileoutput check")
    return False


def patch_adxl345(klipper_dir):
    """Patch adxl345.py with check_connected guard."""
    filepath = os.path.join(klipper_dir, "klippy", "extras", "adxl345.py")
    print(f"\nPatching {filepath}")
    backup_file(filepath)
    ok = True
    content = read_file(filepath)

    if 'check_connected' not in content:
        ok &= apply_replacement(
            filepath,
            "    def read_reg(self, reg):\n",
            "    def check_connected(self):\n"
            "        if self.mcu.non_critical_disconnected:\n"
            "            raise self.printer.command_error(\n"
            "                \"ADXL: %s could not connect because mcu: \"\n"
            "                \"%s is non_critical_disconnected!\"\n"
            "                % (self.name, self.mcu.get_name()))\n"
            "\n"
            "    def read_reg(self, reg):\n",
            "adxl345: add check_connected()",
        )

        ok &= apply_replacement(
            filepath,
            "    def start_internal_client(self):\n"
            "        aqh = AccelQueryHelper(self.printer)\n",
            "    def start_internal_client(self):\n"
            "        self.check_connected()\n"
            "        aqh = AccelQueryHelper(self.printer)\n",
            "adxl345: call check_connected in start_internal_client",
        )
    else:
        print("  SKIP (already applied): adxl345: check_connected")

    return ok


def patch_lis2dw(klipper_dir):
    """Patch lis2dw.py with check_connected guard."""
    filepath = os.path.join(klipper_dir, "klippy", "extras", "lis2dw.py")
    if not os.path.exists(filepath):
        print(f"\nSKIP (file not found): {filepath}")
        return True
    print(f"\nPatching {filepath}")
    backup_file(filepath)
    content = read_file(filepath)
    ok = True

    if 'check_connected' not in content:
        ok &= apply_replacement(
            filepath,
            "    def read_reg(self, reg):\n",
            "    def check_connected(self):\n"
            "        if self.mcu.non_critical_disconnected:\n"
            "            raise self.printer.command_error(\n"
            "                \"LIS2DW: %s could not connect because mcu: \"\n"
            "                \"%s is non_critical_disconnected!\"\n"
            "                % (self.name, self.mcu.get_name()))\n"
            "\n"
            "    def read_reg(self, reg):\n",
            "lis2dw: add check_connected()",
        )

        ok &= apply_replacement(
            filepath,
            "    def start_internal_client(self):\n"
            "        aqh = adxl345.AccelQueryHelper(self.printer)\n",
            "    def start_internal_client(self):\n"
            "        self.check_connected()\n"
            "        aqh = adxl345.AccelQueryHelper(self.printer)\n",
            "lis2dw: call check_connected in start_internal_client",
        )
    else:
        print("  SKIP (already applied): lis2dw: check_connected")

    return ok


def patch_mpu9250(klipper_dir):
    """Patch mpu9250.py with check_connected guard."""
    filepath = os.path.join(klipper_dir, "klippy", "extras", "mpu9250.py")
    if not os.path.exists(filepath):
        print(f"\nSKIP (file not found): {filepath}")
        return True
    print(f"\nPatching {filepath}")
    backup_file(filepath)
    content = read_file(filepath)
    ok = True

    if 'check_connected' not in content:
        ok &= apply_replacement(
            filepath,
            "    def read_reg(self, reg):\n",
            "    def check_connected(self):\n"
            "        if self.mcu.non_critical_disconnected:\n"
            "            raise self.printer.command_error(\n"
            "                \"MPU: %s could not connect because mcu: \"\n"
            "                \"%s is non_critical_disconnected!\"\n"
            "                % (self.name, self.mcu.get_name()))\n"
            "\n"
            "    def read_reg(self, reg):\n",
            "mpu9250: add check_connected()",
        )

        ok &= apply_replacement(
            filepath,
            "    def start_internal_client(self):\n"
            "        aqh = adxl345.AccelQueryHelper(self.printer)\n",
            "    def start_internal_client(self):\n"
            "        self.check_connected()\n"
            "        aqh = adxl345.AccelQueryHelper(self.printer)\n",
            "mpu9250: call check_connected in start_internal_client",
        )
    else:
        print("  SKIP (already applied): mpu9250: check_connected")

    return ok


def patch_neopixel(klipper_dir):
    """Patch neopixel.py to re-send data on reconnect."""
    filepath = os.path.join(klipper_dir, "klippy", "extras", "neopixel.py")
    print(f"\nPatching {filepath}")
    backup_file(filepath)
    content = read_file(filepath)

    if 'get_non_critical_reconnect_event_name' in content:
        print("  SKIP (already applied): neopixel: reconnect handler")
        return True

    return apply_replacement(
        filepath,
        "        printer.register_event_handler(\"klippy:connect\", self.send_data)\n",
        "        printer.register_event_handler(\"klippy:connect\", self.send_data)\n"
        "        printer.register_event_handler(\n"
        "            self.mcu.get_non_critical_reconnect_event_name(), self.send_data\n"
        "        )\n",
        "neopixel: register reconnect handler",
    )


def patch_display(klipper_dir):
    """Patch display.py to re-init LCD on reconnect."""
    filepath = os.path.join(klipper_dir, "klippy", "extras", "display", "display.py")
    print(f"\nPatching {filepath}")
    backup_file(filepath)
    content = read_file(filepath)
    ok = True

    if 'handle_reconnect' in content:
        print("  SKIP (already applied): display: reconnect handler")
        return True

    ok &= apply_replacement(
        filepath,
        "        self.printer.register_event_handler(\"klippy:ready\", self.handle_ready)\n"
        "        self.screen_update_timer = self.reactor.register_timer(\n",
        "        self.printer.register_event_handler(\"klippy:ready\", self.handle_ready)\n"
        "        self.printer.register_event_handler(\n"
        "            self.lcd_chip.mcu.get_non_critical_reconnect_event_name(),\n"
        "            self.handle_reconnect,\n"
        "        )\n"
        "        self.screen_update_timer = self.reactor.register_timer(\n",
        "display: register reconnect handler",
    )

    ok &= apply_replacement(
        filepath,
        "    def handle_ready(self):\n"
        "        self.lcd_chip.init()\n",
        "    def handle_reconnect(self):\n"
        "        self.lcd_chip.init()\n"
        "\n"
        "    def handle_ready(self):\n"
        "        self.lcd_chip.init()\n",
        "display: add handle_reconnect()",
    )

    return ok


def patch_st7920(klipper_dir):
    """Patch st7920.py to expose mcu attribute."""
    filepath = os.path.join(klipper_dir, "klippy", "extras", "display", "st7920.py")
    if not os.path.exists(filepath):
        print(f"\nSKIP (file not found): {filepath}")
        return True
    print(f"\nPatching {filepath}")
    backup_file(filepath)
    content = read_file(filepath)

    if "self.mcu = mcu" in content or "self.mcu =" in content:
        print("  SKIP (already has mcu attribute)")
        return True
    if "self.spi = bus.MCU_SPI(mcu," in content:
        return apply_replacement(
            filepath,
            "        self.spi = bus.MCU_SPI(mcu,",
            "        self.spi = bus.MCU_SPI(mcu,\n"
            "        self.mcu = mcu  # noncritical mcu support\n"
            "        self.spi = bus.MCU_SPI(mcu,",
            "st7920: add mcu attribute",
        )
    print("  WARN: Could not find insertion point for self.mcu in st7920.py")
    return True  # Non-fatal


def patch_uc1701(klipper_dir):
    """Patch uc1701.py to expose mcu attribute."""
    filepath = os.path.join(klipper_dir, "klippy", "extras", "display", "uc1701.py")
    if not os.path.exists(filepath):
        print(f"\nSKIP (file not found): {filepath}")
        return True
    print(f"\nPatching {filepath}")
    backup_file(filepath)
    ok = True
    content = read_file(filepath)

    # UC1701 class
    if "class UC1701" in content and "self.mcu = io.spi.get_mcu()" not in content:
        ok &= apply_replacement(
            filepath,
            "        io = SPI4wire(config, \"a0_pin\")\n"
            "        DisplayBase.__init__(self, io)\n",
            "        io = SPI4wire(config, \"a0_pin\")\n"
            "        self.mcu = io.spi.get_mcu()\n"
            "        DisplayBase.__init__(self, io)\n",
            "uc1701: add mcu attribute to UC1701",
        )
    else:
        print("  SKIP (already applied): uc1701: UC1701 mcu attribute")

    # SSD1306 class
    content = read_file(filepath)
    if "io_bus = io.spi" in content and "self.mcu = io_bus.get_mcu()" not in content:
        ok &= apply_replacement(
            filepath,
            "            io_bus = io.spi\n"
            "        self.reset = ResetHelper(",
            "            io_bus = io.spi\n"
            "        self.mcu = io_bus.get_mcu()\n"
            "        self.reset = ResetHelper(",
            "uc1701: add mcu attribute to SSD1306",
        )
    else:
        print("  SKIP (already applied or not present): uc1701: SSD1306 mcu attribute")

    return ok


def patch_temperature_mcu(klipper_dir):
    """Patch temperature_mcu.py to support reconnection."""
    filepath = os.path.join(klipper_dir, "klippy", "extras", "temperature_mcu.py")
    if not os.path.exists(filepath):
        print(f"\nSKIP (file not found): {filepath}")
        return True
    print(f"\nPatching {filepath}")
    backup_file(filepath)
    content = read_file(filepath)
    ok = True

    if '_build_config' in content:
        print("  SKIP (already applied): temperature_mcu: _build_config refactor")
        return True

    lines = read_lines(filepath)

    # Find the event handler registration - handle both old and new style
    reg_idx = find_line(lines, r'register_event_handler.*mcu_identify')
    if reg_idx >= 0:
        # Add config callback registration after
        # Find end of the register_event_handler call (may span lines)
        end = reg_idx
        for j in range(reg_idx, min(reg_idx + 5, len(lines))):
            if ')' in lines[j]:
                end = j
                break
        insert_after(lines, end,
            "        self.mcu_adc.get_mcu().register_config_callback(self._build_config)")
        write_lines(filepath, lines)
        print("  OK: temperature_mcu: register config callback")
        lines = read_lines(filepath)
    else:
        print("  FAIL: Could not find mcu_identify event handler registration")
        ok = False

    # Refactor the identify method to delegate to _build_config
    # Find "def handle_mcu_identify(self):" or "def _mcu_identify(self):"
    id_idx = find_line(lines, r'def (handle_mcu_identify|_mcu_identify)\(self\)')
    if id_idx >= 0:
        # Find "# Obtain mcu information" after it
        obtain_idx = find_line(lines, r'# Obtain mcu information', id_idx)
        if obtain_idx >= 0 and obtain_idx < id_idx + 5:
            insert_before(lines, obtain_idx,
                "        self._build_config()\n"
                "\n"
                "    def _build_config(self):")
            write_lines(filepath, lines)
            print("  OK: temperature_mcu: refactor with _build_config")
        else:
            print("  FAIL: Could not find '# Obtain mcu information'")
            ok = False
    else:
        print("  FAIL: Could not find handle_mcu_identify/_mcu_identify")
        ok = False

    return ok


def patch_tmc(klipper_dir):
    """Patch tmc.py to skip init for disconnected non-critical MCU."""
    filepath = os.path.join(klipper_dir, "klippy", "extras", "tmc.py")
    if not os.path.exists(filepath):
        print(f"\nSKIP (file not found): {filepath}")
        return True
    print(f"\nPatching {filepath}")
    backup_file(filepath)
    content = read_file(filepath)

    if 'non_critical_disconnected' in content:
        print("  SKIP (already applied): tmc: non-critical guard")
        return True

    return apply_replacement(
        filepath,
        "        # Send init\n"
        "        try:\n"
        "            self._init_registers()\n",
        "        # Send init\n"
        "        try:\n"
        "            if self.mcu_tmc.get_mcu().non_critical_disconnected:\n"
        "                logging.info(\n"
        "                    \"TMC %s failed to init - non_critical_mcu: %s is disconnected!\",\n"
        "                    self.name,\n"
        "                    self.mcu_tmc.get_mcu().get_name(),\n"
        "                )\n"
        "            else:\n"
        "                self._init_registers()\n",
        "tmc: skip init for disconnected non-critical MCU",
    )


def patch_tmc2130(klipper_dir):
    """Patch tmc2130.py to expose mcu attribute."""
    filepath = os.path.join(klipper_dir, "klippy", "extras", "tmc2130.py")
    if not os.path.exists(filepath):
        print(f"\nSKIP (file not found): {filepath}")
        return True
    print(f"\nPatching {filepath}")
    backup_file(filepath)
    content = read_file(filepath)

    if 'self.mcu = ' in content and 'MCU_TMC_SPI' in content:
        print("  SKIP (already applied): tmc2130: mcu attribute")
        return True

    # Use line-based approach - no blank line between tmc_frequency and get_fields
    lines = read_lines(filepath)
    idx = find_line(lines, r'self\.tmc_frequency = tmc_frequency')
    if idx >= 0:
        insert_after(lines, idx,
            "        self.mcu = self.tmc_spi.get_mcu()")
        write_lines(filepath, lines)
        print("  OK: tmc2130: add mcu attribute")
        return True
    print("  FAIL: Could not find self.tmc_frequency = tmc_frequency in MCU_TMC_SPI")
    return False


def patch_tmc2660(klipper_dir):
    """Patch tmc2660.py to expose mcu attribute."""
    filepath = os.path.join(klipper_dir, "klippy", "extras", "tmc2660.py")
    if not os.path.exists(filepath):
        print(f"\nSKIP (file not found): {filepath}")
        return True
    print(f"\nPatching {filepath}")
    backup_file(filepath)
    content = read_file(filepath)

    if 'self.mcu = self.spi.get_mcu()' in content:
        print("  SKIP (already applied): tmc2660: mcu attribute")
        return True

    # Find "self.fields = fields" in MCU_TMC2660_SPI
    lines = read_lines(filepath)
    cls_idx = find_line(lines, r'class MCU_TMC2660')
    if cls_idx >= 0:
        field_idx = find_line(lines, r'self\.fields = fields', cls_idx)
        if field_idx >= 0:
            insert_after(lines, field_idx,
                "        self.mcu = self.spi.get_mcu()")
            write_lines(filepath, lines)
            print("  OK: tmc2660: add mcu attribute")
            return True
    print("  FAIL: Could not find self.fields = fields in MCU_TMC2660_SPI")
    return False


def patch_tmc_uart(klipper_dir):
    """Patch tmc_uart.py to expose mcu attribute."""
    filepath = os.path.join(klipper_dir, "klippy", "extras", "tmc_uart.py")
    if not os.path.exists(filepath):
        print(f"\nSKIP (file not found): {filepath}")
        return True
    print(f"\nPatching {filepath}")
    backup_file(filepath)
    content = read_file(filepath)

    if 'self.mcu = self.mcu_uart' in content:
        print("  SKIP (already applied): tmc_uart: mcu attribute")
        return True

    # Find "self.tmc_frequency = tmc_frequency" in MCU_TMC_uart
    lines = read_lines(filepath)
    cls_idx = find_line(lines, r'class MCU_TMC_uart:')
    if cls_idx >= 0:
        freq_idx = find_line(lines, r'self\.tmc_frequency = tmc_frequency', cls_idx)
        if freq_idx >= 0:
            insert_after(lines, freq_idx,
                "        self.mcu = self.mcu_uart.get_mcu()")
            write_lines(filepath, lines)
            print("  OK: tmc_uart: add mcu attribute")
            return True
    print("  FAIL: Could not find self.tmc_frequency in MCU_TMC_uart")
    return False


# =====================================================================
# Main patch/revert logic
# =====================================================================

def do_patch(klipper_dir):
    """Apply all patches."""
    print("=" * 60)
    print("Non-Critical MCU Patch for Klipper")
    print(f"Target: {klipper_dir}")
    if is_refactored_mcu(klipper_dir):
        print("Detected: Refactored MCU architecture (2024+)")
    else:
        print("Detected: Legacy MCU architecture")
    print("=" * 60)

    all_ok = True

    # Core patches (required)
    all_ok &= patch_clocksync(klipper_dir)
    all_ok &= patch_serialhdl(klipper_dir)
    all_ok &= patch_mcu(klipper_dir)
    all_ok &= patch_stepper(klipper_dir)

    # Extras patches (accelerometers, displays, TMC drivers)
    all_ok &= patch_adxl345(klipper_dir)
    all_ok &= patch_lis2dw(klipper_dir)
    all_ok &= patch_mpu9250(klipper_dir)
    all_ok &= patch_neopixel(klipper_dir)
    all_ok &= patch_display(klipper_dir)
    all_ok &= patch_st7920(klipper_dir)
    all_ok &= patch_uc1701(klipper_dir)
    all_ok &= patch_temperature_mcu(klipper_dir)
    all_ok &= patch_tmc(klipper_dir)
    all_ok &= patch_tmc2130(klipper_dir)
    all_ok &= patch_tmc2660(klipper_dir)
    all_ok &= patch_tmc_uart(klipper_dir)

    print("\n" + "=" * 60)
    if all_ok:
        print("All patches applied successfully!")
        print("\nUsage: Add to your printer.cfg for any secondary MCU:")
        print("  [mcu my_secondary_mcu]")
        print("  serial: /dev/serial/by-id/...")
        print("  is_non_critical: True")
        print("  # reconnect_interval: 2.0  # optional, default 2s")
        print("\nNote: The primary [mcu] cannot be non-critical.")
        print("Restart Klipper to apply: sudo systemctl restart klipper")
    else:
        print("Some patches FAILED. Check output above for details.")
        print("You can revert with: python3 patch_noncritical_mcu.py --revert")
    print("=" * 60)

    return all_ok


def do_revert(klipper_dir):
    """Revert all patches from backups."""
    print("=" * 60)
    print("Reverting Non-Critical MCU Patch")
    print(f"Target: {klipper_dir}")
    print("=" * 60)

    files_to_check = [
        os.path.join("klippy", "clocksync.py"),
        os.path.join("klippy", "serialhdl.py"),
        os.path.join("klippy", "mcu.py"),
        os.path.join("klippy", "stepper.py"),
        os.path.join("klippy", "extras", "adxl345.py"),
        os.path.join("klippy", "extras", "lis2dw.py"),
        os.path.join("klippy", "extras", "mpu9250.py"),
        os.path.join("klippy", "extras", "neopixel.py"),
        os.path.join("klippy", "extras", "display", "display.py"),
        os.path.join("klippy", "extras", "display", "st7920.py"),
        os.path.join("klippy", "extras", "display", "uc1701.py"),
        os.path.join("klippy", "extras", "temperature_mcu.py"),
        os.path.join("klippy", "extras", "tmc.py"),
        os.path.join("klippy", "extras", "tmc2130.py"),
        os.path.join("klippy", "extras", "tmc2660.py"),
        os.path.join("klippy", "extras", "tmc_uart.py"),
    ]

    restored = 0
    for rel_path in files_to_check:
        full_path = os.path.join(klipper_dir, rel_path)
        if restore_file(full_path):
            restored += 1

    print(f"\nRestored {restored} file(s).")
    if restored > 0:
        print("Restart Klipper to apply: sudo systemctl restart klipper")


def main():
    parser = argparse.ArgumentParser(
        description="Patch Klipper with non-critical MCU support (from Kalico PR #339)"
    )
    parser.add_argument(
        "--klipper-dir",
        default=None,
        help="Path to Klipper installation (default: auto-detect ~/klipper)",
    )
    parser.add_argument(
        "--revert",
        action="store_true",
        help="Revert all patches using backups",
    )
    args = parser.parse_args()

    klipper_dir = args.klipper_dir
    if klipper_dir is None:
        klipper_dir = find_klipper_dir()
        if klipper_dir is None:
            print("ERROR: Could not find Klipper installation.")
            print("Please specify with --klipper-dir /path/to/klipper")
            sys.exit(1)

    klipper_dir = os.path.abspath(klipper_dir)
    if not os.path.isfile(os.path.join(klipper_dir, "klippy", "mcu.py")):
        print(f"ERROR: {klipper_dir} does not appear to be a Klipper installation.")
        sys.exit(1)

    if args.revert:
        do_revert(klipper_dir)
    else:
        success = do_patch(klipper_dir)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
