import logging
import json
from pathlib import Path
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from datetime import timedelta
from .sensor import EnergySensor, DailyEnergySensor, MonthlyEnergySensor
from .utils import load_storage, save_storage
from .const import DOMAIN, STORAGE_FILE, CONF_DEBUG_LOGGING
import voluptuous as vol

_LOGGER = logging.getLogger(__name__)

def _is_debug_enabled(hass: HomeAssistant) -> bool:
	"""Check if debug logging is enabled for this integration."""
	if DOMAIN not in hass.data:
		return False
	
	# Check all config entries for debug setting
	for config_entry in hass.config_entries.async_entries(DOMAIN):
		if config_entry.options.get(CONF_DEBUG_LOGGING, False):
			return True
	return False

def _debug_log(hass: HomeAssistant, message: str) -> None:
	"""Log debug message only if debug logging is enabled."""
	if _is_debug_enabled(hass):
		_LOGGER.warning(f"DEBUG: {message}")

def _info_log(hass: HomeAssistant, message: str, force: bool = False) -> None:
	"""Log info message, respecting debug setting unless forced."""
	if force or _is_debug_enabled(hass):
		_LOGGER.info(message)

def detect_power_sensors(hass: HomeAssistant) -> list:
    """Detect power sensors using various criteria for broader detection."""
    entity_registry = er.async_get(hass)
    power_sensors = []
    kw_sensors = []
    
    # Get all entity states from Home Assistant
    all_states = hass.states.async_all()
    
    _debug_log(hass, f"Scanning {len(all_states)} entities for power sensors...")
    
    # Check for entities based on several criteria
    for state in all_states:
        entity_id = state.entity_id
        if not entity_id.startswith("sensor."):
            continue
            
        # Check if it looks like a power sensor
        is_power_sensor = False
        detection_reason = ""
        
        # 1. Check unit of measurement (most reliable)
        unit = state.attributes.get("unit_of_measurement", "")
        if unit in ["W", "w", "Watt", "watt", "Watts", "watts", "kW", "kw", "kilowatt", "kilowatts"]:
            is_power_sensor = True
            detection_reason = f"unit '{unit}'"
            
            # Track kW sensors specifically
            if unit.lower() in ["kw", "kilowatt", "kilowatts"]:
                kw_sensors.append(entity_id)
            
        # 2. Check device class
        device_class = state.attributes.get("device_class", "")
        if device_class == "power":
            is_power_sensor = True
            detection_reason += f" + device_class '{device_class}'" if detection_reason else f"device_class '{device_class}'"
            
        # 3. Check entity naming patterns
        name_patterns = ["_power", "_consumption", "_usage", "power_", "watt"]
        if any(pattern in entity_id for pattern in name_patterns):
            # Only use name as indicator if numerical state is present
            try:
                float(state.state)
                is_power_sensor = True
                detection_reason += f" + name pattern" if detection_reason else "name pattern"
            except (ValueError, TypeError):
                # Not a numerical sensor, so name pattern is not good enough
                pass
                
        # 4. Check for entity_registry entries with unit W/kW or device_class power
        try:
            entity_reg = entity_registry.async_get(entity_id)
            if entity_reg and (entity_reg.unit_of_measurement in ["W", "kW"] or entity_reg.device_class == "power"):
                is_power_sensor = True
                detection_reason += f" + registry ({entity_reg.unit_of_measurement or entity_reg.device_class})" if detection_reason else f"registry ({entity_reg.unit_of_measurement or entity_reg.device_class})"
                
                # Track kW sensors from registry too
                if entity_reg.unit_of_measurement == "kW":
                    kw_sensors.append(entity_id)
        except (KeyError, AttributeError):
            pass
            
        if is_power_sensor:
            power_sensors.append(entity_id)
            _debug_log(hass, f"✓ Detected power sensor: {entity_id} (reason: {detection_reason}, state: {state.state})")
        elif unit:  # Log sensors with units that we didn't detect as power sensors
            _debug_log(hass, f"✗ Skipped sensor: {entity_id} (unit: '{unit}', device_class: '{device_class}', state: {state.state})")
            
    _info_log(hass, f"Detected {len(power_sensors)} power sensors total", force=True)
    if kw_sensors:
        _info_log(hass, f"Detected {len(kw_sensors)} kW sensors: {kw_sensors}", force=True)
    return power_sensors

def check_existing_energy_sensors(hass: HomeAssistant) -> dict:
    """Check for existing energy sensors and map them to their devices."""
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    device_energy_sensors = {}
    
    # Get all entity states from Home Assistant
    all_states = hass.states.async_all()
    
    # Find energy sensors
    for state in all_states:
        entity_id = state.entity_id
        if not entity_id.startswith("sensor."):
            continue
            
        # Check if it's an energy sensor
        is_energy_sensor = False
        
        # Check unit of measurement
        unit = state.attributes.get("unit_of_measurement", "")
        if unit in ["kWh", "kwh"]:
            is_energy_sensor = True
            
        # Check device class
        device_class = state.attributes.get("device_class", "")
        if device_class == "energy":
            is_energy_sensor = True
            
        if is_energy_sensor:
            entity = entity_registry.async_get(entity_id)
            if entity and entity.device_id:
                # Add this sensor to the device's list of energy sensors
                if entity.device_id not in device_energy_sensors:
                    device_energy_sensors[entity.device_id] = []
                device_energy_sensors[entity.device_id].append(entity_id)
    
    return device_energy_sensors

def find_generated_sensors(hass: HomeAssistant) -> dict:
    """Find all energy sensors generated by this integration."""
    entity_registry = er.async_get(hass)
    result = {}
    
    # Look for entities with unique IDs that match our pattern
    for entity_id, entry in entity_registry.entities.items():
        if entry.platform == DOMAIN:
            # Get the base_name from the unique_id
            unique_id = entry.unique_id
            if "_energy" in unique_id:
                # Extract base name, handling different patterns
                if "_daily_energy" in unique_id:
                    base_name = unique_id.replace("_daily_energy", "")
                elif "_monthly_energy" in unique_id:
                    base_name = unique_id.replace("_monthly_energy", "")
                else:
                    base_name = unique_id.replace("_energy", "")
                
                if base_name not in result:
                    result[base_name] = []
                result[base_name].append(entity_id)
    
    _debug_log(hass, f"Found {len(result)} generated sensor groups: {result}")
    return result

def get_source_device_info(hass: HomeAssistant, source_entity_id: str):
    """Get the device info for a source entity."""
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    
    # Get the entity and check if it has a device
    entity = entity_registry.async_get(source_entity_id)
    if not entity or not entity.device_id:
        return None
    
    # Get the device
    device = device_registry.async_get(entity.device_id)
    if not device:
        return None
        
    # Return device info that can be used to associate entities
    return device.identifiers

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Energy Sensor Generator component."""
    hass.data.setdefault(DOMAIN, {})
    
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Energy Sensor Generator from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Setup storage path
    storage_path = Path(hass.config.path(STORAGE_FILE))
    
    # Store references in hass.data
    hass.data[DOMAIN][entry.entry_id] = {
        "config": entry.data,
        "storage": storage_path,
        "options": entry.options,
        "unsubscribers": [],
    }

    # Register generate service
    hass.services.async_register(
        DOMAIN, 
        "generate_sensors", 
        lambda call: generate_sensors_service(hass, call, entry)
    )
    
    # Register reset service for correcting doubled values
    hass.services.async_register(
        DOMAIN,
        "reset_energy_sensors",
        lambda call: reset_energy_sensors_service(hass, call, entry)
    )
    
    # Register debug service for troubleshooting
    hass.services.async_register(
        DOMAIN,
        "debug_sensor_detection",
        lambda call: debug_sensor_detection_service(hass, call, entry)
    )
    
    # Register diagnostic service for individual sensors
    async def diagnose_sensor_wrapper(call):
        _LOGGER.warning("DIAGNOSE SERVICE CALLED!")  # Make sure this appears in logs
        await diagnose_sensor_service(hass, call, entry)
    
    hass.services.async_register(
        DOMAIN,
        "diagnose_sensor",
        diagnose_sensor_wrapper
    )
    
    # Register list sensors service
    async def list_sensors_wrapper(call):
        _LOGGER.warning("LIST SENSORS SERVICE CALLED!")  # Make sure this appears in logs
        await list_sensors_service(hass, call, entry)
    
    hass.services.async_register(
        DOMAIN,
        "list_sensors",
        list_sensors_wrapper
    )
    
    # Test service to verify service registration
    async def test_service_wrapper(call):
        _LOGGER.warning("TEST SERVICE WORKING! Services are properly registered.")
    
    hass.services.async_register(
        DOMAIN,
        "test_service",
        test_service_wrapper
    )
    
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    
    # Set up periodic sampling for more accurate energy calculation
    sample_interval = entry.options.get("sample_interval", 60)
    
    # Note: Removed periodic power sensor sampling as energy calculations are now 
    # handled exclusively by individual sensor interval timers to prevent double counting
    
    # Schedule generate_sensors_service to run after a short delay to ensure sensor platform is ready
    if entry.options.get("selected_power_sensors"):
        async def delayed_sensor_generation():
            """Generate sensors after a delay to ensure platform is ready."""
            # Wait a bit for the sensor platform to be fully initialized
            import asyncio
            await asyncio.sleep(2)
            
            # Check if async_add_entities is available, retry if not
            for attempt in range(5):  # Try up to 5 times
                if hass.data[DOMAIN][entry.entry_id].get("async_add_entities"):
                    await generate_sensors_service(hass, None, entry)
                    _LOGGER.info("Successfully generated sensors during startup")
                    break
                else:
                    _LOGGER.debug(f"async_add_entities not ready yet, attempt {attempt + 1}/5")
                    await asyncio.sleep(1)
            else:
                _LOGGER.warning("Failed to generate sensors during startup - async_add_entities not available")
        
        # Schedule the delayed generation
        hass.async_create_task(delayed_sensor_generation())
    
    return True
    
async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Cancel all periodic tasks
    for unsub in hass.data[DOMAIN][entry.entry_id].get("unsubscribers", []):
        unsub()
        
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor"])
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        
    return unload_ok

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
    
    # Check if async_add_entities is available
    if not async_add_entities:
        _LOGGER.error("async_add_entities callback not available - sensor platform may not be ready yet")
        return

    # Get power sensors using more flexible detection
    all_power_sensors = detect_power_sensors(hass)
    _LOGGER.info(f"Auto-detected {len(all_power_sensors)} power sensors: {all_power_sensors}")

    # Use selected power sensors from options if present
    selected_sensors = options.get("selected_power_sensors") if options else None
    if selected_sensors:
        # During startup, assume selected sensors will become available
        # Don't filter them out immediately if they're not yet available
        existing_sensors = []
        missing_sensors = []
        
        for sensor in selected_sensors:
            if hass.states.get(sensor) is not None:
                existing_sensors.append(sensor)
            else:
                missing_sensors.append(sensor)
                _LOGGER.info(f"Selected sensor {sensor} not yet available (may be starting up), will create energy sensor anyway")
        
        # Use all selected sensors, regardless of current availability
        power_sensors = selected_sensors
        _LOGGER.info(f"Using manually selected power sensors: {power_sensors}")
        
        if missing_sensors:
            _LOGGER.info(f"Missing sensors during startup: {missing_sensors} - assuming they will become available")
    else:
        power_sensors = all_power_sensors
        _LOGGER.info(f"Using all detected power sensors: {power_sensors}")

    if not power_sensors:
        _LOGGER.warning("No power sensors found for energy sensor generation.")
        return

    # Check if we should create daily and monthly sensors
    create_daily = options.get("create_daily_sensors", True)
    create_monthly = options.get("create_monthly_sensors", True)
    
    # Find existing generated sensors
    existing_generated = find_generated_sensors(hass)
    _LOGGER.debug(f"Found {len(existing_generated)} existing generated sensor groups")
    
    # Create a set of base names to track what we're keeping
    base_names_to_keep = set()
    
    # Get entity registry for operations
    entity_registry = er.async_get(hass)
    
    # Create a list of entity IDs that will be kept
    entity_ids_to_keep = set()
    
    # Check for existing energy sensors to avoid duplication
    device_energy_sensors = check_existing_energy_sensors(hass)

    entities = []
    storage = await load_storage(storage_path)

    for sensor in power_sensors:
        base_name = sensor.replace("sensor.", "").replace("_power", "")
        base_names_to_keep.add(base_name)
        
        # Check if we already have this base_name handled
        if base_name in existing_generated:
            existing_entities = existing_generated[base_name]
            
            # Find main, daily, and monthly entities
            main_entity = next((e for e in existing_entities if e.endswith(f"{base_name}_energy")), None)
            daily_entity = next((e for e in existing_entities if "_daily_energy" in e), None)
            monthly_entity = next((e for e in existing_entities if "_monthly_energy" in e), None)
            
            # Keep track of entities we're keeping
            if main_entity:
                entity_ids_to_keep.add(main_entity)
                
            if create_daily and daily_entity:
                entity_ids_to_keep.add(daily_entity)
            elif daily_entity:
                # Should remove daily entity
                entity_registry.async_remove(daily_entity)
                _LOGGER.debug(f"Removed daily entity {daily_entity}")
                
            if create_monthly and monthly_entity:
                entity_ids_to_keep.add(monthly_entity)
            elif monthly_entity:
                # Should remove monthly entity
                entity_registry.async_remove(monthly_entity)
                _LOGGER.debug(f"Removed monthly entity {monthly_entity}")
            
            # If we're missing daily/monthly but should have them, create them
            if create_daily and not daily_entity:
                device_identifiers = get_source_device_info(hass, sensor)
                daily_sensor = DailyEnergySensor(hass, base_name, f"sensor.{base_name}_energy", storage_path, device_identifiers)
                entities.append(daily_sensor)
                
            if create_monthly and not monthly_entity:
                device_identifiers = get_source_device_info(hass, sensor)
                monthly_sensor = MonthlyEnergySensor(hass, base_name, f"sensor.{base_name}_energy", storage_path, device_identifiers)
                entities.append(monthly_sensor)
                
            # Note: For existing entities, they should be handled by async_setup_entry
            # which will recreate and re-add them to ensure proper linking during reload
            
            # Skip to next sensor as we've handled the existing ones
            continue
        
        # Check if this device already has energy sensors from another integration
        # But only if the sensor currently exists - during startup we should proceed anyway
        entity = entity_registry.async_get(sensor)
        device_id = entity.device_id if entity else None
        
        # Get device identifiers for proper device grouping
        device_identifiers = get_source_device_info(hass, sensor)
        
        # Only skip if device has energy sensors AND the source sensor is currently available
        if device_id and device_id in device_energy_sensors and hass.states.get(sensor) is not None:
            _LOGGER.info(f"Device for {sensor} already has energy sensors: {device_energy_sensors[device_id]}")
            continue
        
        # Create Energy Sensor (kWh) - always create it, even if source sensor isn't available yet
        energy_sensor = EnergySensor(hass, base_name, sensor, storage_path, device_identifiers)
        entities.append(energy_sensor)

        # Create Daily and Monthly Sensors if enabled
        if create_daily:
            daily_sensor = DailyEnergySensor(hass, base_name, f"sensor.{base_name}_energy", storage_path, device_identifiers)
            entities.append(daily_sensor)
            
        if create_monthly:
            monthly_sensor = MonthlyEnergySensor(hass, base_name, f"sensor.{base_name}_energy", storage_path, device_identifiers)
            entities.append(monthly_sensor)

    # Remove entities that are no longer needed
    for base_name, entity_ids in existing_generated.items():
        if base_name not in base_names_to_keep:
            _LOGGER.info(f"Removing entities for {base_name} as it's no longer selected")
            for entity_id in entity_ids:
                entity_registry.async_remove(entity_id)
                _LOGGER.debug(f"Removed entity {entity_id}")
        else:
            # Remove entities that are no longer needed (e.g., disabled daily/monthly)
            for entity_id in entity_ids:
                if entity_id not in entity_ids_to_keep:
                    entity_registry.async_remove(entity_id)
                    _LOGGER.debug(f"Removed entity {entity_id} as it's no longer needed")

    # Add entities using the correct async_add_entities callback
    if async_add_entities and entities:
        async_add_entities(entities)
    elif not entities:
        _LOGGER.info("No new energy sensors to add.")
    else:
        _LOGGER.error("async_add_entities callback not found for adding entities")

async def reset_energy_sensors_service(hass: HomeAssistant, call, entry: ConfigEntry = None) -> None:
	"""Service to reset energy sensor values (useful for correcting doubled values)."""
	_LOGGER.info("Resetting energy sensors")

	# Use the config entry from the call context if not provided
	if entry is None:
		# Try to get the first config entry for this domain
		entries = list(hass.data[DOMAIN].values())
		if not entries:
			_LOGGER.error("No config entry found for energy_sensor_generator.")
			return
		storage_path = entries[0]["storage"]
	else:
		storage_path = hass.data[DOMAIN][entry.entry_id]["storage"]

	# Get optional parameters from service call
	reset_factor = call.data.get("reset_factor", 0.5)  # Default to halving values
	reset_to_zero = call.data.get("reset_to_zero", False)
	selected_sensors = call.data.get("sensors", [])

	# Find all generated energy sensors
	existing_generated = find_generated_sensors(hass)
	
	# Load storage
	storage = await load_storage(storage_path)
	
	sensors_reset = 0
	for base_name, entity_ids in existing_generated.items():
		# If specific sensors selected, only reset those
		if selected_sensors and base_name not in selected_sensors:
			continue
			
		for entity_id in entity_ids:
			# Get the storage key for this entity
			if "_daily_energy" in entity_id:
				storage_key = f"{base_name}_daily_energy"
			elif "_monthly_energy" in entity_id:
				storage_key = f"{base_name}_monthly_energy"
			else:
				storage_key = f"{base_name}_energy"
			
			# Reset the stored value
			if storage_key in storage:
				old_value = storage[storage_key].get("value", 0.0) if isinstance(storage[storage_key], dict) else storage[storage_key]
				
				if reset_to_zero:
					new_value = 0.0
				else:
					new_value = old_value * reset_factor
				
				# Update storage
				if isinstance(storage[storage_key], dict):
					storage[storage_key]["value"] = new_value
				else:
					storage[storage_key] = new_value
				
				_LOGGER.info(f"Reset {entity_id}: {old_value:.4f} kWh -> {new_value:.4f} kWh")
				sensors_reset += 1
	
	# Save updated storage
	await save_storage(storage_path, storage)
	
	# Force entities to reload their state
	entity_registry = er.async_get(hass)
	for base_name, entity_ids in existing_generated.items():
		if selected_sensors and base_name not in selected_sensors:
			continue
		for entity_id in entity_ids:
			entity = entity_registry.async_get(entity_id)
			if entity:
				await hass.helpers.entity_component.async_update_entity(entity_id)
	
	_LOGGER.info(f"Reset {sensors_reset} energy sensors")

async def debug_sensor_detection_service(hass: HomeAssistant, call, entry: ConfigEntry = None) -> None:
	"""Service to debug sensor detection issues."""
	_LOGGER.info("=== DEBUG: Sensor Detection Analysis ===")
	
	# Get power sensors using detection logic
	all_power_sensors = detect_power_sensors(hass)
	_LOGGER.info(f"Total detected power sensors: {len(all_power_sensors)}")
	
	# Get selected sensors from config
	if entry is None:
		entries = list(hass.data[DOMAIN].values())
		if entries:
			options = getattr(entries[0], "options", {})
		else:
			options = {}
	else:
		options = entry.options
	
	selected_sensors = options.get("selected_power_sensors", [])
	_LOGGER.info(f"Selected sensors in config: {selected_sensors}")
	
	# Check each selected sensor
	for sensor in selected_sensors:
		state = hass.states.get(sensor)
		if state:
			unit = state.attributes.get("unit_of_measurement", "")
			device_class = state.attributes.get("device_class", "")
			_LOGGER.info(f"Sensor {sensor}: Available, unit='{unit}', device_class='{device_class}', state={state.state}")
		else:
			_LOGGER.warning(f"Sensor {sensor}: NOT AVAILABLE")
	
	# Check existing generated sensors
	existing_generated = find_generated_sensors(hass)
	_LOGGER.info(f"Existing generated sensors: {len(existing_generated)} groups")
	for base_name, entities in existing_generated.items():
		_LOGGER.info(f"  {base_name}: {entities}")
	
	_LOGGER.info("=== END DEBUG ===")

async def diagnose_sensor_service(hass: HomeAssistant, call, entry: ConfigEntry = None) -> None:
	"""Service to diagnose a specific energy sensor."""
	sensor_name = call.data.get("sensor_name", "")
	
	if not sensor_name:
		_LOGGER.error("No sensor name provided for diagnosis")
		return
	
	_LOGGER.info(f"Diagnosing energy sensor: {sensor_name}")
	
	# Find the sensor entity
	entity_registry = er.async_get(hass)
	energy_entity = None
	
	# First, try exact match
	if sensor_name.startswith("sensor."):
		if sensor_name in entity_registry.entities and entity_registry.entities[sensor_name].platform == DOMAIN:
			energy_entity = sensor_name
	
	# If not found, try partial matches
	if not energy_entity:
		for entity_id, entity_entry in entity_registry.entities.items():
			if entity_entry.platform == DOMAIN:
				# Check if sensor_name is part of the entity_id or name
				if (sensor_name.lower() in entity_id.lower() or 
					(entity_entry.name and sensor_name.lower() in entity_entry.name.lower())):
					energy_entity = entity_id
					_LOGGER.info(f"Found matching sensor: {entity_id}")
					break
	
	if not energy_entity:
		_LOGGER.error(f"Could not find energy sensor matching: {sensor_name}")
		_LOGGER.info("Available energy sensors from this integration:")
		for entity_id, entity_entry in entity_registry.entities.items():
			if entity_entry.platform == DOMAIN:
				_LOGGER.info(f"  {entity_id} ({entity_entry.name})")
		return
	
	# Get the sensor state
	state = hass.states.get(energy_entity)
	if not state:
		_LOGGER.error(f"Could not get state for: {energy_entity}")
		return
	
	_LOGGER.info(f"DIAGNOSIS for {energy_entity}:")
	_LOGGER.info(f"  Current value: {state.state} {state.attributes.get('unit_of_measurement', 'N/A')}")
	
	# Get attributes
	attrs = state.attributes
	_LOGGER.info(f"  Last power: {attrs.get('last_power', 'N/A')}")
	_LOGGER.info(f"  Last update: {attrs.get('last_update', 'N/A')}")
	_LOGGER.info(f"  Conversion factor: {attrs.get('power_to_kw_factor', 'N/A')}")
	_LOGGER.info(f"  Source unit: {attrs.get('source_unit', 'N/A')}")
	_LOGGER.info(f"  Calculation count: {attrs.get('calculation_count', 'N/A')}")
	_LOGGER.info(f"  Source current value: {attrs.get('source_current_value', 'N/A')}")
	_LOGGER.info(f"  Source unit of measurement: {attrs.get('source_unit_of_measurement', 'N/A')}")
	_LOGGER.info(f"  Sample interval: {attrs.get('sample_interval', 'N/A')} seconds")
	
	# Check source sensor
	source_sensor = None
	for attr_name, attr_value in attrs.items():
		if "source" in attr_name.lower() and "sensor" in attr_name.lower():
			source_sensor = attr_value
			break
	
	if not source_sensor:
		# Try to deduce from entity_id
		if "_energy" in energy_entity:
			base_name = energy_entity.replace("sensor.", "").replace("_energy", "").replace("_daily", "").replace("_monthly", "")
			source_sensor = f"sensor.{base_name}_power"
	
	if source_sensor:
		source_state = hass.states.get(source_sensor)
		if source_state:
			_LOGGER.info(f"SOURCE SENSOR {source_sensor}:")
			_LOGGER.info(f"  Current value: {source_state.state}")
			_LOGGER.info(f"  Unit: {source_state.attributes.get('unit_of_measurement', 'N/A')}")
			_LOGGER.info(f"  Device class: {source_state.attributes.get('device_class', 'N/A')}")
			_LOGGER.info(f"  State class: {source_state.attributes.get('state_class', 'N/A')}")
		else:
			_LOGGER.error(f"SOURCE SENSOR {source_sensor} NOT FOUND")
	
	# Get storage information
	if entry:
		storage_path = hass.data[DOMAIN][entry.entry_id]["storage"]
		try:
			storage = await load_storage(storage_path)
			# Find storage key
			storage_key = None
			for key in storage.keys():
				if sensor_name.lower() in key.lower():
					storage_key = key
					break
			
			if storage_key:
				_LOGGER.info(f"STORAGE DATA for {storage_key}:")
				storage_data = storage[storage_key]
				if isinstance(storage_data, dict):
					for k, v in storage_data.items():
						_LOGGER.info(f"  {k}: {v}")
				else:
					_LOGGER.info(f"  Legacy value: {storage_data}")
			else:
				_LOGGER.info("No storage data found")
		except Exception as e:
			_LOGGER.error(f"Error reading storage: {e}")

async def list_sensors_service(hass: HomeAssistant, call, entry: ConfigEntry = None) -> None:
	"""Service to list all available energy sensors from this integration."""
	_LOGGER.info("=== LISTING ALL ENERGY SENSORS ===")
	
	entity_registry = er.async_get(hass)
	energy_sensors = []
	
	# Find all sensors from this integration
	for entity_id, entity_entry in entity_registry.entities.items():
		if entity_entry.platform == DOMAIN:
			energy_sensors.append((entity_id, entity_entry.name or "No Name"))
	
	_LOGGER.info(f"Found {len(energy_sensors)} energy sensors from this integration:")
	for entity_id, name in energy_sensors:
		state = hass.states.get(entity_id)
		if state:
			attrs = state.attributes
			method = attrs.get("calculation_method", "unknown")
			value = state.state
			_LOGGER.info(f"  {entity_id} ({name}) - Value: {value} kWh, Method: {method}")
		else:
			_LOGGER.info(f"  {entity_id} ({name}) - NOT AVAILABLE")
	
	if not energy_sensors:
		_LOGGER.warning("No energy sensors found from this integration!")
		_LOGGER.info("Checking all sensors in registry:")
		for entity_id, entity_entry in entity_registry.entities.items():
			if "energy" in entity_id.lower():
				_LOGGER.info(f"  {entity_id} (platform: {entity_entry.platform})")
