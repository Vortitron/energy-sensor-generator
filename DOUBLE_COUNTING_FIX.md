# Energy Sensor Generator - Double-Counting Fix (v0.0.77)

## The Problem Found

Your sensors were showing **4-5x overreads** (e.g., värmepump feed showing 23 kWh when the pump itself only used 5 kWh).

### Root Cause: 1-Minute Overlap in Statistical Calculations

The statistical calculation had a **1-minute buffer** that caused overlapping time windows:

**Before (with bug):**
- 12:00:00 → Calculate from 11:59:00 to 12:00:00 ✓
- 12:01:00 → Calculate from **12:00:00** to 12:01:00 
  - **BUT** it actually calculated from 11:59:00 (because of -1 minute buffer!)
  - Result: 11:59:00-12:00:00 counted **TWICE**

Every 60-second update overlapped the previous minute, causing persistent double (or more) counting!

**After (fixed):**
- 12:00:00 → Calculate from 11:59:00 to 12:00:00 ✓
- 12:01:00 → Calculate from **12:01:00** to 12:02:00 ✓
  - NO overlap, NO double-counting!

## What Was Fixed (v0.0.77)

### 1. Removed the 1-Minute Buffer
```python
# OLD (caused double-counting):
stat_start_time = self._last_statistical_calculation - timedelta(minutes=1)

# NEW (fixed):
stat_start_time = self._last_statistical_calculation  # Exactly where we left off
```

### 2. Reduced Extended Retry Buffer
The fallback retry also had a huge 5-minute buffer - reduced to 30 seconds:
```python
# OLD: extended_start = self._last_statistical_calculation - timedelta(minutes=5)
# NEW: extended_start = self._last_statistical_calculation - timedelta(seconds=30)
```

### 3. Added Service to Reset Statistical Tracking
New service: `energy_sensor_generator.reset_statistical_tracking`

This clears the `last_statistical_calculation` timestamps, forcing sensors to start fresh with a clean lookback window.

## How to Fix Your Current Overreads

### Step 1: Reset Statistical Tracking
This prevents future double-counting:

```yaml
service: energy_sensor_generator.reset_statistical_tracking
```

This will:
- Clear all statistical calculation timestamps
- Force next calculation to use the lookback window (fresh start)
- Show notification with results

### Step 2: Fix the Accumulated Overreads

You have two options:

**Option A: Copy from a known-good hour (recommended)**

If you know a time when the values were correct (e.g., this morning before the issue):
```yaml
service: energy_sensor_generator.copy_from_previous_hour
data:
  target_datetime: "2025-10-01 08:00:00"  # Replace with a good timestamp
```

**Option B: Manually adjust each sensor**

For "värmepump feed" that should be ~5 kWh but shows ~23 kWh:
```yaml
service: energy_sensor_generator.adjust_energy
data:
  entity_id: sensor.varmepumpen_mikro_hob_feed_untracked_energy  # Replace with actual entity_id
  set_to_value: 5.0  # Set to correct value
```

### Step 3: Restart Home Assistant

To ensure the fix is applied:
1. **Restart Home Assistant**
2. The sensors will start fresh with no double-counting
3. Monitor for the next hour to verify readings are correct

## How to Verify the Fix

After restarting:

1. **Enable Debug Logging** (Advanced Settings)
2. **Watch the logs** for statistical calculations:
   ```
   Using incremental statistical calculation: 2025-10-01 12:00:00 to 2025-10-01 12:01:00
   Statistical calculation successful: 0.0833 kWh (incremental since ...)
   ```

3. **Check the time windows** - they should NOT overlap:
   - 12:00:00 to 12:01:00 ✓
   - 12:01:00 to 12:02:00 ✓ (starts exactly where previous ended)
   - 12:02:00 to 12:03:00 ✓

4. **Compare readings** after 1 hour:
   - Heat pump power sensor: ~500W average
   - Expected energy in 1 hour: 0.5 kWh
   - If sensor shows 0.5 kWh → **FIXED!**
   - If sensor shows 2-3 kWh → Still an issue (report it)

## Why This Happened

The 1-minute buffer was added with good intentions - to ensure no data was missed between calculations. However:

1. **The buffer was too large** - 1 minute when calculations run every 60 seconds = 100% overlap
2. **Statistical data is complete** - Home Assistant's recorder captures all state changes, so no buffer needed
3. **Incremental calculation should be exact** - start where the previous ended, no gaps, no overlaps

## Technical Details

### The Statistical Calculation
Uses Home Assistant's recorder history to get all power state changes in a time window, then calculates energy using the Left Riemann sum method (same as HA's integration sensor).

### Why Incremental Calculation?
- **First calculation**: Uses lookback window (default 30 min) to capture initial data
- **Subsequent calculations**: Only calculate energy since last successful calculation
  - Efficient: Don't recalculate old data
  - Accurate: Picks up exactly where we left off
  - **CRITICAL**: Must NOT overlap with previous calculation!

### The Fix in Code
```python
# Incremental calculation: only calculate since last successful calculation
# NO buffer to prevent double-counting! Start exactly where we left off.
stat_start_time = self._last_statistical_calculation  # Changed from: - timedelta(minutes=1)
stat_end_time = now
```

## Services Available

### `reset_statistical_tracking`
- Clears statistical timestamps
- Forces fresh calculations
- Use when: You suspect double-counting

### `copy_from_previous_hour`  
- Sets all sensors to values from a specific datetime
- Use when: You need to fix accumulated overreads

### `adjust_energy`
- Adjusts individual sensor values
- Use when: Only one sensor needs correction

## Prevention for Future

The fix is now in the code - the 1-minute buffer has been removed. Future calculations will NOT double-count.

However, monitor your sensors for the next 24 hours to ensure:
- Hourly energy matches expected power × time
- No unexplained jumps
- Statistical calculations show non-overlapping time windows in debug logs

## Version History
- **v0.0.77** - Fixed double-counting in statistical calculations (removed 1-minute buffer)
- **v0.0.76** - Added spike protection (disabled by default), hourly copy service, config persistence fix
- **v0.0.75** - Previous version
