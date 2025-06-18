# Energy Sensor Generator

A Home Assistant custom integration that automatically generates kWh energy sensors from power sensors (in Watts) for use in the Energy dashboard. Unlike other solutions, it operates entirely in Python, avoiding YAML-based helpers like `integration` (Riemann Sum) or `utility_meter`, making it tidy, self-contained, and easy to manage.

[![Add to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=vortitron&repository=energy-sensor-generator&category=integration)

## Features
- **Automatic Detection**: Identifies all power sensors (`unit: W` or `kW`, `device_class: power`) in Home Assistant.
- **Smart Energy Calculation**: Uses Home Assistant's statistical data for accurate energy calculations, especially for intermittent loads like heaters, with fallback to trapezoidal rule.
- **Statistical Accuracy**: For peaky/intermittent devices (hot water heaters, dishwashers), uses mean power values from Home Assistant's recorder instead of point sampling to avoid missing energy consumption.
- **Daily and Monthly Tracking**: Tracks energy usage with automatic daily and monthly resets, replacing the `utility_meter` helper.
- **Flexible Generation**: Supports automatic sensor creation on startup or manual triggering via a UI button.
- **Energy Dashboard Compatibility**: Generates sensors with `device_class: energy` and `state_class: total_increasing` for seamless integration.
- **Persistent Storage**: Saves energy data to a JSON file to survive Home Assistant restarts.
- **No Dependencies**: Pure Python implementation, no need for MQTT, Node-RED, or external integrations.
- **HACS Ready**: Easily installed via the Home Assistant Community Store (HACS).

## How It Works
The `energy_sensor_generator` integration simplifies energy monitoring by creating kWh sensors for devices like Tuya smart plugs that report power in Watts. Here's a detailed breakdown of its operation:

1. **Power Sensor Detection**:
   - The integration scans the Home Assistant entity registry for sensors with `unit_of_measurement: W` or `kW` and `device_class: power` (e.g., `sensor.plug_1_power`).
   - It supports any device providing power data, such as Tuya plugs via LocalTuya or other integrations.

2. **Energy Calculation**:
   - For each power sensor, it creates a custom `EnergySensor` that calculates kWh by integrating power over time.
   - **NEW**: The integration now intelligently chooses between two calculation methods:
     - **Statistical Method** (Primary): Uses Home Assistant's statistical data (mean power values) from the recorder database for highly accurate energy calculations, especially for intermittent/peaky devices like hot water heaters.
     - **Point Sampling Method** (Fallback): Uses the traditional trapezoidal rule with instantaneous power readings when statistical data is unavailable.
   - **Why Statistical is Better**: For devices that cycle on/off frequently (heaters, dishwashers), point sampling every 60 seconds can miss significant energy consumption. Statistical calculation uses the mean power over time periods, capturing all energy usage regardless of when sampling occurs.
   - Energy values are updated at regular intervals (default 60 seconds) with the most accurate method available.
   - Data is stored in a JSON file (`.storage/energy_sensor_generator.json`) to persist across restarts.

3. **Daily and Monthly Tracking**:
   - For each power sensor, it creates `DailyEnergySensor` and `MonthlyEnergySensor` entities (e.g., `sensor.plug_1_daily_energy`, `sensor.plug_1_monthly_energy`).
   - These sensors track the cumulative kWh from the `EnergySensor` and reset automatically at midnight (daily) or the 1st of the month (monthly).
   - Reset times and values are stored in the JSON file for continuity.

4. **Sensor Generation**:
   - **Automatic Mode**: If enabled in the config flow, sensors are generated when Home Assistant starts or the integration is reloaded.
   - **Manual Mode**: Users can trigger generation via a Lovelace button that calls the `energy_sensor_generator.generate_sensors` service.
   - Duplicate sensors are avoided using unique IDs based on the power sensor's name.

5. **Energy Dashboard Integration**:
   - All generated sensors have `device_class: energy`, `state_class: total_increasing`, and `unit_of_measurement: kWh`, making them compatible with the Home Assistant Energy dashboard.
   - Users can add `*_daily_energy` or `*_monthly_energy` sensors to the dashboard for visualization.

6. **Storage and Performance**:
   - Energy data is saved in `.storage/energy_sensor_generator.json`, with minimal disk usage (a few KB per sensor).
   - The integration uses efficient event handling (`async_track_state_change_event`) to monitor power sensor updates, scaling well for multiple devices.
   - Update frequency is configurable via the `update_interval` setting (default: 60 seconds).

## Requirements
- **Home Assistant**: Version 2023.6.0 or later.
- **Power Sensors**: Devices (e.g., Tuya smart plugs) that expose power sensors with `unit: W` or `kW` and `device_class: power`. LocalTuya is recommended for Tuya plugs due to faster updates.
- **HACS**: For easy installation (manual installation is also supported).
- **No External Dependencies**: No MQTT, Node-RED, or additional integrations required.

## Installation
Follow these steps to install the `energy_sensor_generator` integration:

1. **Add to HACS**:
   - Open Home Assistant and navigate to **HACS > Integrations > Explore & Download Repositories**.
   - Click the three-dot menu and select **Custom Repositories**.
   - Add the repository: `https://github.com/vortitron/energy-sensor-generator`, Category: **Integration**.
   - Search for "Energy Sensor Generator" and click **Download**.
   - Restart Home Assistant after downloading.

2. **Manual Installation (Alternative)**:
   - Download the [latest release](https://github.com/vortitron/energy-sensor-generator/releases) or clone the repository.
   - Copy the `custom_components/energy_sensor_generator` folder to your Home Assistant `config/custom_components/` directory.
   - Restart Home Assistant.

3. **Configure the Integration**:
   - Go to **Settings > Devices & Services > Add Integration**.
   - Search for "Energy Sensor Generator" and select it.
   - Configure the options:
     - **Auto Generate**: Enable to create sensors automatically on startup (recommended).
     - **Update Interval**: Set the frequency (in seconds) for energy calculations (default: 60).
   - Click **Submit** to complete setup.

4. **Add a Lovelace Button** (Optional for Manual Mode):
   - Edit your Lovelace dashboard (**Edit Dashboard > Add Card**).
   - Choose **Button** card and configure it:
     ```yaml
     type: button
     name: Generate Energy Sensors
     show_icon: true
     icon: mdi:lightning-bolt
     tap_action:
       action: call-service
       service: energy_sensor_generator.generate_sensors
     ```
   - Save the dashboard.

5. **Add Sensors to Energy Dashboard**:
   - Go to **Settings > Dashboards > Energy**.
   - Under **Individual Devices**, click **Add Device**.
   - Select the generated sensors (e.g., `sensor.plug_1_daily_energy`, `sensor.plug_1_monthly_energy`).
   - Save to display energy usage in the dashboard.

## Usage
- **Automatic Generation**:
  - If "Auto Generate" is enabled, sensors are created when Home Assistant starts or the integration is reloaded.
  - New power sensors (e.g., from newly added Tuya plugs) are detected automatically.
- **Manual Generation**:
  - Click the "Generate Energy Sensors" button in your Lovelace dashboard to create or update sensors.
  - This is useful if you add new devices and have "Auto Generate" disabled.
- **Device Swap Handling**:
  - If you swap a device between power sockets, use the `Reassign Energy Data` service to transfer energy data to the new device association.
  - Call the service `energy_sensor_generator.reassign_energy_data` with parameters `from_device` and `to_device` (e.g., `plug_1` to `plug_2`).
  - This updates the storage file to associate energy data with the new device name.
- **Monitoring**:
  - View sensor states in **Developer Tools > States** (e.g., `sensor.plug_1_energy`, `sensor.plug_1_daily_energy`).
  - Check energy usage in the **Energy** dashboard.
- **Logs**:
  - Monitor integration activity in **Settings > System > Logs**. Look for messages like:
    ```
    2025-05-11 10:00:00 INFO Generating energy sensors
    2025-05-11 10:00:01 INFO Reset daily energy for plug_1
    ```

## Example Output
For a power sensor `sensor.plug_1_power`, the integration creates:
- `sensor.plug_1_energy`: Cumulative kWh (e.g., `5.23 kWh`).
- `sensor.plug_1_daily_energy`: Daily kWh, resets at midnight (e.g., `0.45 kWh`).
- `sensor.plug_1_monthly_energy`: Monthly kWh, resets on the 1st (e.g., `12.67 kWh`).

Storage file (`.storage/energy_sensor_generator.json`):
```json
{
  "plug_1_energy": {"value": 5.23},
  "plug_1_daily_energy": {"value": 0.45, "last_reset": "2025-05-11T00:00:00"},
  "plug_1_monthly_energy": {"value": 12.67, "last_reset": "2025-05-01T00:00:00"}
}
```

## Troubleshooting

### Debug Logging
- **NEW**: You can now enable detailed debug logging to help troubleshoot issues without needing a new release
- Debug logging can be enabled during initial setup or toggled later in the integration options
- When enabled, detailed calculation logs will be written to help diagnose energy calculation issues
- Debug logging includes:
  - Statistical vs point sampling calculation details
  - Energy calculation breakdowns (power values, time deltas, conversion factors)
  - Sensor detection and unit conversion information
  - Interval update timing and calculation methods
- **To enable**: Go to **Settings > Devices & Services > Energy Sensor Generator > Configure** and toggle "Debug Logging"
- Debug messages appear in **Settings > System > Logs** with the "DEBUG:" prefix
- **Note**: Disable debug logging once issues are resolved to prevent log spam

### Database Access Issues (RESOLVED ✅)
- **Fixed in v0.0.47**: All database access now uses the proper `recorder.get_instance(hass).async_add_executor_job()` method
- **No more frame warnings**: The integration now follows Home Assistant's best practices for database operations
- **Improved reliability**: Historical data access is now completely async and non-blocking
- **Better performance**: Uses the recorder's optimised database connection pool for all operations

### Statistical Calculation Issues (RESOLVED)
- **✅ FIXED**: The previous blocking database call issues have been resolved in version 0.0.47
- **Historical Data Access**: The integration now properly uses `async_add_executor_job` to access Home Assistant's recorder data without blocking calls
- **Enhanced Accuracy**: Statistical calculation now uses the same historical data that the Energy Dashboard uses, providing much more accurate energy calculations for devices with infrequent updates
- **Automatic Fallback**: If historical data is unavailable, the integration gracefully falls back to point sampling
- **Configuration**: Statistical calculation is enabled by default but can be disabled in integration options if needed
- **Perfect for Tuya Devices**: Now properly handles devices that only update every 5+ minutes by using historical state data instead of just current power readings

### Sensors Not Available During Startup:
- **New in v0.0.28**: The integration is now resilient to source power sensors not being available during Home Assistant startup
- Energy sensors are created even if source sensors aren't ready yet, and will begin tracking once sources become available
- Look for log messages like "Source sensor not yet available during startup" - this is normal and expected
- Energy tracking will begin automatically once the source power sensors finish loading
- If sensors remain unavailable after several minutes, check that the devices are properly configured and online

### No Power Sensors Found:
Ensure your devices (e.g., Tuya plugs) expose power sensors with unit: W or kW and device_class: power. Check in Developer Tools > States.
Use the LocalTuya integration for Tuya devices, as the official Tuya integration may not expose power data reliably.
Energy Values Incorrect:
Verify that power sensors update frequently (e.g., every 5-30 seconds). LocalTuya typically provides faster updates.
Adjust the update_interval in the integration settings to match your device's update frequency.
Check logs for warnings about invalid power values.
Storage Errors:
Ensure the .storage directory is writable: chmod -R 755 /config/.storage.
Monitor disk space to prevent write failures.
Energy Dashboard Issues:
Confirm sensors have device_class: energy and state_class: total_increasing in Developer Tools > States.
If the dashboard doesn't display data, try restarting Home Assistant or re-adding the sensors.
General Issues:
Check logs in Settings > System > Logs for errors.
Reinstall the integration via HACS if issues persist.
Report bugs or ask for help in the GitHub Issues section.
Limitations
Power Sensor Update Frequency:
Energy calculation accuracy depends on how often power sensors update. Tuya plugs using the official integration may update sporadically; LocalTuya is recommended.
Storage:
The JSON storage file grows with the number of sensors but remains small (a few KB per sensor). Monitor disk space for large installations.
Energy Dashboard:
If Home Assistant's Energy dashboard rejects custom sensors (unlikely), a future version may add MQTT support as a fallback.
Contributing
Contributions are welcome! To contribute:
Fork the repository.
Create a branch for your feature or bug fix.
Submit a pull request with a clear description of changes.
Report issues or suggest features in the GitHub Issues section.
License
This project is licensed under the MIT License. See the LICENSE file for details.
Credits
Developed by [vortitron]. Inspired by the Home Assistant community's need for a clean, Python-based energy monitoring solution.
Enjoy tracking your energy usage with a tidy, all-in-one integration!

---

### Changelog

#### Version 0.0.47
- **🎉 FIXED: Resolved All Blocking Database Call Issues**: Successfully implemented proper async access to Home Assistant's historical data using `recorder.get_instance(hass).async_add_executor_job()`
- **⚡ Statistical Calculation Re-Enabled**: Now properly accesses the same historical data that the Energy Dashboard uses, providing much more accurate energy calculations
- **🔧 Enhanced Historical Data Access**: Uses `homeassistant.components.recorder.history.get_significant_states` to access real sensor data history with proper time filtering
- **📊 Improved Accuracy for Tuya Devices**: Now properly handles devices that only update every 5+ minutes by using historical state data for energy calculations
- **🐛 Robust Fallback System**: Gracefully falls back to point sampling if historical data is unavailable, ensuring reliability
- **📝 Updated Configuration Options**: Statistical calculation is now enabled by default with proper async implementation
- **🏗️ Database Access Fix**: Uses the specific recorder database executor to prevent frame warnings about direct database access

#### Version 0.0.46
- **🚨 CRITICAL FIX: Resolved Blocking Database Calls**: Fixed "Caught blocking call to _do_get_db_connection_protected" errors that were causing integration failures
- **⚡ Statistical Calculation Temporarily Disabled**: Due to Home Assistant's recorder API blocking call issues, statistical calculation was disabled by default
- **🔧 New Configuration Options**: Added toggle for statistical calculation in integration options (experimental/advanced users only)
- **🐛 Point Sampling Fallback**: Integration now reliably falls back to point sampling which provides accurate energy calculations
- **📊 Enhanced Debug Logging**: Added debug logging toggle option to help troubleshoot issues without requiring new releases
- **📝 Improved Documentation**: Added comprehensive troubleshooting section covering debug logging and statistical calculation issues

#### Version 0.0.45
- **🚀 MAJOR FEATURE: Statistical Energy Calculation**: Integration now uses Home Assistant's statistical data (mean power values) from the recorder database for highly accurate energy calculations
- **📊 Perfect for Intermittent Loads**: Especially beneficial for devices like hot water heaters, dishwashers, and other appliances that cycle on/off frequently
- **🎯 Eliminates Missed Energy**: Statistical method captures all energy consumption regardless of sampling timing, solving the problem where point sampling could miss energy from devices that turn off between samples
- **🔄 Intelligent Fallback**: Automatically falls back to traditional trapezoidal rule when statistical data is unavailable
- **📈 Enhanced Precision**: Improved decimal precision consistency across all sensor types (4 decimal places)
- **🛠️ New Diagnostic Service**: Added `diagnose_sensor` service for detailed sensor troubleshooting
- **📋 Better Diagnostics**: Enhanced sensor attributes showing calculation method used, statistical calculation status, and more detailed source sensor information
- **🔧 Improved Logging**: Better unit detection logging and special tracking for very small power values
- **⚙️ Configurable**: Statistical calculation can be disabled if needed via integration options

#### Version 0.0.34
- **Fixed kilowatt (kW) support**: The integration now properly detects power sensors with kW units (kW, kilowatt, kilowatts) and applies the correct conversion factor
- Power sensors in kW no longer get incorrectly divided by 1000, preventing energy calculation errors
- Enhanced power sensor detection to include kW units in the automatic scanning logic
- Improved debug logging to show the correct unit (kW or W) in energy calculation messages
- Added proper unit conversion handling for both Watts (W) and kilowatts (kW) power sensors

#### Version 0.0.21
- Improved device integration by attaching energy sensors directly to their source device
- Energy sensors now appear under the same device as the power sensor they monitor
- Better mimics built-in Home Assistant helper behavior with energy entities

#### Version 0.0.20
- Added device registration to sensors for better Energy Dashboard integration
- Completely rewrote options flow checkbox implementation for better compatibility
- Fixed issue where custom sensors weren't being properly displayed
- Associated energy sensors with their source devices when possible

#### Version 0.0.19
- Fixed display of options flow to properly use checkboxes
- Improved energy sensor unit implementation for Energy Dashboard compatibility
- Added proper native unit measurements using UnitOfEnergy constants

#### Version 0.0.18
- Fixed Energy Dashboard compatibility by using proper SensorStateClass enum
- Improved sensor attribute handling for better dashboard integration

#### Version 0.0.17
- Improved power sensor selection UI to use checkboxes instead of a comma-separated list
- Custom sensors are now added directly to the checkbox list for easier selection
- Unified the sensor selection system to make it more intuitive

#### Version 0.0.23
- **CRITICAL FIX**: Resolved double counting issue that was causing energy readings approximately double what they should be
- Removed redundant periodic power sensor sampling that was triggering duplicate calculations
- Modified state change handling to only track power values without performing calculations
- Added concurrent calculation protection to prevent overlapping energy calculations
- Added new `reset_energy_sensors` service to help correct previously doubled values
- Increased default sampling interval to 60 seconds for consistency
- Energy calculations are now handled exclusively by interval timers to ensure accuracy

#### Version 0.0.24
- **RELOAD FIX**: Fixed issue where existing sensors weren't properly linked to the entity platform during integration reload
- Improved async_setup_entry to recreate and re-add existing entities ensuring they remain functional after reload
- Enhanced error handling and device linking for more robust sensor management
- Better handling of sensor recreation during integration restarts and reloads

#### Version 0.0.25
- **CRITICAL FIX**: Fixed energy counting not starting after sensor recreation during reload
- Sensors now properly initialise with current power readings when first added or recreated
- Added detailed debug logging to help diagnose energy calculation issues
- Ensures energy calculations begin immediately rather than waiting for first power state change

#### Version 0.0.26
- **FRIENDLY NAMES**: Energy sensors now use device/entity friendly names instead of entity IDs
- If a power sensor is named "Hot Water" the energy sensor becomes "Hot Water Energy" instead of "smart_plug_2 Energy"  
- Improved name resolution to check entity registry names, device names, and friendly names
- Falls back gracefully to formatted entity ID names if no friendly name is available
- Makes energy sensors much more user-friendly in the Energy dashboard and UI

#### Version 0.0.27
- **STARTUP FIX**: Fixed issue where sensors weren't loading during Home Assistant restart
- Added delayed sensor generation with retry mechanism to ensure sensor platform is ready
- Fixed YAML formatting in services.yaml (removed tab characters that caused parsing errors)
- Improved startup timing and error handling for more reliable sensor creation
- Better logging to help diagnose startup issues

#### Version 0.0.28
- **STARTUP RESILIENCE**: Major improvement to startup reliability when source power sensors are not yet available
- Integration now assumes selected sensors will become available rather than failing immediately during startup
- Energy sensors are created regardless of source sensor availability and gracefully handle when sources become available
- Added proper initialisation handling when source sensors become available after energy sensor creation
- Improved logging to distinguish between startup delays vs actual missing sensors
- Fixed issues where integration would fail to create energy sensors during Home Assistant restart due to sensor loading order

#### Version 0.0.16
- Enhanced power sensor detection using more flexible matching criteria
- Added entity auto-complete for custom power sensor selection
- Improved UI with proper entity selector that shows all available sensors
- Smart detection for entities containing "_power" in their names

#### Version 0.0.15
- Added support for manually specifying custom power sensor entities
- You can now enter comma-separated entity IDs for power sensors that aren't automatically detected
- Improved sensor selection interface with both multi-select and text input options

#### Version 0.0.14
- Fixed options flow implementation to match Home Assistant's expected patterns
- Corrected initialization of the options flow class

#### Version 0.0.13
- Fixed options flow implementation to be compatible with future Home Assistant releases
- Fixed validation in options flow for selecting power sensors

#### Version 0.0.12
- Bug fixes for sensor creation

#### Version 0.0.11
- Added options flow: you can now manually select which power sensors should have kWh/energy entities generated via the integration's settings in Home Assistant.

#### Version 0.0.10
- Fixed issue with sensor entity creation where the async_add_entities callback wasn't properly stored and accessed
- Improved reliability of automatic sensor generation

#### Version 0.0.9
- Initial release

---

### Lovelace Button Configuration
As requested, here's the Lovelace button configuration to add to your Home Assistant dashboard for triggering the sensor generation manually. This was included in the `README.md` but is provided here separately for clarity and to ensure it's easy to copy-paste.

**YAML for the Button Card**:
```yaml
type: button
name: Generate Energy Sensors
show_icon: true
icon: mdi:lightning-bolt
tap_action:
  action: call-service
  service: energy_sensor_generator.generate_sensors
```

**YAML for Reassign Energy Data Button**:
```yaml
type: button
name: Reassign Energy Data
show_icon: true
icon: mdi:swap-horizontal
tap_action:
  action: call-service
  service: energy_sensor_generator.reassign_energy_data
  service_data:
    from_device: plug_1
    to_device: plug_2
```
Steps to Add the Button:
Open your Lovelace dashboard in edit mode (Edit Dashboard).
Click Add Card and select Button.
In the UI editor:
Set Name to Generate Energy Sensors.
Enable Show Icon and set Icon to mdi:lightning-bolt.
Set Action to Call Service.
Select energy_sensor_generator.generate_sensors as the service.
Save the card.
Alternatively, if using YAML mode, add the above YAML to your lovelace.yaml or dashboard configuration.
Save and reload the dashboard to see the button.
Usage:
Click the button to trigger the energy_sensor_generator.generate_sensors service.
The integration will scan for power sensors and create or update the corresponding kWh sensors (*_energy, *_daily_energy, *_monthly_energy).
Check Developer Tools > States or the Energy dashboard to confirm the new sensors.

## Installation (HACS)

1. Open HACS in Home Assistant
2. Go to "Integrations"
3. Click the three dots (⋯) in the top right corner
4. Select "Custom repositories"
5. Add this repository URL: `https://github.com/Vortitron/energy-sensor-generator`
6. Select "Integration" as the category
7. Click "Add"
8. Search for "Energy Sensor Generator" and install it
9. Restart Home Assistant
10. Go to Settings → Devices & Services → Add Integration
11. Search for "Energy Sensor Generator" and set it up

## Troubleshooting

### kW Sensors Not Being Added

If you have power sensors with kW units that aren't being detected or added:

1. **Check sensor detection**: Call the debug service by going to Developer Tools → Services and calling:
   - Service: `energy_sensor_generator.debug_sensor_detection`
   - This will log detailed information about detected sensors

2. **Check the logs**: Look in Settings → System → Logs for messages from `energy_sensor_generator` showing:
   - Which sensors were detected and why
   - Whether kW sensors were found specifically
   - Any errors during sensor creation

3. **Manual selection**: In the integration's options (Settings → Devices & Services → Energy Sensor Generator → Configure), you can manually select the kW power sensors that should have energy sensors created

4. **Verify sensor attributes**: Check that your kW power sensors have:
   - `unit_of_measurement: kW` (or `kilowatt`/`kilowatts`)
   - `device_class: power` (optional but helpful)
   - A numeric state value

### Deprecation Warnings

If you see deprecation warnings about config_entry, these have been fixed in version 0.0.42+. Update to the latest version to resolve these warnings.
