# Energy Sensor Generator

A Home Assistant custom integration that automatically generates kWh energy sensors from power sensors (in Watts) for use in the Energy dashboard. Unlike other solutions, it operates entirely in Python, avoiding YAML-based helpers like `integration` (Riemann Sum) or `utility_meter`, making it tidy, self-contained, and easy to manage.

[![Add to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=vortitron&repository=energy-sensor-generator&category=integration)

## Features
- **Automatic Detection**: Identifies all power sensors (`unit: W`, `device_class: power`) in Home Assistant.
- **Custom Energy Calculation**: Computes kWh using a trapezoidal rule, replacing the `integration` helper.
- **Daily and Monthly Tracking**: Tracks energy usage with automatic daily and monthly resets, replacing the `utility_meter` helper.
- **Flexible Generation**: Supports automatic sensor creation on startup or manual triggering via a UI button.
- **Energy Dashboard Compatibility**: Generates sensors with `device_class: energy` and `state_class: total_increasing` for seamless integration.
- **Persistent Storage**: Saves energy data to a JSON file to survive Home Assistant restarts.
- **No Dependencies**: Pure Python implementation, no need for MQTT, Node-RED, or external integrations.
- **HACS Ready**: Easily installed via the Home Assistant Community Store (HACS).

## How It Works
The `energy_sensor_generator` integration simplifies energy monitoring by creating kWh sensors for devices like Tuya smart plugs that report power in Watts. Here's a detailed breakdown of its operation:

1. **Power Sensor Detection**:
   - The integration scans the Home Assistant entity registry for sensors with `unit_of_measurement: W` and `device_class: power` (e.g., `sensor.plug_1_power`).
   - It supports any device providing power data, such as Tuya plugs via LocalTuya or other integrations.

2. **Energy Calculation**:
   - For each power sensor, it creates a custom `EnergySensor` that calculates kWh by integrating power over time.
   - The integration uses the trapezoidal rule: `energy_kwh = (avg_power * time_delta_hours) / 1000`, where `avg_power` is the average of consecutive power readings.
   - Energy values are updated whenever the power sensor changes, ensuring accurate accumulation.
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
- **Power Sensors**: Devices (e.g., Tuya smart plugs) that expose power sensors with `unit: W` and `device_class: power`. LocalTuya is recommended for Tuya plugs due to faster updates.
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
Troubleshooting
No Power Sensors Found:
Ensure your devices (e.g., Tuya plugs) expose power sensors with unit: W and device_class: power. Check in Developer Tools > States.
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
