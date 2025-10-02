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
from .sensor import EnergySensor, DailyEnergySensor, MonthlyEnergySensor, WeeklyEnergySensor, AnnualEnergySensor, SyntheticGridTotalEnergySensor
from .utils import StorageManager
from .const import DOMAIN, STORAGE_FILE, CONF_DEBUG_LOGGING, CONF_CREATE_SYNTHETIC_GRID_TOTAL
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
            # Specifically identify energy sensors to help with debugging
            if unit in ["kWh", "kwh"] or device_class == "energy":
                _debug_log(hass, f"✗ Skipped ENERGY sensor: {entity_id} (unit: '{unit}', device_class: '{device_class}', state: {state.state}) - Energy sensors cannot be used as power sources")
            else:
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
                elif "_weekly_energy" in unique_id:
                    base_name = unique_id.replace("_weekly_energy", "")
                elif "_annual_energy" in unique_id:
                    base_name = unique_id.replace("_annual_energy", "")
                else:
                    base_name = unique_id.replace("_energy", "")
                
                # Normalize to lowercase for consistency with sensor generation
                base_name = base_name.lower()
                
                if base_name not in result:
                    result[base_name] = []
                result[base_name].append(entity_id)
    
    _debug_log(hass, f"Found {len(result)} generated sensor groups: {result}")
    return result

def get_source_device_info(hass: HomeAssistant, source_entity_id: str):
    """Get the device info for a source entity."""
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    
    # Synthetic base has no source device
    if source_entity_id.endswith("synthetic_grid_total_power") or "synthetic_grid_total" in source_entity_id:
        return None

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

    # Setup storage manager (HA Store-based)
    storage_manager = StorageManager(hass)
    
    # Create main integration device
    from homeassistant.helpers import device_registry as dr
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "main")},
        name="Energy Sensor Generator",
        manufacturer="Energy Sensor Generator",
        model="Integration",
        sw_version="0.0.78",
    )
    
    # Store references in hass.data
    hass.data[DOMAIN][entry.entry_id] = {
        "config": entry.data,
        "storage": Path(hass.config.path(STORAGE_FILE)),
        "storage_manager": storage_manager,
        "options": entry.options,
        "unsubscribers": [],
        "reload_scheduled": False,
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
    
    # Register migration service for fixing mismatched entity IDs
    hass.services.async_register(
        DOMAIN,
        "migrate_entity_ids",
        lambda call: migrate_entity_ids_service(hass, call, entry)
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
    
    # Register export/import services
    hass.services.async_register(
        DOMAIN,
        "export_energy_data",
        lambda call: export_energy_data_service(hass, call, entry)
    )
    hass.services.async_register(
        DOMAIN,
        "import_energy_data",
        lambda call: import_energy_data_service(hass, call, entry)
    )
    
    # Register adjust energy service for fixing spikes/errors
    hass.services.async_register(
        DOMAIN,
        "adjust_energy",
        lambda call: adjust_energy_service(hass, call, entry)
    )
    
    # Register service to copy hourly data from previous hour
    hass.services.async_register(
        DOMAIN,
        "copy_from_previous_hour",
        lambda call: copy_from_previous_hour_service(hass, call, entry)
    )
    
    # Register service to clear statistical calculation tracking (force fresh calculation)
    hass.services.async_register(
        DOMAIN,
        "reset_statistical_tracking",
        lambda call: reset_statistical_tracking_service(hass, call, entry)
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
            await asyncio.sleep(3)
            
            # Attempt generation a few times; service will reload entry if platform not yet ready
            for attempt in range(5):
                await generate_sensors_service(hass, None, entry)
                _LOGGER.info("Attempted sensor generation during startup")
                await asyncio.sleep(2)
        
        # Schedule the delayed generation
        hass.async_create_task(delayed_sensor_generation())
    
    return True
    
async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Cancel all periodic tasks
    for unsub in hass.data[DOMAIN][entry.entry_id].get("unsubscribers", []):
        unsub()
    
    # Flush pending storage writes
    try:
        storage_manager = hass.data[DOMAIN][entry.entry_id].get("storage_manager")
        if storage_manager:
            await storage_manager.async_flush()
    except Exception:
        pass

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
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            _LOGGER.error("No config entry found for energy_sensor_generator.")
            return
        entry = entries[0]
    
    options = entry.options
    storage_manager = hass.data[DOMAIN][entry.entry_id]["storage_manager"]
    
    # Ensure the platform is loaded; we will add via async_forward_entry_setups and recreate entities

    # Get power sensors using more flexible detection
    all_power_sensors = detect_power_sensors(hass)
    _LOGGER.info(f"Auto-detected {len(all_power_sensors)} power sensors: {all_power_sensors}")

    # Use selected power sensors from options if present
    selected_sensors = options.get("selected_power_sensors") if options else None
    _LOGGER.info(f"Configuration options: {options}")
    _LOGGER.info(f"Selected sensors from config: {selected_sensors}")
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

    # Check if we should create period sensors
    create_daily = options.get("create_daily_sensors", True)
    create_monthly = options.get("create_monthly_sensors", True)
    create_weekly = options.get("create_weekly_sensors", True)
    create_annual = options.get("create_annual_sensors", True)
    
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
    # Load storage via central manager
    storage = await storage_manager.async_load()

    _LOGGER.info(f"About to process {len(power_sensors)} power sensors: {power_sensors}")

    for sensor in power_sensors:
        # Derive a canonical base name. We intentionally disambiguate sensors like
        #   sensor.smart_plug_power_2  vs  sensor.smart_plug_2_power
        # by mapping them to different base names to avoid collisions.
        raw_id = sensor.replace("sensor.", "")
        candidate_base = raw_id.replace("_power", "")

        # If the pattern is <root>_power_<n>, prefer <root>_energy_<n>
        # so it does not collide with <root>_<n>_power (which becomes <root>_<n>).
        if "_power_" in raw_id:
            parts = raw_id.rsplit("_power_", 1)
            if len(parts) == 2 and parts[1].isdigit():
                alt_base = f"{parts[0]}_energy_{parts[1]}"
                _LOGGER.info(
                    f"Disambiguated base name for {sensor}: '{candidate_base}' -> '{alt_base}'"
                )
                candidate_base = alt_base

        base_name = candidate_base.lower()
        base_names_to_keep.add(base_name)

        _LOGGER.info(f"Processing power sensor: {sensor} -> base_name: '{base_name}'")
        
        # Check if we already have this base_name handled
        if base_name in existing_generated:
            existing_entities = existing_generated[base_name]
            _LOGGER.info(f"Found existing entities for {base_name}: {existing_entities}")
            
            # Find main, daily, monthly, weekly, and annual entities
            main_entity = next((e for e in existing_entities if e.lower().endswith(f"{base_name}_energy")), None)
            daily_entity = next((e for e in existing_entities if "_daily_energy" in e), None)
            monthly_entity = next((e for e in existing_entities if "_monthly_energy" in e), None)
            weekly_entity = next((e for e in existing_entities if "_weekly_energy" in e), None)
            annual_entity = next((e for e in existing_entities if "_annual_energy" in e), None)
            
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
            if create_weekly and weekly_entity:
                entity_ids_to_keep.add(weekly_entity)
            elif weekly_entity:
                entity_registry.async_remove(weekly_entity)
                _LOGGER.debug(f"Removed weekly entity {weekly_entity}")
            if create_annual and annual_entity:
                entity_ids_to_keep.add(annual_entity)
            elif annual_entity:
                entity_registry.async_remove(annual_entity)
                _LOGGER.debug(f"Removed annual entity {annual_entity}")
            
            # If we're missing daily/monthly but should have them, create them
            # Determine the main energy sensor entity_id
            if base_name.endswith("_energy") or "_energy_" in base_name:
                main_energy_entity = f"sensor.{base_name}"
            else:
                main_energy_entity = f"sensor.{base_name}_energy"

            if create_daily and not daily_entity:
                device_identifiers = get_source_device_info(hass, sensor)
                daily_sensor = DailyEnergySensor(hass, base_name, main_energy_entity, storage_manager, device_identifiers)
                entities.append(daily_sensor)
                
            if create_monthly and not monthly_entity:
                device_identifiers = get_source_device_info(hass, sensor)
                monthly_sensor = MonthlyEnergySensor(hass, base_name, main_energy_entity, storage_manager, device_identifiers)
                entities.append(monthly_sensor)
            if create_weekly and not weekly_entity:
                device_identifiers = get_source_device_info(hass, sensor)
                weekly_sensor = WeeklyEnergySensor(hass, base_name, main_energy_entity, storage_manager, device_identifiers)
                entities.append(weekly_sensor)
            if create_annual and not annual_entity:
                device_identifiers = get_source_device_info(hass, sensor)
                annual_sensor = AnnualEnergySensor(hass, base_name, main_energy_entity, storage_manager, device_identifiers)
                entities.append(annual_sensor)
                
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

        # Only skip if device has energy sensors from OTHER integrations (not this one) AND the source sensor is currently available
        if device_id and device_id in device_energy_sensors and hass.states.get(sensor) is not None:
            existing_sensors = device_energy_sensors[device_id]
            # Check if any existing energy sensors are from OTHER integrations (not this one)
            has_other_integration_sensors = any(
                entity_id.startswith("sensor.") and
                (entry := entity_registry.async_get(entity_id)) and
                entry.platform != DOMAIN
                for entity_id in existing_sensors
            )

            if has_other_integration_sensors:
                _LOGGER.info(f"Device for {sensor} already has energy sensors from other integrations: {existing_sensors} - SKIPPING")
                continue
            else:
                _LOGGER.info(f"Device for {sensor} has energy sensors from THIS integration: {existing_sensors} - will recreate/update")
        else:
            _LOGGER.info(f"Device for {sensor} - no existing energy sensors or sensor not available - will create new")
        
        # Create Energy Sensor (kWh) - always create it, even if source sensor isn't available yet
        _LOGGER.info(f"Creating new energy sensors for {sensor} (base_name: {base_name}) - this is the NEW sensor creation path")
        energy_sensor = EnergySensor(hass, base_name, sensor, storage_manager, device_identifiers)
        entities.append(energy_sensor)

        # Determine the main energy sensor entity_id for period sensors to reference
        # If base_name already contains "_energy" (e.g., "smart_plug_energy_2"), use it directly
        if base_name.endswith("_energy") or "_energy_" in base_name:
            main_energy_entity = f"sensor.{base_name}"
        else:
            main_energy_entity = f"sensor.{base_name}_energy"

        # Create Daily and Monthly Sensors if enabled
        if create_daily:
            daily_sensor = DailyEnergySensor(hass, base_name, main_energy_entity, storage_manager, device_identifiers)
            entities.append(daily_sensor)
            
        if create_monthly:
            monthly_sensor = MonthlyEnergySensor(hass, base_name, main_energy_entity, storage_manager, device_identifiers)
            entities.append(monthly_sensor)
        if create_weekly:
            weekly_sensor = WeeklyEnergySensor(hass, base_name, main_energy_entity, storage_manager, device_identifiers)
            entities.append(weekly_sensor)
        if create_annual:
            annual_sensor = AnnualEnergySensor(hass, base_name, main_energy_entity, storage_manager, device_identifiers)
            entities.append(annual_sensor)

    # Remove entities that are no longer needed
    entities_removed = 0
    for base_name, entity_ids in existing_generated.items():
        if base_name not in base_names_to_keep:
            _LOGGER.info(f"Removing entities for {base_name} as it's no longer selected")
            for entity_id in entity_ids:
                try:
                    entity_registry.async_remove(entity_id)
                    entities_removed += 1
                    _LOGGER.debug(f"Removed entity {entity_id}")
                except Exception as e:
                    _LOGGER.warning(f"Failed to remove entity {entity_id}: {e}")
        else:
            # Remove entities that are no longer needed (e.g., disabled daily/monthly)
            for entity_id in entity_ids:
                if entity_id not in entity_ids_to_keep:
                    try:
                        entity_registry.async_remove(entity_id)
                        entities_removed += 1
                        _LOGGER.debug(f"Removed entity {entity_id} as it's no longer needed")
                    except Exception as e:
                        _LOGGER.warning(f"Failed to remove entity {entity_id}: {e}")
    
    if entities_removed > 0:
        _LOGGER.info(f"Removed {entities_removed} entities that are no longer needed")

    # Handle synthetic grid total sensor
    try:
        entity_registry = er.async_get(hass)
        existing_grid = None
        for entity_id, entry_reg in entity_registry.entities.items():
            if entry_reg.platform == DOMAIN and (entry_reg.unique_id == "synthetic_grid_total_energy"):
                existing_grid = entity_id
                break
        
        if options.get(CONF_CREATE_SYNTHETIC_GRID_TOTAL, False):
            # Option is enabled - add synthetic grid sensor if it doesn't exist
            if not existing_grid:
                entities.append(SyntheticGridTotalEnergySensor(hass))
                _LOGGER.info("Adding synthetic grid total energy sensor")
        else:
            # Option is disabled - remove synthetic grid sensor if it exists
            if existing_grid:
                try:
                    entity_registry.async_remove(existing_grid)
                    _LOGGER.info(f"Removed synthetic grid total sensor {existing_grid} as option is disabled")
                except Exception as e:
                    _LOGGER.warning(f"Failed to remove synthetic grid sensor {existing_grid}: {e}")
    except Exception as e:
        _LOGGER.error(f"Failed to handle Synthetic Grid Total sensor: {e}")

    # Add entities using the stored callback in sensor platform if present, otherwise ask HA to reload entry
    if entities:
        add_cb = hass.data[DOMAIN][entry.entry_id].get("async_add_entities")
        if add_cb:
            add_cb(entities)
            _LOGGER.info(f"Successfully added {len(entities)} new energy sensors")
        else:
            # Defer entity creation to platform by reloading once; avoid tight loops/non-recoverable states
            flags = hass.data[DOMAIN][entry.entry_id]
            if not flags.get("reload_scheduled"):
                flags["reload_scheduled"] = True
                _LOGGER.debug("async_add_entities not available; scheduling single deferred reload")
                async def _reload_once():
                    import asyncio
                    await asyncio.sleep(2)
                    try:
                        await hass.config_entries.async_reload(entry.entry_id)
                    except Exception as e:
                        _LOGGER.debug(f"Deferred reload failed: {e}")
                    finally:
                        # Allow future reloads if needed
                        flags["reload_scheduled"] = False
                hass.async_create_task(_reload_once())
    else:
        _LOGGER.info("No new energy sensors to add.")

    # Persist any changes
    await storage_manager.async_save(storage)

async def reset_energy_sensors_service(hass: HomeAssistant, call, entry: ConfigEntry = None) -> None:
	"""Service to reset energy sensor values (useful for correcting doubled values)."""
	_LOGGER.info("Resetting energy sensors")

	# Use the config entry from the call context if not provided
	if entry is None:
		entries = list(hass.data[DOMAIN].values())
		if not entries:
			_LOGGER.error("No config entry found for energy_sensor_generator.")
			return
		storage_manager = entries[0]["storage_manager"]
	else:
		storage_manager = hass.data[DOMAIN][entry.entry_id]["storage_manager"]

	# Get optional parameters from service call
	reset_factor = call.data.get("reset_factor", 0.5)  # Default to halving values
	reset_to_zero = call.data.get("reset_to_zero", False)
	selected_sensors = call.data.get("sensors", [])

	# Find all generated energy sensors
	existing_generated = find_generated_sensors(hass)
	
	# Load storage
	storage = await storage_manager.async_load()
	
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
			elif "_weekly_energy" in entity_id:
				storage_key = f"{base_name}_weekly_energy"
			elif "_annual_energy" in entity_id:
				storage_key = f"{base_name}_annual_energy"
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
	await storage_manager.async_save(storage)
	
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

async def migrate_entity_ids_service(hass: HomeAssistant, call, entry: ConfigEntry = None) -> None:
	"""Service to migrate entity IDs to match unique_id patterns."""
	if entry is None:
		entries = hass.config_entries.async_entries(DOMAIN)
		if not entries:
			_LOGGER.error("No config entry found for energy_sensor_generator.")
			return
		entry = entries[0]
	
	entity_registry = er.async_get(hass)
	migrated_count = 0
	skipped_count = 0
	error_count = 0
	
	_LOGGER.info("=== STARTING ENTITY ID MIGRATION ===")
	
	# Look for entities with this integration's platform
	for entity_id, entity_entry in entity_registry.entities.items():
		if entity_entry.platform == DOMAIN and entity_entry.config_entry_id == entry.entry_id:
			unique_id = entity_entry.unique_id
			
			# Determine expected entity_id from unique_id
			if unique_id.endswith("_daily_energy"):
				expected_entity_id = f"sensor.{unique_id}"
			elif unique_id.endswith("_monthly_energy"):
				expected_entity_id = f"sensor.{unique_id}"
			elif unique_id.endswith("_weekly_energy"):
				expected_entity_id = f"sensor.{unique_id}"
			elif unique_id.endswith("_annual_energy"):
				expected_entity_id = f"sensor.{unique_id}"
			elif unique_id.endswith("_energy") or "_energy_" in unique_id:
				expected_entity_id = f"sensor.{unique_id}"
			else:
				# Skip non-energy sensors or unexpected patterns
				continue
			
			# Check if entity_id needs migration
			if entity_id != expected_entity_id:
				# Check if target entity_id is already taken
				if expected_entity_id in entity_registry.entities:
					_LOGGER.warning(
						f"Cannot migrate {entity_id} to {expected_entity_id} - target already exists"
					)
					skipped_count += 1
				else:
					# Migrate the entity_id
					try:
						entity_registry.async_update_entity(
							entity_id,
							new_entity_id=expected_entity_id
						)
						_LOGGER.info(f"✓ Migrated: {entity_id} → {expected_entity_id}")
						migrated_count += 1
					except Exception as e:
						_LOGGER.error(f"✗ Failed to migrate {entity_id}: {e}")
						error_count += 1
	
	_LOGGER.info(f"=== MIGRATION COMPLETE ===")
	_LOGGER.info(f"  Migrated: {migrated_count}")
	_LOGGER.info(f"  Skipped: {skipped_count}")
	_LOGGER.info(f"  Errors: {error_count}")
	
	# Create a persistent notification with the results
	await hass.services.async_call(
		"persistent_notification",
		"create",
		{
			"title": "Entity ID Migration Complete",
			"message": f"Migrated {migrated_count} entities\nSkipped {skipped_count} (conflicts)\nErrors: {error_count}",
			"notification_id": "energy_sensor_migration"
		}
	)

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
		storage_manager = hass.data[DOMAIN][entry.entry_id]["storage_manager"]
		try:
			storage = await storage_manager.async_load()
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


async def export_energy_data_service(hass: HomeAssistant, call, entry: ConfigEntry = None) -> None:
	"""Export the integration's energy data to a JSON file under the HA config directory."""
	try:
		if entry is None:
			entries = hass.config_entries.async_entries(DOMAIN)
			if not entries:
				_LOGGER.error("No config entry found for export.")
				return
			entry = entries[0]
		storage_manager = hass.data[DOMAIN][entry.entry_id]["storage_manager"]
		# Load current storage
		storage = await storage_manager.async_load()
		# Compute target path
		relative = call.data.get("target_path")
		if relative:
			from pathlib import Path
			target = Path(hass.config.path(relative))
			# Ensure parent dirs exist
			target.parent.mkdir(parents=True, exist_ok=True)
		else:
			from datetime import datetime
			from pathlib import Path
			ts = datetime.now().strftime("%Y%m%d_%H%M%S")
			target = Path(hass.config.path(f"energy_backup_{ts}.json"))
		# Write JSON
		try:
			import json
			with target.open("w", encoding="utf-8") as f:
				json.dump(storage, f, indent=2)
			_LOGGER.info(f"Exported energy data to {target}")
		except Exception as e:
			_LOGGER.error(f"Failed to write export file {target}: {e}")
	except Exception as e:
		_LOGGER.error(f"Export service failed: {e}")


async def import_energy_data_service(hass: HomeAssistant, call, entry: ConfigEntry = None) -> None:
	"""Import energy data from a JSON file, supporting merge/replace and base-name reassignment."""
	try:
		if entry is None:
			entries = hass.config_entries.async_entries(DOMAIN)
			if not entries:
				_LOGGER.error("No config entry found for import.")
				return
			entry = entries[0]
		storage_manager = hass.data[DOMAIN][entry.entry_id]["storage_manager"]
		# Load current storage
		current = await storage_manager.async_load()
		# Read source JSON
		source_rel = call.data.get("source_path")
		if not source_rel:
			_LOGGER.error("source_path is required for import_energy_data")
			return
		from pathlib import Path
		source = Path(hass.config.path(source_rel))
		if not source.exists():
			_LOGGER.error(f"Import file not found: {source}")
			return
		try:
			import json
			with source.open("r", encoding="utf-8") as f:
				incoming = json.load(f)
		except Exception as e:
			_LOGGER.error(f"Failed to read import file {source}: {e}")
			return
		# Optional reassignment of base names (e.g., plug_1 -> plug_2)
		reassign_from = call.data.get("reassign_from", []) or []
		reassign_to = call.data.get("reassign_to", []) or []
		if reassign_from and (len(reassign_from) != len(reassign_to)):
			_LOGGER.error("reassign_from and reassign_to must have equal length")
			return
		reassignment_map = dict(zip(reassign_from, reassign_to))
		def remap_key(key: str) -> str:
			# Keys are like base_energy, base_daily_energy, etc.
			for old, new in reassignment_map.items():
				if key.startswith(old + "_") or key == old:
					return key.replace(old, new, 1)
			return key
		
		remapped = {}
		for k, v in incoming.items():
			remapped[remap_key(k)] = v
		
		mode = (call.data.get("mode") or "merge").lower()
		if mode not in ("merge", "replace"):
			mode = "merge"
		
		if mode == "replace":
			merged = remapped
		else:
			# Merge dictionaries; if dict entries exist, prefer incoming
			merged = dict(current)
			for k, v in remapped.items():
				merged[k] = v
		
		# Save merged data
		await storage_manager.async_save(merged)
		_LOGGER.info(f"Imported energy data from {source} with mode={mode}, reassigned={len(reassignment_map)} mappings")
		
		# Force sensor updates to pick up new values
		entity_registry = er.async_get(hass)
		for entity_id, entry_reg in entity_registry.entities.items():
			if entry_reg.platform == DOMAIN and entity_id.startswith("sensor."):
				try:
					await hass.helpers.entity_component.async_update_entity(entity_id)
				except Exception:
					pass
	except Exception as e:
		_LOGGER.error(f"Import service failed: {e}")


async def copy_from_previous_hour_service(hass: HomeAssistant, call, entry: ConfigEntry = None) -> None:
	"""Copy all sensor values from a previous hour - useful for fixing hourly spikes."""
	if entry is None:
		entries = hass.config_entries.async_entries(DOMAIN)
		if not entries:
			_LOGGER.error("No config entry found for copy_from_previous_hour.")
			return
		entry = entries[0]
	
	storage_manager = hass.data[DOMAIN][entry.entry_id]["storage_manager"]
	
	# Get the target datetime from the service call
	target_datetime_str = call.data.get("target_datetime")
	hours_back = call.data.get("hours_back", 1)  # Default to 1 hour back
	
	if not target_datetime_str:
		_LOGGER.error("target_datetime is required (format: 'YYYY-MM-DD HH:00:00')")
		return
	
	try:
		from datetime import datetime
		import homeassistant.util.dt as dt_util
		
		# Parse the target datetime
		target_dt = datetime.fromisoformat(target_datetime_str)
		if target_dt.tzinfo is None:
			target_dt = dt_util.as_local(target_dt)
		
		_LOGGER.info(f"Looking for sensor values at {target_dt} (to copy to current values)")
		
	except Exception as e:
		_LOGGER.error(f"Invalid datetime format: {target_datetime_str}. Use format: 'YYYY-MM-DD HH:00:00'. Error: {e}")
		return
	
	# Find all energy sensors from this integration
	entity_registry = er.async_get(hass)
	energy_sensors = []
	
	for entity_id, entity_entry in entity_registry.entities.items():
		if entity_entry.platform == DOMAIN and entity_id.startswith("sensor."):
			# Skip daily/monthly/weekly/annual sensors - only work with main energy sensors
			if not any(period in entity_id for period in ["_daily_", "_monthly_", "_weekly_", "_annual_"]):
				energy_sensors.append(entity_id)
	
	if not energy_sensors:
		_LOGGER.error("No energy sensors found to copy")
		return
	
	_LOGGER.info(f"Found {len(energy_sensors)} energy sensors to process")
	
	# Try to get historical states using recorder
	try:
		from homeassistant.components import recorder
		from homeassistant.components.recorder import history
		
		# Calculate the time range to look for the value
		# We want the state AT the target time
		start_time = target_dt - timedelta(minutes=5)  # Small buffer
		end_time = target_dt + timedelta(minutes=5)
		
		sensors_updated = 0
		errors = []
		
		for entity_id in energy_sensors:
			try:
				# Get historical states for this sensor
				states = await hass.async_add_executor_job(
					history.state_changes_during_period,
					hass,
					start_time,
					end_time,
					entity_id
				)
				
				if entity_id in states and states[entity_id]:
					# Find the state closest to target_dt
					closest_state = None
					min_diff = None
					
					for state in states[entity_id]:
						if state.state not in ("unknown", "unavailable"):
							state_time = state.last_updated
							time_diff = abs((state_time - target_dt).total_seconds())
							if min_diff is None or time_diff < min_diff:
								min_diff = time_diff
								closest_state = state
					
					if closest_state:
						try:
							historical_value = float(closest_state.state)
							
							# Get storage key from entity's unique_id
							entity_entry = entity_registry.async_get(entity_id)
							if entity_entry:
								storage_key = entity_entry.unique_id
								
								# Load and update storage
								storage = await storage_manager.async_load()
								
								if storage_key in storage:
									if isinstance(storage[storage_key], dict):
										old_value = storage[storage_key].get("value", 0.0)
										storage[storage_key]["value"] = historical_value
									else:
										old_value = storage[storage_key]
										storage[storage_key] = historical_value
									
									await storage_manager.async_save(storage)
									
									# Force entity update
									try:
										await hass.helpers.entity_component.async_update_entity(entity_id)
									except Exception:
										pass
									
									_LOGGER.info(f"✓ {entity_id}: {old_value:.4f} → {historical_value:.4f} kWh (from {closest_state.last_updated})")
									sensors_updated += 1
								else:
									errors.append(f"{entity_id}: No storage key found")
						except (ValueError, TypeError) as e:
							errors.append(f"{entity_id}: Invalid value {closest_state.state}")
					else:
						errors.append(f"{entity_id}: No valid state found near {target_dt}")
				else:
					errors.append(f"{entity_id}: No historical data found")
			
			except Exception as e:
				errors.append(f"{entity_id}: {str(e)}")
		
		# Log summary
		_LOGGER.info(f"Copy complete: {sensors_updated} sensors updated, {len(errors)} errors")
		
		if errors:
			_LOGGER.warning(f"Errors during copy: {', '.join(errors[:5])}" + (f" and {len(errors)-5} more..." if len(errors) > 5 else ""))
		
		# Create notification
		await hass.services.async_call(
			"persistent_notification",
			"create",
			{
				"title": "Hourly Data Copy Complete",
				"message": f"Copied values from {target_dt.strftime('%Y-%m-%d %H:%M')}\n\n✓ Updated: {sensors_updated} sensors\n✗ Errors: {len(errors)} sensors\n\nCheck logs for details.",
				"notification_id": "energy_hourly_copy"
			}
		)
		
	except ImportError:
		_LOGGER.error("Recorder component not available - cannot retrieve historical data")
	except Exception as e:
		_LOGGER.error(f"Failed to copy hourly data: {e}", exc_info=True)


async def reset_statistical_tracking_service(hass: HomeAssistant, call, entry: ConfigEntry = None) -> None:
	"""Reset the last_statistical_calculation timestamp to force a fresh calculation."""
	if entry is None:
		entries = hass.config_entries.async_entries(DOMAIN)
		if not entries:
			_LOGGER.error("No config entry found for reset_statistical_tracking.")
			return
		entry = entries[0]
	
	storage_manager = hass.data[DOMAIN][entry.entry_id]["storage_manager"]
	
	# Find all energy sensors from this integration
	entity_registry = er.async_get(hass)
	sensors_reset = 0
	
	storage = await storage_manager.async_load()
	
	for entity_id, entity_entry in entity_registry.entities.items():
		if entity_entry.platform == DOMAIN and entity_id.startswith("sensor."):
			# Skip daily/monthly/weekly/annual sensors - only work with main energy sensors
			if not any(period in entity_id for period in ["_daily_", "_monthly_", "_weekly_", "_annual_"]):
				storage_key = entity_entry.unique_id
				
				if storage_key in storage and isinstance(storage[storage_key], dict):
					# Remove the last_statistical_calculation timestamp
					if "last_statistical_calculation" in storage[storage_key]:
						del storage[storage_key]["last_statistical_calculation"]
						sensors_reset += 1
						_LOGGER.info(f"Reset statistical tracking for {entity_id}")
	
	await storage_manager.async_save(storage)
	
	_LOGGER.info(f"Reset statistical tracking for {sensors_reset} sensors - next calculation will use lookback window")
	
	# Create notification
	await hass.services.async_call(
		"persistent_notification",
		"create",
		{
			"title": "Statistical Tracking Reset",
			"message": f"Reset {sensors_reset} sensors.\n\nNext calculation will use the lookback window instead of incremental calculation.\n\nThis can help if you suspect double-counting occurred.",
			"notification_id": "energy_stat_reset"
		}
	)


async def adjust_energy_service(hass: HomeAssistant, call, entry: ConfigEntry = None) -> None:
	"""Adjust energy sensor value - useful for correcting spikes or errors."""
	if entry is None:
		entries = hass.config_entries.async_entries(DOMAIN)
		if not entries:
			_LOGGER.error("No config entry found for adjust_energy.")
			return
		entry = entries[0]
	
	storage_manager = hass.data[DOMAIN][entry.entry_id]["storage_manager"]
	entity_id = call.data.get("entity_id", "")
	adjustment_kwh = call.data.get("adjustment_kwh")
	set_to_value = call.data.get("set_to_value")
	copy_from_entity = call.data.get("copy_from_entity")
	
	if not entity_id:
		_LOGGER.error("entity_id is required for adjust_energy")
		return
	
	# Ensure only one adjustment method is specified
	methods_specified = sum([adjustment_kwh is not None, set_to_value is not None, copy_from_entity is not None])
	if methods_specified != 1:
		_LOGGER.error("Specify exactly ONE of: adjustment_kwh, set_to_value, or copy_from_entity")
		return
	
	# Find the entity and get its storage key
	entity_registry = er.async_get(hass)
	entity_entry = entity_registry.async_get(entity_id)
	
	if not entity_entry or entity_entry.platform != DOMAIN:
		_LOGGER.error(f"Entity {entity_id} not found or not from this integration")
		return
	
	# Determine storage key from unique_id
	unique_id = entity_entry.unique_id
	storage_key = unique_id
	
	# Load storage
	storage = await storage_manager.async_load()
	
	if storage_key not in storage:
		_LOGGER.error(f"No storage data found for {entity_id} (key: {storage_key})")
		return
	
	# Get current value
	if isinstance(storage[storage_key], dict):
		old_value = storage[storage_key].get("value", 0.0)
	else:
		old_value = storage[storage_key]
	
	# Calculate new value
	if adjustment_kwh is not None:
		new_value = old_value + adjustment_kwh
		action = f"adjusted by {adjustment_kwh:+.4f} kWh"
	elif set_to_value is not None:
		new_value = set_to_value
		action = f"set to {set_to_value:.4f} kWh"
	else:  # copy_from_entity
		copy_state = hass.states.get(copy_from_entity)
		if not copy_state or copy_state.state in ("unknown", "unavailable"):
			_LOGGER.error(f"Source entity {copy_from_entity} not available")
			return
		try:
			new_value = float(copy_state.state)
			action = f"copied from {copy_from_entity} ({new_value:.4f} kWh)"
		except (ValueError, TypeError):
			_LOGGER.error(f"Invalid value from {copy_from_entity}: {copy_state.state}")
			return
	
	# Update storage
	if isinstance(storage[storage_key], dict):
		storage[storage_key]["value"] = new_value
	else:
		storage[storage_key] = new_value
	
	await storage_manager.async_save(storage)
	
	# Force entity update
	try:
		await hass.helpers.entity_component.async_update_entity(entity_id)
	except Exception as e:
		_LOGGER.warning(f"Could not force update entity: {e}")
	
	_LOGGER.info(f"Energy adjusted for {entity_id}: {old_value:.4f} kWh -> {new_value:.4f} kWh ({action})")
	
	# Create persistent notification
	await hass.services.async_call(
		"persistent_notification",
		"create",
		{
			"title": "Energy Value Adjusted",
			"message": f"**{entity_id}**\n\nOld: {old_value:.4f} kWh\nNew: {new_value:.4f} kWh\n\n{action}",
			"notification_id": f"energy_adjust_{entity_id.replace('.', '_')}"
		}
	)
