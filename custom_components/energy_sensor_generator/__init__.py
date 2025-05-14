import logging
import json
from pathlib import Path
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .sensor import EnergySensor, DailyEnergySensor, MonthlyEnergySensor
from .utils import load_storage, save_storage
from .const import DOMAIN, STORAGE_FILE
import voluptuous as vol

_LOGGER = logging.getLogger(__name__)

def detect_power_sensors(hass: HomeAssistant) -> list:
    """Detect power sensors using various criteria for broader detection."""
    entity_registry = er.async_get(hass)
    power_sensors = []
    
    # Get all entity states from Home Assistant
    all_states = hass.states.async_all()
    
    # Check for entities based on several criteria
    for state in all_states:
        entity_id = state.entity_id
        if not entity_id.startswith("sensor."):
            continue
            
        # Check if it looks like a power sensor
        is_power_sensor = False
        
        # 1. Check unit of measurement (most reliable)
        unit = state.attributes.get("unit_of_measurement", "")
        if unit in ["W", "w", "Watt", "watt", "Watts", "watts"]:
            is_power_sensor = True
            
        # 2. Check device class
        device_class = state.attributes.get("device_class", "")
        if device_class == "power":
            is_power_sensor = True
            
        # 3. Check entity naming patterns
        name_patterns = ["_power", "_consumption", "_usage", "power_", "watt"]
        if any(pattern in entity_id for pattern in name_patterns):
            # Only use name as indicator if numerical state is present
            try:
                float(state.state)
                is_power_sensor = True
            except (ValueError, TypeError):
                # Not a numerical sensor, so name pattern is not good enough
                pass
                
        # 4. Check for entity_registry entries with unit W or device_class power
        try:
            entity_reg = entity_registry.async_get(entity_id)
            if entity_reg and (entity_reg.unit_of_measurement == "W" or entity_reg.device_class == "power"):
                is_power_sensor = True
        except (KeyError, AttributeError):
            pass
            
        if is_power_sensor:
            power_sensors.append(entity_id)
            
    return power_sensors

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "config": entry.data,
        "storage": hass.config.path(".storage", STORAGE_FILE),
        "async_add_entities": None  # Will be set during sensor setup
    }

    # Register service for manual generation
    hass.services.async_register(
        DOMAIN, "generate_sensors", generate_sensors_service, schema={}
    )

    # Register service for reassigning energy data
    hass.services.async_register(
        DOMAIN, "reassign_energy_data", reassign_energy_data_service, 
        schema=vol.Schema({
            vol.Required("from_device"): str,
            vol.Required("to_device"): str
        })
    )

    # Forward to sensor setup
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])

    # Auto-generate on setup if enabled
    if entry.data.get("auto_generate", False):
        await generate_sensors_service(hass, None, entry=entry)

    return True

async def generate_sensors_service(hass: HomeAssistant, call, entry: ConfigEntry = None) -> None:
    """Service to generate energy sensors."""
    _LOGGER.info("Generating energy sensors")

    # Use the config entry from the call context if not provided
    if entry is None:
        # Try to get the first config entry for this domain
        entries = list(hass.data[DOMAIN].values())
        if not entries:
            _LOGGER.error("No config entry found for energy_sensor_generator.")
            return
        entry_data = entries[0]["config"]
        options = getattr(entries[0], "options", {})
        storage_path = entries[0]["storage"]
        async_add_entities = entries[0].get("async_add_entities")
    else:
        entry_data = entry.data
        options = entry.options
        storage_path = hass.data[DOMAIN][entry.entry_id]["storage"]
        async_add_entities = hass.data[DOMAIN][entry.entry_id].get("async_add_entities")

    # Get power sensors using more flexible detection
    all_power_sensors = detect_power_sensors(hass)
    _LOGGER.info(f"Auto-detected {len(all_power_sensors)} power sensors: {all_power_sensors}")

    # Get custom power sensors from options if present
    custom_sensors = []
    if options and "custom_power_sensors" in options:
        custom_sensors_str = options.get("custom_power_sensors", "")
        if custom_sensors_str:
            custom_sensors = [s.strip() for s in custom_sensors_str.split(",") if s.strip()]
            # Add custom sensors that are not already detected
            custom_sensors = [s for s in custom_sensors if s not in all_power_sensors]
            _LOGGER.info(f"Using custom power sensors: {custom_sensors}")
    
    # Combine auto-detected and custom sensors
    all_power_sensors.extend(custom_sensors)

    # Use manual selection if present in options
    selected_sensors = options.get("selected_power_sensors") if options else None
    if selected_sensors:
        power_sensors = [eid for eid in all_power_sensors if eid in selected_sensors]
        _LOGGER.info(f"Using manually selected power sensors: {power_sensors}")
    else:
        power_sensors = all_power_sensors
        _LOGGER.info(f"Using all detected power sensors: {power_sensors}")

    if not power_sensors:
        _LOGGER.warning("No power sensors found for energy sensor generation.")
        return

    entities = []
    storage = load_storage(storage_path)

    for sensor in power_sensors:
        base_name = sensor.replace("sensor.", "").replace("_power", "")
        
        # Create Energy Sensor (kWh)
        energy_sensor = EnergySensor(hass, base_name, sensor, storage_path)
        entities.append(energy_sensor)

        # Create Daily and Monthly Sensors
        for period in ["daily", "monthly"]:
            period_sensor = (
                DailyEnergySensor if period == "daily" else MonthlyEnergySensor
            )(hass, base_name, f"sensor.{base_name}_energy", storage_path)
            entities.append(period_sensor)

    # Add entities using the correct async_add_entities callback
    if async_add_entities:
        async_add_entities(entities)
    else:
        _LOGGER.error("async_add_entities callback not found for adding entities")

async def reassign_energy_data_service(hass: HomeAssistant, call) -> None:
    """Service to reassign energy data from one device to another."""
    _LOGGER.info("Reassigning energy data")
    from_device = call.data.get("from_device")
    to_device = call.data.get("to_device")
    
    storage_path = hass.data[DOMAIN][list(hass.data[DOMAIN].keys())[0]]["storage"]
    storage = load_storage(storage_path)
    
    for key in list(storage.keys()):
        if key.startswith(from_device):
            new_key = key.replace(from_device, to_device)
            storage[new_key] = storage.pop(key)
            _LOGGER.info(f"Reassigned energy data from {key} to {new_key}")
    
    save_storage(storage_path, storage)
    _LOGGER.info(f"Completed reassignment from {from_device} to {to_device}")
