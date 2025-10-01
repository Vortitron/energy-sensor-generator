# Energy Sensor Generator - Spike Protection & Energy Adjustment Update (v0.0.76)

## Issues Fixed

### 1. **Spike Protection - Optional Protection Against Massive Overreads** ✅
Your screenshot showed a massive spike of 1,832 kWh in one hour at 16:00-17:00.

**What was added:**
- **Optional spike detection** - Monitors energy calculations and rejects unrealistic spikes (DISABLED by default)
- **Configurable threshold** - Default: 0 (disabled) - set to any value > 0 to enable (in Advanced Settings)
- **Warning notifications** - You'll be alerted in logs when a spike is detected and rejected

**How it works:**
- **By default:** Does nothing, allowing you to investigate the root cause
- **When enabled (value > 0):** Before adding energy to a sensor, checks if it exceeds the maximum allowed per hour
- If a spike is detected, the reading is rejected and logged with a clear warning
- You can enable and adjust the `max_energy_per_hour` setting in Advanced Settings

**Why disabled by default?** You wanted to find and fix the root cause rather than mask it with limits!

### 2. **Bulk Hourly Fix - Copy All Sensors from Previous Hour** ✅
**This is the main solution you asked for!**

**New service: `energy_sensor_generator.copy_from_previous_hour`**

Set ALL energy sensors to their values from a specific date/hour - perfect for fixing hourly spikes!

```yaml
service: energy_sensor_generator.copy_from_previous_hour
data:
  target_datetime: "2025-09-30 15:00:00"  # Hour BEFORE the spike
```

**What it does:**
- Retrieves historical values for ALL energy sensors from the specified datetime
- Updates current sensor values to match those historical values
- Effectively "deletes" everything after that hour (removing the spike)
- Works on all sensors at once - no need to adjust each individually
- Shows notification with results

**To fix your 16:00-17:00 spike:** Use `target_datetime: "2025-09-30 15:00:00"` (the hour before)

See `HOURLY_FIX_GUIDE.md` for complete details!

### 3. **Individual Energy Adjustment Service - Single Sensor Correction** ✅
You can also manually adjust individual sensor values when needed!

**New service: `energy_sensor_generator.adjust_energy`**

This service lets you fix incorrect readings in three ways:

#### **Option 1: Adjust by amount (add/subtract)**
```yaml
service: energy_sensor_generator.adjust_energy
data:
  entity_id: sensor.smart_plug_energy
  adjustment_kwh: -1.5  # Subtract 1.5 kWh (use positive to add)
```

#### **Option 2: Set to exact value**
```yaml
service: energy_sensor_generator.adjust_energy
data:
  entity_id: sensor.smart_plug_energy
  set_to_value: 10.5  # Set to exactly 10.5 kWh
```

#### **Option 3: Copy from another sensor**
```yaml
service: energy_sensor_generator.adjust_energy
data:
  entity_id: sensor.washing_machine_energy
  copy_from_entity: sensor.washing_machine_daily_energy  # Copy value from this sensor
```

**How to use it:**
1. Go to Developer Tools → Services
2. Select `Energy Sensor Generator: Adjust Energy Value`
3. Choose your sensor and adjustment method
4. Execute - you'll get a confirmation notification

### 4. **Advanced Config Persistence** ✅
**Yes! The advanced settings now properly remember their values!**

**What was fixed:**
- All advanced config values now persist when you exit and come back
- Added the new `max_energy_per_hour` setting to the advanced config
- Settings are properly loaded from stored configuration

**Advanced Settings Available:**
- Sampling interval
- Debug logging
- Use statistical calculation
- Force statistical only
- Initial statistical lookback period
- **NEW: Maximum energy per hour (spike protection) - Default: 0 (disabled)**
- Create synthetic grid total

**All values persist correctly** when you exit and return to the config!

## How to Use the New Features

### Preventing Future Spikes

1. **Go to Settings → Devices & Services → Energy Sensor Generator**
2. **Click "Configure"**
3. **Click "Configure advanced settings →"**
4. **Set "Maximum energy per hour (spike protection)" to appropriate value**
   - Default: 10 kWh/hour
   - For a 3000W device: set to 3-4 kWh/hour
   - For a 10000W device: set to 10-15 kWh/hour
5. **Submit the form**

### Fixing the Existing Spike

To fix that 1,832 kWh spike at 16:00-17:00:

**BEST METHOD: Copy all sensors from hour before the spike**
```yaml
service: energy_sensor_generator.copy_from_previous_hour
data:
  target_datetime: "2025-09-30 15:00:00"  # Hour BEFORE the spike at 16:00
```

This will:
- Set ALL sensors to their 15:00 values (before the spike)
- Remove the entire spike hour from all affected sensors
- Show you which sensors were updated

**Alternative: Individual sensor adjustment**
If only one sensor spiked:
```yaml
service: energy_sensor_generator.adjust_energy
data:
  entity_id: sensor.your_sensor_energy
  adjustment_kwh: -1832  # Subtract the spike amount
```

## Technical Details

### Spike Detection Algorithm
```python
# For each energy calculation:
1. Calculate time interval in hours
2. Calculate max_allowed = max_energy_per_hour × interval_hours
3. If calculated_energy > max_allowed:
   - Log warning with details
   - Reject the reading
   - Update tracking but don't add energy
4. Else:
   - Add energy normally
```

### Files Changed
- `const.py` - Added CONF_MAX_ENERGY_PER_HOUR constant
- `sensor.py` - Added spike detection to both statistical and point sampling
- `__init__.py` - Added adjust_energy service
- `services.yaml` - Added adjust_energy service definition
- `options_flow.py` - Added max_energy_per_hour to advanced settings with persistence fix
- `translations/en.json` - Added translations for new settings
- `manifest.json` - Bumped version to 0.0.76

## Testing Recommendations

1. **Monitor logs** for spike detection warnings
2. **Test the service** with a small adjustment first
3. **Adjust the threshold** if you get false positives (legitimate readings rejected)
4. **Verify advanced settings persist** by opening/closing the config multiple times

## Notes

- The spike protection is **active immediately** after restart/reload
- Default threshold (10 kWh/hour) works for most household devices
- For high-power devices, increase the threshold in Advanced Settings
- The adjustment service works on all energy sensors created by this integration
- All adjustments are logged and show a confirmation notification

## Version History
- **v0.0.76** - Added spike protection, energy adjustment service, fixed advanced config persistence
- **v0.0.75** - Previous version
