# Sensor Naming Logic Guide

## Overview
The integration now uses intelligent sensor naming to handle devices with multiple power sensors, ensuring clear identification of each sensor.

## Naming Logic

### 1. **Device + Sensor Name (Most Common)**
When device name and sensor name are different and both meaningful:
```
Smart Power Strip - Outlet 1 Power
Smart Power Strip - Outlet 2 Power 
Smart Power Strip - USB Power
Multi Outlet Device - Living Room Power
Multi Outlet Device - Kitchen Power
```

### 2. **Device Name Only**
When sensor name is generic or missing:
```
Smart Plug
Tuya Smart Switch
Energy Monitor
```

### 3. **Friendly Name Only**
When friendly name already contains device context:
```
Living Room Smart Plug Power
Kitchen Outlet Power Monitor
Bedroom Light Switch Power
```

### 4. **Disambiguation Suffix**
For sensors that don't clearly indicate they're power sensors:
```
Generic Sensor (current_power)
Unknown Device (smart_plug_power)
Temperature Monitor (power_consumption)
```

## Examples by Device Type

### **Multi-Outlet Power Strips**
```
Antela Smart Power Strip - Outlet 1 Power
Antela Smart Power Strip - Outlet 2 Power  
Antela Smart Power Strip - Outlet 3 Power
Antela Smart Power Strip - USB Power
```

### **Smart Plugs with Multiple Sensors**
```
Tuya Smart Plug - Current Power
Tuya Smart Plug - Voltage Power
Tuya Smart Plug - Total Power
```

### **Energy Monitoring Devices**
```
SAM Energy Monitor - Current Power
SAM Energy Monitor - Heating Power
SAM Energy Monitor - Cooling Power
```

### **Single-Sensor Devices**
```
Smart Plug Power
Kitchen Outlet Power
Bedroom Light Switch
```

## Benefits

1. **Clear Identification**: Users can easily distinguish between multiple sensors on the same device
2. **Consistent Naming**: Follows a predictable pattern across different device types
3. **Prevents Confusion**: Avoids duplicate or ambiguous names
4. **Scalable**: Works with any number of sensors per device
5. **Debug-Friendly**: Entity ID suffix helps with troubleshooting when needed

## Technical Implementation

The naming logic:
1. Retrieves device name from device registry
2. Gets friendly name from entity registry
3. Applies intelligent rules to avoid redundancy
4. Adds disambiguation when necessary
5. Maintains consistency across the interface

This ensures that users can easily select the correct sensors, especially important for complex devices with multiple power monitoring points.
