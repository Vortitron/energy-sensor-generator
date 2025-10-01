# Energy Sensor Generator - Hourly Spike Fix Guide

## What Changed (v0.0.76)

### 1. ✅ Spike Protection is Now DISABLED by Default
- **Default value: 0** (which means disabled, no limit)
- Only activates if you explicitly set a value > 0 in Advanced Settings
- This lets you investigate the root cause without artificial limits

### 2. ✅ Advanced Config Settings Now Persist
Yes, the advanced config box settings are fixed! They now properly remember all values when you exit and come back:
- Sample interval
- Debug logging
- Use statistical calculation
- Force statistical only
- Statistical lookback minutes
- **Max energy per hour** (spike protection, default: 0 = disabled)
- Create synthetic grid total

All settings are loaded from `{**self.config_entry.options, **self._user_defaults}` and properly saved.

### 3. ✅ New Service: Copy All Sensors from Previous Hour

This is exactly what you asked for! You can now copy all sensor values from a specific date/hour.

**Service: `energy_sensor_generator.copy_from_previous_hour`**

#### How to Use It

To fix the spike at 16:00-17:00 by copying values from 15:00:

```yaml
service: energy_sensor_generator.copy_from_previous_hour
data:
  target_datetime: "2025-09-30 15:00:00"
```

This will:
1. Look up ALL energy sensor values from that datetime (15:00)
2. Set the current values of all sensors to those historical values
3. Effectively "deletes" everything after that hour (the spike)
4. Show a notification with results

#### What It Does Behind the Scenes

1. **Finds all energy sensors** from this integration (excluding daily/monthly/annual)
2. **Retrieves historical data** from Home Assistant's recorder database
3. **Finds the closest state** to your specified datetime
4. **Updates storage** for each sensor with the historical value
5. **Forces entity refresh** so you see the changes immediately
6. **Creates notification** showing how many were updated vs errors

#### Example Use Cases

**Fix yesterday's spike at 16:00:**
```yaml
service: energy_sensor_generator.copy_from_previous_hour
data:
  target_datetime: "2025-09-29 15:00:00"  # Hour BEFORE the spike
```

**Fix this morning's spike at 08:00:**
```yaml
service: energy_sensor_generator.copy_from_previous_hour
data:
  target_datetime: "2025-09-30 07:00:00"  # Hour BEFORE the spike
```

**Reset to midnight values:**
```yaml
service: energy_sensor_generator.copy_from_previous_hour
data:
  target_datetime: "2025-09-30 00:00:00"
```

## How to Fix Your Current Spike (16:00-17:00)

### Step 1: Identify the Good Hour
Looking at your screenshot, the hour BEFORE the spike (15:00) had normal values.

### Step 2: Run the Service

Go to **Developer Tools → Services** and run:

```yaml
service: energy_sensor_generator.copy_from_previous_hour
data:
  target_datetime: "2025-09-30 15:00:00"
```

### Step 3: Verify
- Check the notification for results
- Check logs for detailed info: `✓ sensor.xxx: old → new kWh`
- View your energy dashboard to confirm the spike is gone

## Investigating the Root Cause

Now that spike protection is disabled by default, you can investigate why the spike happened:

1. **Enable Debug Logging** (Advanced Settings)
2. **Watch the logs** when the next update happens
3. Look for these patterns:
   - `"Statistical energy calculation"` - shows energy added from stats
   - `"Point sampling calculation"` - shows energy from point sampling
   - `"Energy added: X.XXXX kWh"` - the actual increment
   - Any warnings about missing data or calculation failures

4. **Common causes of spikes:**
   - Missing statistical data forcing large catch-up calculations
   - Source sensor going unavailable then returning with accumulated value
   - Incorrect unit detection (kW vs W)
   - Restart/reload causing statistical lookback to add already-counted energy

5. **If you find the pattern, you can:**
   - Adjust `stat_initial_lookback_minutes` 
   - Enable `force_statistical_only` 
   - Or enable spike protection with an appropriate limit

## Service Comparison

### `copy_from_previous_hour` - Bulk Fix (NEW)
- **Use when:** Multiple sensors spiked at the same time
- **What it does:** Sets ALL sensors to values from a specific datetime
- **Example:** Fix entire hour's worth of bad data

### `adjust_energy` - Individual Fix
- **Use when:** One sensor has an error
- **What it does:** Adjusts a single sensor by amount, to value, or from another sensor
- **Example:** Subtract 10 kWh from one sensor

## Notes

- The service uses Home Assistant's recorder history, so keep recorder retention long enough
- It finds the state closest to your specified time (within ±5 minutes)
- Skips daily/monthly/weekly/annual sensors (only updates main energy sensors)
- Shows detailed results in logs and notification
- Works on all energy sensors created by this integration

## Troubleshooting

**"No historical data found"**
- Check if recorder has data for that time period
- Verify the datetime format is correct: `YYYY-MM-DD HH:MM:SS`

**"No storage key found"**
- The sensor might not be from this integration
- Check entity registry to confirm

**Some sensors updated, some didn't**
- Normal - some sensors might not have had valid data at that time
- Check logs for specific error messages

## Version Info
- **v0.0.76** - Added copy_from_previous_hour service, disabled spike protection by default, fixed advanced config persistence
