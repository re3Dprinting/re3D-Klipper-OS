import configparser
from pathlib import Path
import unittest

import jinja2


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "src/modules/fullpageos/filesystem/home/pi/printer_data/config"


class CoreXYConfigTests(unittest.TestCase):
    def setUp(self):
        self.config = configparser.ConfigParser(interpolation=None)
        self.config.read(CONFIG_PATH / "printer.cfg")

    def test_only_serial_and_pinmap_are_included(self):
        includes = [section for section in self.config if section.startswith("include ")]
        self.assertEqual(includes, [
            "include src/common/board_pinmap/archimajor_pinmap.cfg",
            "include build/_serial.cfg",
        ])
        self.assertTrue((CONFIG_PATH / includes[0][8:]).is_file())
        self.assertEqual(
            {section for section in self.config if section.startswith("tmc5160 ")},
            {"tmc5160 stepper_x", "tmc5160 stepper_y"},
        )
        self.assertFalse(any(section.startswith(
            ("extruder", "heater", "temperature_", "probe", "beacon", "delayed_gcode")
        ) for section in self.config))
        for section in ("stepper_y1", "stepper_z1", "force_move",
                        "safe_z_home", "bed_mesh"):
            self.assertNotIn(section, self.config)

    def test_corexy_belts_and_motor_ports(self):
        self.assertEqual(self.config["printer"]["kinematics"], "corexy")
        self.assertEqual(self.config.getfloat("printer", "max_velocity"), 50)
        self.assertEqual(self.config.getfloat("printer", "max_accel"), 300)
        for axis, direction in (("x", "XDIR"), ("y", "!YDIR")):
            with self.subTest(axis=axis):
                stepper = self.config["stepper_" + axis]
                self.assertEqual(stepper.getfloat("rotation_distance"), 3 * 20)
                self.assertEqual(stepper.getint("microsteps"), 16)
                self.assertEqual(stepper["step_pin"], axis.upper() + "STEP")
                self.assertEqual(stepper["dir_pin"], direction)
                self.assertEqual(stepper["enable_pin"], "!STEP_EN")
                self.assertEqual(stepper["endstop_pin"], "^!" + axis.upper() + "STOP")
                self.assertEqual(stepper.getfloat("homing_speed"), 10)
                driver = self.config["tmc5160 stepper_" + axis]
                self.assertEqual(driver["cs_pin"], axis.upper() + "CS")
                self.assertEqual(driver.getfloat("run_current"), 1.4)
                self.assertEqual(driver.getfloat("sense_resistor"), 0.050)

    def test_stock_limits_and_unhomed_z(self):
        for axis, endstop, maximum, positive in (
            ("x", 0, 590, False), ("y", 610, 610, True),
        ):
            stepper = self.config["stepper_" + axis]
            self.assertEqual(stepper.getfloat("position_endstop"), endstop)
            self.assertEqual(stepper.getfloat("position_max"), maximum)
            self.assertEqual(stepper.getboolean("homing_positive_dir"), positive)
        self.assertIn("stepper_z", self.config)
        self.assertEqual(self.config["stepper_z"]["step_pin"], "ZSTEP")
        self.assertEqual(self.config["homing_override"]["axes"], "xyz")
        self.assertFalse(any(
            key.startswith("set_position_") for key in self.config["homing_override"]
        ))

    def test_homing_only_requests_selected_xy_axes(self):
        environment = jinja2.Environment(
            variable_start_string="{", variable_end_string="}",
            undefined=jinja2.StrictUndefined,
        )
        template = environment.from_string(self.config["homing_override"]["gcode"])

        def reject_z(message):
            raise RuntimeError(message)

        for params, expected in (
            ({}, ["G28 X Y"]),
            ({"X": ""}, ["G28 X"]),
            ({"Y": ""}, ["G28 Y"]),
            ({"X": "0", "Y": "0"}, ["G28 X", "G28 Y"]),
            ({"O": ""}, ["G28 X Y"]),
        ):
            with self.subTest(params=params):
                rendered = template.render(params=params, action_raise_error=reject_z)
                self.assertEqual(
                    [line.strip() for line in rendered.splitlines() if line.strip()],
                    expected,
                )
        for params in ({"Z": ""}, {"X": "", "Z": ""}, {"Y": "", "Z": ""},
                       {"X": "", "Y": "", "Z": ""}):
            with self.subTest(params=params):
                with self.assertRaisesRegex(RuntimeError, "Z homing is disabled"):
                    template.render(params=params, action_raise_error=reject_z)


if __name__ == "__main__":
    unittest.main()
