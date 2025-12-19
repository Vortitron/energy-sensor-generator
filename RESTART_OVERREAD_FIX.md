# CRITICAL FIX: Restart Overread Bug (v0.0.78)

## The Problem

**MASSIVE overreads on every restart** - showing 400+ kWh spikes!

### What Was Happening

When Home Assistant restarted:

1. Sensor loads saved state from storage: `state = 5.0 kWh` ✓
2. Sensor loads `_last_update` from storage ✓
3. **BUT:** `_last_statistical_calculation` is lost/old
4. Sensor thinks: "I have no recent calculation, do 30-minute lookback!"
5. Calculates 0.5 kWh from the last 30 minutes
6. **ADDS it to existing state**: `5.0 + 0.5 = 5.5 kWh` ❌
7. **But that 0.5 kWh was ALREADY COUNTED before restart!**

### Result
**Every restart added 30 minutes of duplicate energy to every sensor!**

If you had 10 sensors, each got +0.5 kWh extra on restart = 5 kWh total phantom energy.

## The Fix (v0.0.78)

### New Logic: Use `_last_update` After Restart

```python
# OLD (caused restart overreads):
if no last_statistical_calculation:
    # Do 30-minute lookback (adds already-counted energy!)
    start = now - 30 minutes

# NEW (fixed):
if no last_statistical_calculation BUT we have last_update:
    # Start from where we last updated (no duplicate counting!)
    start = last_update
```

### Three Scenarios Now:

**Scenario 1: Normal incremental calculation**
- Has `_last_statistical_calculation`
- Start from that timestamp
- No overlap, no duplication ✓

**Scenario 2: Post-restart calculation** (THE FIX!)
- NO `_last_statistical_calculation` (restart cleared it)
- BUT has `_last_update` from storage
- **Start from `_last_update` instead of lookback**
- Only counts energy since last update ✓

**Scenario 3: True first calculation**
- NO `_last_statistical_calculation`
- NO `_last_update` (brand new sensor)
- Use 30-minute lookback (this is OK for new sensors)
- Log warning that this adds historical energy ⚠️

## How to Fix Your Current Overread

### Step 1: Fix the ~400 kWh Spike

Use the hourly copy service to set all sensors back to values before the restart:

```yaml
service: energy_sensor_generator.copy_from_previous_hour
data:
  hour_to_fix: "2025-10-01 16:00:00"      # Hour that spiked
  hours_back: 1                            # Copy from the previous hour automatically
  # Legacy mode:
  # target_datetime: "2025-10-01 15:45:00"
```

**Or** if you know the exact correct values, adjust manually:

```yaml
service: energy_sensor_generator.adjust_energy
data:
  entity_id: sensor.varmepumpen_mikro_hob_feed_untracked_energy
  set_to_value: 5.0  # The value it should be
```

### Step 2: Restart Home Assistant with the Fix

With v0.0.78, the next restart will:
- Load `_last_update` from storage
- Start calculating from that time
- **NOT add 30 minutes of duplicate energy** ✓

### Step 3: Verify

After restart, check the logs:

```
Using last_update for sensor.xxx_energy to prevent double-counting on restart (last update: 2025-10-01 16:20:00)
Post-restart calculation using last_update: 2025-10-01 16:20:00 to 2025-10-01 16:21:00
```

The sensor should only add energy from the ACTUAL time elapsed since restart, not 30 minutes!

## Why This Bug Was So Bad

1. **Happened on EVERY restart** - not just occasionally
2. **Affected ALL sensors** - multiply the overread by number of sensors
3. **Compounded with the double-counting bug** - if you restarted while double-counting was happening, you got BOTH bugs!
4. **Not obvious** - the energy just appeared to be "high" but without clear indication of the cause

Example:
- 10 sensors
- Each does 30-minute lookback on restart
- Each adds ~0.5 kWh of duplicate energy
- Total phantom energy: **5 kWh per restart**
- If you restart 3 times a day... **15 kWh/day phantom energy!**

## Technical Details

### What `_last_update` Tracks
- The timestamp of the last power sensor state update
- Saved to storage on every calculation
- Survives restarts
- Represents: "This is when I last saw new power data"

### What `_last_statistical_calculation` Tracked
- The timestamp of the last SUCCESSFUL statistical calculation
- Was NOT being saved properly to storage (or was being lost)
- After restart: Would be None or very old
- This caused the fallback to lookback window

### The Fix
Now we check for `_last_update` BEFORE falling back to lookback:

```python
if last_statistical_calculation exists:
    use it (incremental)
elif last_update exists:
    use it (post-restart, no duplication!)  # NEW!
else:
    use lookback (true first calculation)
```

## Warning Signs That You Had This Bug

- Energy spikes whenever you restart Home Assistant
- Spikes are consistent in size (~30 min of power usage)
- Multiple sensors all spike at the same time (restart time)
- Spikes disappear if you don't restart for a while

## Future Restarts

With v0.0.78:
- ✅ First restart: Still might have issue (old storage format)
- ✅ After first calculation: `_last_update` saved
- ✅ Second restart: **FIX ACTIVE** - uses `_last_update`
- ✅ All future restarts: No duplicate counting!

## Cumulative Fixes

v0.0.78 includes ALL previous fixes:

1. ✅ **v0.0.77**: Removed 1-minute overlap (fixed runtime double-counting)
2. ✅ **v0.0.78**: Use `_last_update` on restart (fixed restart overreads)

Together these fix BOTH major overread bugs!

### Additional Protection (v0.0.82)

Occasionally Home Assistant restarts leave a long gap (10+ minutes) between the last stored update and the first post-restart calculation. Older versions bridged that gap with point sampling, which could add a noticeable chunk of phantom energy even though no measurements existed for the downtime.

From v0.0.82 onwards, point sampling is **skipped** whenever the gap exceeds `max(sample_interval × 3, 10 minutes)`. The integration now just refreshes its tracking timestamps and waits for fresh power readings (or a statistical calculation) before adding any new energy. If an actual overread still slips through, use the hourly copy service with `hour_to_fix` to revert the hour cleanly.

## Version History
- **v0.0.78** - Fixed restart overread by using `_last_update` instead of lookback
- **v0.0.77** - Fixed double-counting in statistical calculations (removed 1-minute buffer)
- **v0.0.76** - Added spike protection, hourly copy service, config persistence fix
