#!/bin/python3
import shutil
import os

from utils import *
from paths import *

#attempt to generate master.cfg from master.cfg.tmpl
#read in master.cfg

#set bedsize, get_bedsize.sh
#set serial, get_serial.sh (if currently plugged in usb is different override)
#set board, overwrite board_pinmap, board_specific

#add if doesnt exist - save_variables.cfg
#add if doesnt exist - standalone.cfg
#add if doesnt exist - wifi_setup.cfg

#overwrite - moonraker.cfg check git hash for correct moonraker.cfg

def add_platform_specific(platform, board, platform_path):
    platform_file = "{}_{}.cfg".format(board, platform)
    generate_platform = OUTPUT_PATH / platform_file
    template_platform = platform_path / platform_file
    add_template_file(template_platform, generate_platform, True) 

def add_board_pinmap(board):
    board_pinmap_file = board + "_pinmap.cfg"
    generate_board_pinmap = OUTPUT_PATH / board_pinmap_file
    template_board_pinmap = PINMAP_PATH / board_pinmap_file
    add_template_file(template_board_pinmap, generate_board_pinmap, True)


def add_board_specific(board, board_path):
    board_specific_file = board + "_specific.cfg"
    generate_board_specific= OUTPUT_PATH / board_specific_file
    template_board_specific = board_path / board_specific_file
    add_template_file(template_board_specific, generate_board_specific, True)

def add_config(config_path):
    if not is_valid_path(config_path): return 
    for config_file in config_path.iterdir():
        generate_file = OUTPUT_PATH / config_file.name
        add_template_file(config_file, generate_file, True)

def add_theme(theme_path):
    if not is_valid_path(theme_path): return 
    for theme_file in theme_path.iterdir():
        generate_file = THEME_PATH / theme_file.name
        add_template_file(theme_file, generate_file, True)

def common_setup_printer(deposition_type_path, board, platform):
    platform_path = deposition_type_path / "platform_specific"
    board_path = deposition_type_path / "board_specific"
    config_path = deposition_type_path / "config"
    theme_path = deposition_type_path / ".theme"
    if not is_valid_path(platform_path) or not is_valid_path(board_path):
        return
    
    # Wipe and recreate build path
    if is_valid_path(OUTPUT_PATH): 
        shutil.rmtree(OUTPUT_PATH)
    os.mkdir(OUTPUT_PATH)

    # Wipe and recreate .theme path
    if is_valid_path(THEME_PATH): 
        shutil.rmtree(THEME_PATH)
    os.mkdir(THEME_PATH)

    # Platform Setup
    add_platform_specific(platform, board, platform_path)
    # Board Pinmap Setup
    add_board_pinmap(board)
    # Board Specific Setup
    add_board_specific(board, board_path)
    #Rest of the config setup
    add_config(config_path)
    #Setup theme path
    add_theme(theme_path)

    #Common Files Setup
    add_template_file(COMMON_PATH / "save_variables.cfg", KLIPPER_CONFIG_PATH / "save_variables.cfg")
    add_template_file(COMMON_PATH / "standalone.cfg", KLIPPER_CONFIG_PATH / "standalone.cfg")
    add_template_file(COMMON_PATH / "macros.cfg", KLIPPER_CONFIG_PATH / "macros.cfg")
    add_template_file(COMMON_PATH / "wifi_setup.conf.tmpl", KLIPPER_CONFIG_PATH / "wifi_setup.conf")
    add_template_file(COMMON_PATH / "moonraker.conf.tmpl", KLIPPER_CONFIG_PATH / "moonraker.conf", True)
    add_template_file(COMMON_PATH / "crowsnest.conf.tmpl", KLIPPER_CONFIG_PATH / "crowsnest.conf", True)
    add_template_file(COMMON_PATH / "timelapse.cfg.tmpl", KLIPPER_CONFIG_PATH / "timelapse.cfg", True)
    add_template_file(COMMON_PATH / "shell_command.cfg.tmpl", KLIPPER_CONFIG_PATH / "shell_command.cfg", True)
    # StallGuard monitor — deploy once; operator tunes threshold in-place
    add_template_file(COMMON_PATH / "stallguard_standalone.cfg", KLIPPER_CONFIG_PATH / "stallguard_standalone.cfg")

def setup_fff_printer(printer_config, board, platform):
    custom_path = FFF_PATH / "custom"
    common_setup_printer(FFF_PATH, board, platform)

    mesh_compensation_enabled = validate_and_return_config_param(field="mesh_compensation_enabled", config=printer_config, valid_selections=["true", "false"], default="false")
    if mesh_compensation_enabled == "true":
        add_template_file( custom_path / "fff_mesh.cfg", OUTPUT_PATH / "fff_mesh.cfg", False)
        add_template_file( custom_path / "fff_mesh_z.cfg", OUTPUT_PATH / "fff_mesh_z.cfg", False)
        # Add platform-specific mesh include depending on selected platform
        # If platform is 'regular' and mesh compensation enabled -> include mesh_regular.cfg
        # If platform is 'xlt' and mesh compensation enabled -> include mesh_xlt.cfg
        platform_mesh_file = f"mesh_{platform}.cfg"
        platform_mesh_template = FFF_PATH / "platform_specific" / platform_mesh_file
        # Only add the platform mesh file if it exists in the platform_specific folder
        if is_valid_path(platform_mesh_template):
            add_template_file(platform_mesh_template, OUTPUT_PATH / platform_mesh_file, False)
    else:
        add_template_file( custom_path / "fff_standard_z.cfg", OUTPUT_PATH / "fff_standard_z.cfg", False)

def setup_fgf_printer(printer_config, board, platform):
    custom_path = FGF_PATH / "custom"
    common_setup_printer(FGF_PATH, board, platform)

    crammer_enabled = validate_and_return_config_param(field="crammer_enabled", config=printer_config, valid_selections=["true", "false"], default="false")
    if crammer_enabled == "true":
        add_template_file( custom_path / "fgf_crammer.cfg", OUTPUT_PATH / "fgf_crammer.cfg", False)

    heater_bed_enabled = validate_and_return_config_param(field="heater_bed_enabled", config=printer_config, valid_selections=["true", "false"], default="true")
    if heater_bed_enabled == "true":
        add_template_file( custom_path / "fgf_heater_bed.cfg", OUTPUT_PATH / "fgf_heater_bed.cfg", False)


