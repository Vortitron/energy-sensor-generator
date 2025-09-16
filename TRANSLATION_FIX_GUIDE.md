# Translation Display Fix Guide

## Issue
The Home Assistant config flow is showing raw field names (like "show_advanced", "create_synthetic_grid_total") instead of the translated descriptions from `en.json`.

## Root Cause
This commonly happens when:
1. Home Assistant hasn't reloaded the translations after changes
2. There's a caching issue with the integration
3. The integration needs to be completely reloaded

## Fix Steps

### Step 1: Reload the Integration
1. Go to **Settings** → **Devices & Services**
2. Find **Energy Sensor Generator** 
3. Click the three dots (⋯) menu
4. Select **Reload**

### Step 2: If Reload Doesn't Work - Full Restart
1. **Settings** → **System** → **Restart Home Assistant**
2. Wait for restart to complete
3. Check the config flow again

### Step 3: If Still Not Working - Remove and Re-add
1. Go to **Settings** → **Devices & Services**
2. Find **Energy Sensor Generator**
3. Click the three dots (⋯) menu 
4. Select **Delete**
5. Go to **Add Integration** and re-add **Energy Sensor Generator**

### Expected Results After Fix
- "show_advanced" should display as: **"Configure advanced settings →"**
- "create_synthetic_grid_total" should display as: **"Create synthetic 'Grid total' energy sensor (requires reload)"**  
- "period_sensors" should display as: **"Create sensors for periods"**

## Technical Details
The translations are correctly configured in `/custom_components/energy_sensor_generator/translations/en.json`:

```json
{
  "options": {
    "step": {
      "init": {
        "data": {
          "period_sensors": "Create sensors for periods",
          "create_synthetic_grid_total": "Create synthetic 'Grid total' energy sensor (requires reload)",
          "show_advanced": "Configure advanced settings →"
        }
      }
    }
  }
}
```

The issue is typically that Home Assistant needs to reload these translations from disk.

## Verification
After applying the fix, the configuration form should show proper field labels instead of raw field names. The advanced settings button should clearly indicate it's a navigation action with the arrow (→) symbol.
