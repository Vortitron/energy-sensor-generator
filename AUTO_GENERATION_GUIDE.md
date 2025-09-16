# Automatic Sensor Generation Guide

## Overview
The integration now automatically creates energy sensors immediately when you save the configuration, eliminating the need to restart the integration when adding new devices.

## How It Works

### **Instant Sensor Creation**
When you configure the integration and click "Submit":
1. ✅ Configuration is saved
2. ✅ New energy sensors are created automatically (within 2-3 seconds)
3. ✅ You get a notification confirming sensors were created
4. ✅ Sensors are immediately available in Home Assistant

### **No More Manual Steps**
**Before this enhancement:**
```
1. Configure integration options
2. Click Submit
3. Go to Settings → Devices & Services
4. Find Energy Sensor Generator
5. Click ⋯ → Reload
6. Wait for reload to complete
7. Check if sensors were created
```

**After this enhancement:**
```
1. Configure integration options
2. Click Submit
3. ✅ Done! Sensors created automatically
```

## User Experience Improvements

### **Configuration Flow**
- Updated descriptions mention that sensors will be created automatically
- Both basic and advanced configuration steps trigger auto-generation
- Clear feedback through notifications

### **Notifications**
**Success Notification:**
```
🔌 Energy Sensor Generator
Energy sensors have been created automatically based on your new configuration. 
Check the Entities page to see your new sensors.
```

**Error Notification (if something goes wrong):**
```
⚠️ Energy Sensor Generator - Error
Failed to automatically create energy sensors: [error details]. 
You may need to manually reload the integration.
```

## Technical Implementation

### **Automatic Trigger**
- Both main config step and advanced step trigger sensor generation
- Uses `hass.async_create_task()` for non-blocking execution
- 2-second delay to ensure config entry is fully processed

### **Error Handling**
- Comprehensive error catching and logging
- User-friendly error notifications
- Fallback to manual reload if automatic generation fails

### **Logging**
```
INFO: Auto-generating energy sensors after configuration update...
INFO: Energy sensors generated successfully after configuration update
```

## Benefits

1. **Instant Gratification**: See your sensors immediately after configuration
2. **Simplified Workflow**: No manual reload steps required
3. **Better UX**: Clear feedback on what's happening
4. **Error Recovery**: Helpful notifications if something goes wrong
5. **Time Saving**: Eliminates multiple navigation steps

## When It Triggers

✅ **Adding new power sensors**
✅ **Changing period sensor options** (daily, weekly, monthly, annual)
✅ **Modifying advanced settings**
✅ **Enabling/disabling synthetic grid total**
✅ **Any configuration change through the options flow**

## Fallback Options

If automatic generation fails:
1. Check the notification for error details
2. Manually reload the integration if needed
3. Check logs for detailed error information
4. Use the "Generate Energy Sensors" service as backup

This enhancement makes the integration much more user-friendly by eliminating the friction of manual reloads and providing immediate feedback on configuration changes.
