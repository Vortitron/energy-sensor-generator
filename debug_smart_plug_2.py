#!/usr/bin/env python3
"""
Debug script to understand why smart_plug_power_2 entities aren't being created
"""

import sys
import os

# Add the current directory to the path so we can import from the integration
sys.path.insert(0, os.path.join(os.getcwd(), 'custom_components', 'energy_sensor_generator'))

def simulate_entity_processing():
    # Simulate what happens during sensor processing
    power_sensors = ['sensor.smart_plug_power', 'sensor.smart_plug_power_2']
    
    print("=== SIMULATING ENTITY PROCESSING ===")
    print()
    
    for sensor in power_sensors:
        print(f"Processing: {sensor}")
        
        # This is the exact logic from generate_sensors_service
        base_name = sensor.replace("sensor.", "").replace("_power", "")
        base_name = base_name.lower()
        
        print(f"  -> base_name: '{base_name}'")
        print(f"  -> Expected main entity: sensor.{base_name}_energy")
        print(f"  -> Expected daily entity: sensor.{base_name}_daily_energy")
        print(f"  -> Expected unique_id: {base_name}_energy")
        print()

if __name__ == "__main__":
    simulate_entity_processing()
