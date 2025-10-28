#!/bin/bash

# The path to your .master.cfg file
config_file="/home/pi/printer_data/config/.master.cfg"

# Function to update the section and configuration parameters
update_config() {
    local section=$1
    local platform_type=$2
    local board_type=$3
    local crammer_enabled=$4
    local heater_bed_enabled=$5
    local mesh_compensation_enabled=$6

    # Replace the entire file content with the new section and parameters
    cat > $config_file <<EOF
[$section]
platform_type=$platform_type
board_type=$board_type
crammer_enabled=$crammer_enabled
heater_bed_enabled=$heater_bed_enabled
mesh_compensation_enabled=$mesh_compensation_enabled
EOF
}

# Function to set parameters based on the input number
set_combination() {
    local choice=$1
    
    case $choice in
        1)
            update_config "fff" "regular" "archimajor" "false" "true" "false"
            ;;
        2)
            update_config "fff" "xlt" "archimajor" "false" "true" "false"
            ;;
        3)
            update_config "fff" "terabot" "archimajor" "false" "true" "false"
            ;;
        4)
            update_config "fgf" "regular" "archimajor" "false" "true" "false"
            ;;
        5)
            update_config "fgf" "xlt" "archimajor" "false" "true" "false"
            ;;
        6)
            update_config "fgf" "terabot" "archimajor" "false" "true" "false"
            ;;
        7)
            update_config "fgf" "regular" "archimajor" "true" "true" "false"
            ;;
        8)
            update_config "fgf" "xlt" "archimajor" "true" "true" "false"
            ;;
        9)
            update_config "fgf" "terabot" "archimajor" "true" "true" "false"
            ;;
        # Add more combinations as needed
        *)
            echo "Invalid combination number. Please enter a number between 1-9."
            exit 1
            ;;
    esac

    echo "Configuration updated to combination $choice successfully."
}

# Check if an argument is provided
if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <combination_number>"
    exit 1
fi

# Call the set_combination function with the input number
set_combination $1
