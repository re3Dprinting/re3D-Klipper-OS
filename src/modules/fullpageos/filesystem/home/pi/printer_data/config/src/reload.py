#!/usr/bin/python3
import requests
import json
import os
import configparser
import subprocess

from setup_printer import COMMON_PATH, common_setup_printer, setup_fgf_printer, setup_fff_printer
from branch_check import VersionManager
from utils import *
from enums import *
from paths import *

url = "http://localhost"

def read_master_config():
    master_config_path = KLIPPER_CONFIG_PATH / ".master.cfg"
    if not master_config_path.exists():
        print(".master.cfg is missing! autogenerating to default")
        add_template_file(COMMON_PATH / "master.cfg", master_config_path)

    master_config = configparser.ConfigParser(inline_comment_prefixes="#")
    master_config.read(str(master_config_path))
    return master_config
    # for p in printer_config: print(p)

def wait_on_moonraker():
    print("Waiting on moonraker...")
    from requests.adapters import HTTPAdapter, Retry
    s = requests.Session()
    retries = Retry(total=5, backoff_factor=1, status_forcelist=[ 502, 503, 504 ])
    s.mount('http://', HTTPAdapter(max_retries=retries))
    s.get(url + "/server/database/list")

#Reloading UI configuration
def reload_ui():
    default_json_file = THEME_PATH / "default.json"
    with open(default_json_file) as f:
        defaults = json.load(f)
    namespaces = [name for name in defaults]

    for name in namespaces:
        default_value = defaults[name]
        print("{} Namespace Default: {}".format(name, default_value))
        print("--- DELETING {} NAMESPACE ---".format(name))
        delete_url = url+"/server/database/item?namespace=mainsail&key="+name
        delete_response = requests.delete(delete_url)
        print(delete_response.text)

        print("--- POSTING NEW DEFAULT INTO {} NAMESPACE ---".format(name))
        post_url = url+"/server/database/item"
        body = {"namespace": "mainsail", "key": name, "value": defaults[name]}
        post_response = requests.post(post_url, json=body)
        print(post_response.text)

def reboot_services():
    os.system("service moonraker restart")
    os.system("service klipper restart")

def main():
    master_config = read_master_config()

    # DEPOSITION TYPE DETERMINATION
    deposition_type = FFF if FFF in master_config else FGF
    
    if not deposition_type:
        print("fff or fgf section is not defined in master.cfg. Please enable one of the sections.")
        return

    printer_config = master_config[deposition_type]
    if not printer_config:
        print("Configuration section for {deposition_type} does not exist!")
        return
    
    # FIELD VALIDATION FOR DEPOSITION TYPE
    board = validate_and_return_config_param(field="board_type", config=printer_config, valid_selections=["azteeg", "archimajor"], default="archimajor")
    platform = validate_and_return_config_param(field="platform_type", config=printer_config, valid_selections=["regular", "xlt", "terabot"], default="regular")

    print("Setting up printer as a {} {} {} machine".format(deposition_type, board, platform))
    if deposition_type is FGF:
        setup_fgf_printer(printer_config, board, platform)
    elif deposition_type is FFF:
        setup_fff_printer(printer_config, board, platform)
    
    # SERIAL FILE GENERATION
    serial_out = subprocess.run([str(SRC_PATH / "get_serial.sh")], capture_output=True)
    print(serial_out.stdout.decode("utf-8"))

    #Validate Klipper Moonraker Branch Definition
    klipper_moonraker_config = master_config["klipper_moonraker"] if "klipper_moonraker" in master_config else {}
    klipper_moonraker_branch = klipper_moonraker_config.get("branch", "")

    print(f'INFO Master Config Branch set to "{klipper_moonraker_branch}"')
    if klipper_moonraker_branch not in {"stable", "develop"}: 
        print(f'WARN: \t "{klipper_moonraker_branch}" is invalid, defaulting to stable')
        klipper_moonraker_branch = "stable"

    #Check our network availability and validate klipper/moonraker branches
    if check_network_availability():
        v = VersionManager(klipper_moonraker_branch)
        v.validate_repository_hashes()

    #Check master config for ui_factory_reset option to factory reset UI components. 
    ui_regeneration = master_config["ui_regeneration"].get("enabled") if "ui_regeneration" in master_config else "true"
    if ui_regeneration != "false":
        reload_ui()
    else:
        print("INFO: UI Regeneration turned off! Skipping ui reload!")
    reboot_services()

if __name__ == "__main__":
    main()
