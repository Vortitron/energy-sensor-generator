# Diagnose Hot Water Overread

## The Problem

**Hot Water Energy**: 260 → 285 kWh = **25 kWh increase**
**Dishwash ww airfry Energy** (the feed): 157 → 171 kWh = **14 kWh increase**

The hot water used more energy than its entire feed supply! Impossible.

## Diagnostic Steps

### Step 1: Check if Hot Water Power is Actually an Energy Sensor

Run this service:

```yaml
service: energy_sensor_generator.diagnose_sensor
data:
  sensor_name: "hot_water"  # or "Hot Water Energy"
```

Check the logs for:
```
SOURCE SENSOR sensor.hot_water_power:
  Unit: W or kWh?  <--- If this is kWh, THAT'S THE PROBLEM!
  Device class: power or energy?
  State class: measurement or total_increasing?
```

**If the source is kWh (energy):** We're integrating already-integrated data = double counting!

### Step 2: Check for Double Calculation Methods

Enable debug logging and watch for these patterns:

```
Statistical energy calculation: hot_water_energy | Energy added: 0.XXXX kWh
```

Then immediately followed by:

```
Point sampling: hot_water_energy | Energy added: 0.XXXX kWh
```

**If you see BOTH in quick succession:** We're using both statistical AND point sampling = double counting!

### Step 3: Check Conversion Factor

In the diagnose output, look for:
```
Conversion factor: 1 or 1000?
Source unit: W or kW?
```

**If unit is W but factor is 1:** We're treating watts as kilowatts = 1000x overread!
**If unit is kW but factor is 1000:** We're dividing kilowatts by 1000 = 1000x underread!

### Step 4: Check for State Change Double-Counting

Look in logs for patterns like:
```
10:00:00 - Statistical calculation: +0.05 kWh
10:00:01 - State change detected: tracking only
10:00:05 - Statistical calculation: +0.05 kWh (should be 0.00!)
```

## Likely Causes

### Cause 1: Source is Energy Sensor (Most Likely)
If "Hot Water Power" is actually an energy sensor (kWh), we're:
1. Taking kWh reading: 100 kWh
2. Treating it as power: 100 W
3. Integrating over 1 hour: 0.1 kWh
4. Adding to total
5. Next reading: 100.1 kWh (it increased by 0.1!)
6. Treating as 100.1 W
7. Integrating: another 0.1 kWh
8. **Result: We add the increase TWICE**

**Fix:** Reconfigure to use the actual POWER sensor, not the energy sensor.

### Cause 2: Both Statistical AND Point Sampling Running
Despite our fixes, if both methods run, we get:
- Statistical: +0.5 kWh
- Point sampling: +0.5 kWh
- Total: +1.0 kWh (double!)

**Fix:** Enable `force_statistical_only` in Advanced Settings.

### Cause 3: Wrong Conversion Factor
If the source is in W but we're not dividing by 1000:
- Power: 1500 W
- Integration: 1500 W × 1 hour = 1500 kWh (should be 1.5!)

**Fix:** Check `_power_to_kw_factor` in storage.

## How to Fix

### If Source is Energy Sensor:
1. Find the actual POWER sensor (should be in Watts, not kWh)
2. Reconfigure integration to use that instead
3. OR create a utility meter from the energy sensor (don't use this integration)

### If Double Calculation Methods:
1. Go to Advanced Settings
2. Enable "Force statistical only"
3. Restart

### If Conversion Factor Wrong:
This is a code bug - report it with the diagnose output.

## Run These Services

```yaml
# Get detailed diagnostics
service: energy_sensor_generator.diagnose_sensor
data:
  sensor_name: "hot_water"

# List all sensors to see their calculation methods
service: energy_sensor_generator.list_sensors

# Reset and start fresh if needed
service: energy_sensor_generator.reset_statistical_tracking
```

Then paste the log output so we can see what's happening!
