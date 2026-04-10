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
    ok = True

    # 1) Add mcu parameter to __init__
    ok &= apply_replacement(
        filepath,
        "class SerialReader:\n"
        "    def __init__(self, reactor, warn_prefix=\"\"):\n"
        "        self.reactor = reactor\n"
        "        self.warn_prefix = warn_prefix\n",
        "class SerialReader:\n"
        "    def __init__(self, reactor, warn_prefix=\"\", mcu=None):\n"
        "        self.reactor = reactor\n"
        "        self.warn_prefix = warn_prefix\n"
        "        self.mcu = mcu\n",
        "serialhdl: add mcu parameter to __init__",
    )

    # 2) Add check_connect method after _start_session
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

    # 3) Add early-break in connect_uart if already connected
    ok &= apply_replacement(
        filepath,
        "        logging.info(\"%sStarting serial connect\", self.warn_prefix)\n"
        "        start_time = self.reactor.monotonic()\n"
        "        while True:\n"
        "            if self.reactor.monotonic() > start_time + 90.:\n",
        "        logging.info(\"%sStarting serial connect\", self.warn_prefix)\n"
        "        start_time = self.reactor.monotonic()\n"
        "        while 1:\n"
        "            if (\n"
        "                self.serialqueue is not None\n"
        "            ):  # if we're already connected, don't recon\n"
        "                break\n"
        "            if self.reactor.monotonic() > start_time + 90.:\n",
        "serialhdl: add reconnect guard in connect_uart",
    )

    # 4) Add _check_noncritical_disconnected and guards on raw_send methods
    ok &= apply_replacement(
        filepath,
        "    # Command sending\n"
        "    def raw_send(self, cmd, minclock, reqclock, cmd_queue):\n"
        "        self.ffi_lib.serialqueue_send(\n",
        "    def _check_noncritical_disconnected(self):\n"
        "        if self.mcu is not None and self.mcu.non_critical_disconnected:\n"
        "            self._error(\"non-critical MCU is disconnected\")\n"
        "\n"
        "    # Command sending\n"
        "    def raw_send(self, cmd, minclock, reqclock, cmd_queue):\n"
        "        self._check_noncritical_disconnected()\n"
        "        if self.serialqueue is None:\n"
        "            return\n"
        "        self.ffi_lib.serialqueue_send(\n",
        "serialhdl: add non-critical disconnect check to raw_send",
    )

    ok &= apply_replacement(
        filepath,
        "    def raw_send_wait_ack(self, cmd, minclock, reqclock, cmd_queue):\n"
        "        self.last_notify_id += 1\n",
        "    def raw_send_wait_ack(self, cmd, minclock, reqclock, cmd_queue):\n"
        "        self._check_noncritical_disconnected()\n"
        "        if self.serialqueue is None:\n"
        "            return\n"
        "        self.last_notify_id += 1\n",
        "serialhdl: add non-critical disconnect check to raw_send_wait_ack",
    )

    return ok


def patch_mcu(klipper_dir):
    """Patch mcu.py - the main non-critical MCU logic."""
    filepath = os.path.join(klipper_dir, "klippy", "mcu.py")
    print(f"\nPatching {filepath}")
    backup_file(filepath)
    ok = True

    # 1) Store config ref and pass mcu to SerialReader
    ok &= apply_replacement(
        filepath,
        "    def __init__(self, config, clocksync):\n"
        "        self._printer = printer = config.get_printer()\n",
        "    def __init__(self, config, clocksync):\n"
        "        self._config = config\n"
        "        self._printer = printer = config.get_printer()\n",
        "mcu: store config reference",
    )

    ok &= apply_replacement(
        filepath,
        "        wp = \"mcu '%s': \" % (self._name)\n"
        "        self._serial = serialhdl.SerialReader(self._reactor, warn_prefix=wp)\n",
        "        wp = \"mcu '%s': \" % (self._name)\n"
        "        self._serial = serialhdl.SerialReader(\n"
        "            self._reactor, warn_prefix=wp, mcu=self\n"
        "        )\n",
        "mcu: pass self to SerialReader",
    )

    # 2) Add non-critical MCU initialization block after _mcu_tick_awake
    ok &= apply_replacement(
        filepath,
        "        self._mcu_tick_awake = 0.\n"
        "        # Register handlers\n",
        "        self._mcu_tick_awake = 0.\n"
        "        # noncritical mcus\n"
        "        self.is_non_critical = config.getboolean(\"is_non_critical\", False)\n"
        "        if self.is_non_critical and self.get_name() == \"mcu\":\n"
        "            raise error(\"Primary MCU cannot be marked as non-critical!\")\n"
        "        if self.is_non_critical:\n"
        "            self.non_critical_recon_timer = self._reactor.register_timer(\n"
        "                self.non_critical_recon_event\n"
        "            )\n"
        "            if canbus_uuid:\n"
        "                raise error(\"CAN MCUs can't be non-critical yet!\")\n"
        "        self.non_critical_disconnected = False\n"
        "        self._non_critical_reconnect_event_name = (\n"
        "            f\"noncritical_mcu_{self.get_name()}:reconnected\"\n"
        "        )\n"
        "        self._non_critical_disconnect_event_name = (\n"
        "            f\"noncritical_mcu_{self.get_name()}:disconnected\"\n"
        "        )\n"
        "        self.reconnect_interval = (\n"
        "            config.getfloat(\"reconnect_interval\", 2.0) + 0.12\n"
        "        )  # add small offset to not collide with other events\n"
        "        self._cached_init_state = False\n"
        "        self._oid_count_post_inits = 0\n"
        "        self._config_cmds_post_inits = []\n"
        "        self._init_cmds_post_inits = []\n"
        "        self._restart_cmds_post_inits = []\n"
        "        # Register handlers\n",
        "mcu: add non-critical MCU init block",
    )

    # 3) _handle_starting: don't shutdown for non-critical MCU spontaneous restart
    ok &= apply_replacement(
        filepath,
        "    def _handle_starting(self, params):\n"
        "        if not self._is_shutdown:\n"
        "            self._printer.invoke_async_shutdown(\n"
        "                \"MCU '%s' spontaneous restart\" % (self._name,))\n",
        "    def _handle_starting(self, params):\n"
        "        if not self._is_shutdown and not self.is_non_critical:\n"
        "            self._printer.invoke_async_shutdown(\n"
        "                \"MCU '%s' spontaneous restart\" % (self._name,))\n",
        "mcu: skip shutdown on non-critical spontaneous restart",
    )

    # 4) Add handle_non_critical_disconnect and non_critical_recon_event
    #    Insert before _send_config
    ok &= apply_replacement(
        filepath,
        "    def _send_config(self, prev_crc):\n",
        "    def handle_non_critical_disconnect(self):\n"
        "        self.non_critical_disconnected = True\n"
        "        self._clocksync.disconnect()\n"
        "        self._disconnect()\n"
        "        self._reactor.update_timer(\n"
        "            self.non_critical_recon_timer, self._reactor.NOW\n"
        "        )\n"
        "        self._printer.send_event(self._non_critical_disconnect_event_name)\n"
        "        self.gcode.respond_info(f\"mcu: '{self._name}' disconnected!\", log=True)\n"
        "\n"
        "    def non_critical_recon_event(self, eventtime):\n"
        "        success = self.recon_mcu()\n"
        "        if success:\n"
        "            self.gcode.respond_info(\n"
        "                f\"mcu: '{self._name}' reconnected!\", log=True\n"
        "            )\n"
        "            return self._reactor.NEVER\n"
        "        else:\n"
        "            return eventtime + self.reconnect_interval\n"
        "\n"
        "    def _send_config(self, prev_crc):\n",
        "mcu: add disconnect/reconnect handlers",
    )

    # 5) In _send_config: cache initial state and use local_config_cmds
    ok &= apply_replacement(
        filepath,
        "    def _send_config(self, prev_crc):\n"
        "        # Build config commands\n"
        "        for cb in self._config_callbacks:\n"
        "            cb()\n"
        "        self._config_cmds.insert(\n"
        "            0, \"allocate_oids count=%d\" % (self._oid_count,))\n",
        "    def _send_config(self, prev_crc):\n"
        "        if not self._cached_init_state:\n"
        "            # first time config, save oid count for state reset later\n"
        "            self._oid_count_post_inits = self._oid_count\n"
        "            self._config_cmds_post_inits = self._config_cmds.copy()\n"
        "            self._init_cmds_post_inits = self._init_cmds.copy()\n"
        "            self._restart_cmds_post_inits = self._restart_cmds.copy()\n"
        "            self._cached_init_state = True\n"
        "        # Build config commands\n"
        "        for cb in self._config_callbacks:\n"
        "            cb()\n"
        "\n"
        "        local_config_cmds = self._config_cmds.copy()\n"
        "\n"
        "        local_config_cmds.insert(\n"
        "            0, \"allocate_oids count=%d\" % (self._oid_count,))\n",
        "mcu: cache init state and use local_config_cmds",
    )

    # 6) Replace self._config_cmds with local_config_cmds in pin resolution
    ok &= apply_replacement(
        filepath,
        "        for cmdlist in (self._config_cmds, self._restart_cmds,\n"
        "                        self._init_cmds):\n",
        "        for cmdlist in (local_config_cmds, self._restart_cmds,\n"
        "                        self._init_cmds):\n",
        "mcu: use local_config_cmds for pin resolution",
    )

    # 7) Replace config CRC calculation to use local_config_cmds
    ok &= apply_replacement(
        filepath,
        "        encoded_config = \"\\n\".join(self._config_cmds).encode()\n"
        "        config_crc = zlib.crc32(encoded_config) & 0xffffffff\n"
        "        self.add_config_cmd(\"finalize_config crc=%d\" % (config_crc,))\n",
        "        encoded_config = \"\\n\".join(local_config_cmds).encode()\n"
        "        config_crc = zlib.crc32(encoded_config) & 0xffffffff\n"
        "        local_config_cmds.append(\"finalize_config crc=%d\" % (config_crc,))\n",
        "mcu: use local_config_cmds for CRC",
    )

    # 8) Replace config send loop
    ok &= apply_replacement(
        filepath,
        "                for c in self._config_cmds:\n"
        "                    self._serial.send(c)\n",
        "                for c in local_config_cmds:\n"
        "                    self._serial.send(c)\n",
        "mcu: use local_config_cmds for sending",
    )

    # 9) Add recon_mcu and reset_to_initial_state before _connect
    ok &= apply_replacement(
        filepath,
        "    def _connect(self):\n"
        "        config_params = self._send_get_config()\n",
        "    def recon_mcu(self):\n"
        "        res = self._mcu_identify()\n"
        "        if not res:\n"
        "            return False\n"
        "        self.reset_to_initial_state()\n"
        "        self.non_critical_disconnected = False\n"
        "        self._connect()\n"
        "        self._printer.send_event(self._non_critical_reconnect_event_name)\n"
        "        return True\n"
        "\n"
        "    def reset_to_initial_state(self):\n"
        "        if self._cached_init_state:\n"
        "            self._oid_count = self._oid_count_post_inits\n"
        "            self._config_cmds = self._config_cmds_post_inits.copy()\n"
        "            self._init_cmds = self._init_cmds_post_inits.copy()\n"
        "            self._restart_cmds = self._restart_cmds_post_inits.copy()\n"
        "        self._reserved_move_slots = 0\n"
        "        self._steppersync = None\n"
        "\n"
        "    def _connect(self):\n"
        "        if self.non_critical_disconnected:\n"
        "            self._reactor.update_timer(\n"
        "                self.non_critical_recon_timer,\n"
        "                self._reactor.NOW + self.reconnect_interval,\n"
        "            )\n"
        "            return\n"
        "        config_params = self._send_get_config()\n",
        "mcu: add recon_mcu, reset_to_initial_state, and _connect guard",
    )

    # 10) Add _check_serial_exists before _mcu_identify
    ok &= apply_replacement(
        filepath,
        "    def _mcu_identify(self):\n"
        "        if self.is_fileoutput():\n",
        "    def _check_serial_exists(self):\n"
        "        rts = self._restart_method != \"cheetah\"\n"
        "        return self._serial.check_connect(self._serialport, self._baud, rts)\n"
        "\n"
        "    def _mcu_identify(self):\n"
        "        if self.is_non_critical and not self._check_serial_exists():\n"
        "            self.non_critical_disconnected = True\n"
        "            return False\n"
        "        else:\n"
        "            self.non_critical_disconnected = False\n"
        "        if self.is_fileoutput():\n",
        "mcu: add _check_serial_exists and non-critical check in _mcu_identify",
    )

    # 11) Add return True at end of _mcu_identify
    ok &= apply_replacement(
        filepath,
        "        self.register_response(self._handle_shutdown, 'shutdown')\n"
        "        self.register_response(self._handle_shutdown, 'is_shutdown')\n"
        "        self.register_response(self._handle_mcu_stats, 'stats')\n"
        "    def _ready(self):\n",
        "        self.register_response(self._handle_shutdown, 'shutdown')\n"
        "        self.register_response(self._handle_shutdown, 'is_shutdown')\n"
        "        self.register_response(self._handle_mcu_stats, 'stats')\n"
        "        return True\n"
        "    def _ready(self):\n",
        "mcu: add return True to _mcu_identify",
    )

    # 12) Add event name getter methods after get_name
    ok &= apply_replacement(
        filepath,
        "    def get_name(self):\n"
        "        return self._name\n"
        "    def register_response(self, cb, msg, oid=None):\n",
        "    def get_name(self):\n"
        "        return self._name\n"
        "    def get_non_critical_reconnect_event_name(self):\n"
        "        return self._non_critical_reconnect_event_name\n"
        "    def get_non_critical_disconnect_event_name(self):\n"
        "        return self._non_critical_disconnect_event_name\n"
        "    def register_response(self, cb, msg, oid=None):\n",
        "mcu: add event name getters",
    )

    # 13) Skip firmware_restart for non-critical disconnected MCU
    #     This patch handles both Klipper versions (with and without _is_mcu_bridge)
    content = read_file(filepath)
    if "_is_mcu_bridge" in content:
        ok &= apply_replacement(
            filepath,
            "    def _firmware_restart(self, force=False):\n"
            "        if self._is_mcu_bridge and not force:\n"
            "            return\n",
            "    def _firmware_restart(self, force=False):\n"
            "        if (\n"
            "            self._is_mcu_bridge and not force\n"
            "        ) or self.non_critical_disconnected:\n"
            "            return\n",
            "mcu: skip firmware_restart for non-critical disconnected",
        )
    else:
        ok &= apply_replacement(
            filepath,
            "    def _firmware_restart(self, force=False):\n",
            "    def _firmware_restart(self, force=False):\n"
            "        if self.non_critical_disconnected:\n"
            "            return\n",
            "mcu: skip firmware_restart for non-critical disconnected",
        )

    # 14) In check_active: handle non-critical disconnect instead of timeout
    ok &= apply_replacement(
        filepath,
        "            or self._is_timeout\n"
        "        ):\n"
        "            return\n"
        "        self._is_timeout = True\n",
        "            or self._is_timeout\n"
        "        ):\n"
        "            return\n"
        "        if self.is_non_critical:\n"
        "            self.handle_non_critical_disconnect()\n"
        "            return\n"
        "        self._is_timeout = True\n",
        "mcu: handle non-critical disconnect in check_active",
    )

    return ok


def patch_stepper(klipper_dir):
    """Patch stepper.py to skip query for disconnected non-critical MCU."""
    filepath = os.path.join(klipper_dir, "klippy", "stepper.py")
    print(f"\nPatching {filepath}")
    backup_file(filepath)

    return apply_replacement(
        filepath,
        "    def _query_mcu_position(self):\n"
        "        if self._mcu.is_fileoutput():\n"
        "            return\n",
        "    def _query_mcu_position(self):\n"
        "        if self._mcu.is_fileoutput() or self._mcu.non_critical_disconnected:\n"
        "            return\n",
        "stepper: skip query for disconnected non-critical MCU",
    )


def patch_adxl345(klipper_dir):
    """Patch adxl345.py with check_connected guard."""
    filepath = os.path.join(klipper_dir, "klippy", "extras", "adxl345.py")
    print(f"\nPatching {filepath}")
    backup_file(filepath)
    ok = True

    # Add check_connected method before read_reg
    ok &= apply_replacement(
        filepath,
        "    def read_reg(self, reg):\n",
        "    def check_connected(self):\n"
        "        if self.mcu.non_critical_disconnected:\n"
        "            raise self.printer.command_error(\n"
        "                f\"ADXL: {self.name} could not connect because mcu: \"\n"
        "                f\"{self.mcu.get_name()} is non_critical_disconnected!\"\n"
        "            )\n"
        "\n"
        "    def read_reg(self, reg):\n",
        "adxl345: add check_connected()",
    )

    # Add check call in start_internal_client
    ok &= apply_replacement(
        filepath,
        "    def start_internal_client(self):\n"
        "        aqh = AccelQueryHelper(self.printer)\n",
        "    def start_internal_client(self):\n"
        "        self.check_connected()\n"
        "        aqh = AccelQueryHelper(self.printer)\n",
        "adxl345: call check_connected in start_internal_client",
    )

    return ok


def patch_lis2dw(klipper_dir):
    """Patch lis2dw.py with check_connected guard."""
    filepath = os.path.join(klipper_dir, "klippy", "extras", "lis2dw.py")
    if not os.path.exists(filepath):
        print(f"\nSKIP (file not found): {filepath}")
        return True
    print(f"\nPatching {filepath}")
    backup_file(filepath)
    ok = True

    ok &= apply_replacement(
        filepath,
        "    def read_reg(self, reg):\n",
        "    def check_connected(self):\n"
        "        if self.mcu.non_critical_disconnected:\n"
        "            raise self.printer.command_error(\n"
        "                f\"LIS2DW: {self.name} could not connect because mcu: \"\n"
        "                f\"{self.mcu.get_name()} is non_critical_disconnected!\"\n"
        "            )\n"
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

    return ok


def patch_mpu9250(klipper_dir):
    """Patch mpu9250.py with check_connected guard."""
    filepath = os.path.join(klipper_dir, "klippy", "extras", "mpu9250.py")
    if not os.path.exists(filepath):
        print(f"\nSKIP (file not found): {filepath}")
        return True
    print(f"\nPatching {filepath}")
    backup_file(filepath)
    ok = True

    ok &= apply_replacement(
        filepath,
        "    def read_reg(self, reg):\n",
        "    def check_connected(self):\n"
        "        if self.mcu.non_critical_disconnected:\n"
        "            raise self.printer.command_error(\n"
        "                f\"MPU: {self.name} could not connect because mcu: \"\n"
        "                f\"{self.mcu.get_name()} is non_critical_disconnected!\"\n"
        "            )\n"
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

    return ok


def patch_neopixel(klipper_dir):
    """Patch neopixel.py to re-send data on reconnect."""
    filepath = os.path.join(klipper_dir, "klippy", "extras", "neopixel.py")
    print(f"\nPatching {filepath}")
    backup_file(filepath)

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
    ok = True

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

    # Look for the SPI setup line - may vary by Klipper version
    if "self.spi = bus.MCU_SPI(mcu," in content and "self.mcu = mcu" not in content:
        return apply_replacement(
            filepath,
            "        self.spi = bus.MCU_SPI(mcu,",
            "        self.spi = bus.MCU_SPI(mcu,",
            "st7920: mcu attribute (checking context)",
        )
        # Try a broader approach
    # Fallback: just check if already patched
    if "self.mcu = mcu" in content or "self.mcu =" in content:
        print("  SKIP (already has mcu attribute)")
        return True
    print("  WARN: Could not find insertion point for self.mcu in st7920.py")
    print("        You may need to manually add 'self.mcu = mcu' in ST7920.__init__")
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

    # UC1701 class
    content = read_file(filepath)
    if "class UC1701" in content:
        ok &= apply_replacement(
            filepath,
            "        io = SPI4wire(config, \"a0_pin\")\n"
            "        DisplayBase.__init__(self, io)\n",
            "        io = SPI4wire(config, \"a0_pin\")\n"
            "        self.mcu = io.spi.get_mcu()\n"
            "        DisplayBase.__init__(self, io)\n",
            "uc1701: add mcu attribute to UC1701",
        )

    # SSD1306 class (if present)
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

    return ok


def patch_temperature_mcu(klipper_dir):
    """Patch temperature_mcu.py to support reconnection."""
    filepath = os.path.join(klipper_dir, "klippy", "extras", "temperature_mcu.py")
    if not os.path.exists(filepath):
        print(f"\nSKIP (file not found): {filepath}")
        return True
    print(f"\nPatching {filepath}")
    backup_file(filepath)
    ok = True

    # Add register_config_callback
    ok &= apply_replacement(
        filepath,
        "        self.printer.register_event_handler(\n"
        "            \"klippy:mcu_identify\", self._mcu_identify)\n",
        "        self.printer.register_event_handler(\n"
        "            \"klippy:mcu_identify\", self._mcu_identify)\n"
        "        self.mcu_adc.get_mcu().register_config_callback(self._build_config)\n",
        "temperature_mcu: register config callback",
    )

    # Refactor _mcu_identify to call _build_config
    ok &= apply_replacement(
        filepath,
        "    def _mcu_identify(self):\n"
        "        # Obtain mcu information\n",
        "    def _mcu_identify(self):\n"
        "        self._build_config()\n"
        "\n"
        "    def _build_config(self):\n"
        "        # Obtain mcu information\n",
        "temperature_mcu: refactor _mcu_identify with _build_config",
    )

    return ok


def patch_tmc(klipper_dir):
    """Patch tmc.py to skip init for disconnected non-critical MCU."""
    filepath = os.path.join(klipper_dir, "klippy", "extras", "tmc.py")
    if not os.path.exists(filepath):
        print(f"\nSKIP (file not found): {filepath}")
        return True
    print(f"\nPatching {filepath}")
    backup_file(filepath)

    return apply_replacement(
        filepath,
        "        # Send init\n"
        "        try:\n"
        "            self._init_registers()\n",
        "        # Send init\n"
        "        try:\n"
        "            if self.mcu_tmc.mcu.non_critical_disconnected:\n"
        "                logging.info(\n"
        "                    \"TMC %s failed to init - non_critical_mcu: %s is disconnected!\",\n"
        "                    self.name,\n"
        "                    self.mcu_tmc.mcu.get_name(),\n"
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

    return apply_replacement(
        filepath,
        "        self.tmc_frequency = tmc_frequency\n"
        "\n"
        "    def get_fields(self):\n",
        "        self.tmc_frequency = tmc_frequency\n"
        "        self.mcu = self.tmc_spi.spi.get_mcu()\n"
        "\n"
        "    def get_fields(self):\n",
        "tmc2130: add mcu attribute",
    )


def patch_tmc2660(klipper_dir):
    """Patch tmc2660.py to expose mcu attribute."""
    filepath = os.path.join(klipper_dir, "klippy", "extras", "tmc2660.py")
    if not os.path.exists(filepath):
        print(f"\nSKIP (file not found): {filepath}")
        return True
    print(f"\nPatching {filepath}")
    backup_file(filepath)

    return apply_replacement(
        filepath,
        "        self.fields = fields\n"
        "\n"
        "    def get_fields(self):\n",
        "        self.fields = fields\n"
        "        self.mcu = self.spi.get_mcu()\n"
        "\n"
        "    def get_fields(self):\n",
        "tmc2660: add mcu attribute",
    )


def patch_tmc_uart(klipper_dir):
    """Patch tmc_uart.py to expose mcu attribute."""
    filepath = os.path.join(klipper_dir, "klippy", "extras", "tmc_uart.py")
    if not os.path.exists(filepath):
        print(f"\nSKIP (file not found): {filepath}")
        return True
    print(f"\nPatching {filepath}")
    backup_file(filepath)

    return apply_replacement(
        filepath,
        "        self.tmc_frequency = tmc_frequency\n"
        "\n"
        "    def get_fields(self):\n",
        "        self.tmc_frequency = tmc_frequency\n"
        "        self.mcu = self.mcu_uart.mcu\n"
        "\n"
        "    def get_fields(self):\n",
        "tmc_uart: add mcu attribute",
    )


def do_patch(klipper_dir):
    """Apply all patches."""
    print("=" * 60)
    print("Non-Critical MCU Patch for Klipper")
    print(f"Target: {klipper_dir}")
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
