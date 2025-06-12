import logging
from datetime import datetime, timedelta
import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import (
    SensorEntity, 
    SensorDeviceClass,
    SensorStateClass
)
from homeassistant.const import UnitOfEnergy
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change, async_track_time_interval
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import device_registry as dr
from .utils import load_storage, save_storage
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

def get_friendly_name(hass: HomeAssistant, entity_id: str) -> str:
	"""Get the friendly name for an entity, falling back to derived name from entity ID."""
	entity_registry = er.async_get(hass)
	entity_entry = entity_registry.async_get(entity_id)
	
	# Try to get custom name from entity registry first
	if entity_entry and entity_entry.name:
		# Remove "_power" suffix if present to get clean base name
		name = entity_entry.name
		if name.lower().endswith(" power"):
			name = name[:-6]  # Remove " power"
		elif name.lower().endswith("_power"):
			name = name[:-6]  # Remove "_power"
		return name
	
	# Try to get friendly_name from entity state
	state = hass.states.get(entity_id)
	if state and state.attributes.get("friendly_name"):
		name = state.attributes["friendly_name"]
		if name.lower().endswith(" power"):
			name = name[:-6]  # Remove " power"
		elif name.lower().endswith("_power"):
			name = name[:-6]  # Remove "_power"
		return name
	
	# Try to get device name if entity is part of a device
	if entity_entry and entity_entry.device_id:
		device_registry = dr.async_get(hass)
		device = device_registry.async_get(entity_entry.device_id)
		if device and device.name_by_user:
			return device.name_by_user
		elif device and device.name:
			return device.name
	
	# Fall back to deriving name from entity ID
	# Convert entity_id like "sensor.smart_plug_2_power" to "Smart Plug 2"
	base_name = entity_id.replace("sensor.", "").replace("_power", "")
	# Convert underscores to spaces and title case
	return base_name.replace("_", " ").title()

def get_friendly_name_from_base(hass: HomeAssistant, base_name: str) -> str:
	"""Get friendly name by trying different possible power sensor patterns."""
	# Try the most common pattern first
	possible_sensors = [
		f"sensor.{base_name}_power",
		f"sensor.{base_name}",
		f"{base_name}_power",
		f"{base_name}"
	]
	
	for sensor_id in possible_sensors:
		if hass.states.get(sensor_id):
			return get_friendly_name(hass, sensor_id)
	
	# If no sensor found, just clean up the base_name
	return base_name.replace("_", " ").title()

def get_unique_entity_name(hass: HomeAssistant, proposed_name: str, domain: str = "sensor") -> str:
	"""Generate a unique entity name by checking for conflicts and adding suffixes if needed."""
	entity_registry = er.async_get(hass)
	
	# Check if the proposed name conflicts with any existing entity
	base_name = proposed_name
	counter = 1
	
	while True:
		# Check if any entity has this name
		name_exists = False
		conflicting_entity = None
		is_own_entity = False
		
		for entity_id, entry in entity_registry.entities.items():
			if entity_id.startswith(f"{domain}.") and (
				(entry.name and entry.name.lower() == proposed_name.lower()) or
				(entry.original_name and entry.original_name.lower() == proposed_name.lower())
			):
				# Check if this conflicting entity is from our own integration
				if entry.platform == DOMAIN:
					# It's our own entity, don't treat as conflict
					is_own_entity = True
					_LOGGER.debug(f"Detected own entity with name '{proposed_name}': {entity_id}")
				else:
					name_exists = True
					conflicting_entity = entity_id
				break
		
		# Also check current states for entities that might not be in registry yet
		# But skip this check if we already found it's our own entity
		if not name_exists and not is_own_entity:
			for state in hass.states.async_all():
				if (state.entity_id.startswith(f"{domain}.") and 
					state.attributes.get("friendly_name", "").lower() == proposed_name.lower()):
					name_exists = True
					conflicting_entity = state.entity_id
					break
		
		if not name_exists:
			if counter > 1:
				_LOGGER.info(f"Entity name conflict resolved: using '{proposed_name}' instead of '{base_name}'")
			return proposed_name
		
		# Name exists, try with a suffix
		if counter == 2:  # Log only on first conflict detection
			_LOGGER.warning(f"Entity name conflict detected: '{base_name}' already exists (conflicting entity: {conflicting_entity}). Adding suffix.")
		
		counter += 1
		proposed_name = f"{base_name} ({counter})"

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
	"""Set up the sensor platform."""
	hass.data[DOMAIN][entry.entry_id]["async_add_entities"] = async_add_entities
	
	# Check if we need to recreate existing entities during reload
	options = entry.options
	
	# Only proceed if we have selected sensors configured
	selected_sensors = options.get("selected_power_sensors", [])
	if not selected_sensors:
		return
	
	# Find existing generated sensors
	entity_registry = er.async_get(hass)
	existing_entities = []
	
	# Look for entities with this integration's platform
	for entity_id, entity_entry in entity_registry.entities.items():
		if entity_entry.platform == DOMAIN and entity_entry.config_entry_id == entry.entry_id:
			existing_entities.append((entity_id, entity_entry.unique_id))
	
	# If we have existing entities, recreate them to ensure they're properly linked
	if existing_entities:
		_LOGGER.info(f"Found {len(existing_entities)} existing energy sensors to recreate during setup")
		
		# Get storage path
		from .const import STORAGE_FILE
		from pathlib import Path
		storage_path = Path(hass.config.path(STORAGE_FILE))
		
		# Group entities by base name
		entities_by_base = {}
		for entity_id, unique_id in existing_entities:
			# Extract base name from unique_id
			if "_daily_energy" in unique_id:
				base_name = unique_id.replace("_daily_energy", "")
				sensor_type = "daily"
			elif "_monthly_energy" in unique_id:
				base_name = unique_id.replace("_monthly_energy", "")
				sensor_type = "monthly"
			else:
				base_name = unique_id.replace("_energy", "")
				sensor_type = "main"
			
			if base_name not in entities_by_base:
				entities_by_base[base_name] = {}
			entities_by_base[base_name][sensor_type] = entity_id
		
		# Recreate entities
		entities_to_add = []
		
		for base_name, sensor_types in entities_by_base.items():
			# Determine source sensor from base name
			source_sensor = f"sensor.{base_name}_power"
			if source_sensor not in selected_sensors:
				# Try to find the actual source sensor from selected sensors
				for selected in selected_sensors:
					selected_base = selected.replace("sensor.", "").replace("_power", "")
					if selected_base == base_name:
						source_sensor = selected
						break
			
			# Check if source sensor still exists
			if hass.states.get(source_sensor) is None:
				_LOGGER.info(f"Source sensor {source_sensor} not yet available during startup for {base_name}, will create energy sensor anyway")
				# During startup, we'll proceed anyway - the sensor should handle unavailable source gracefully
			
			# Get device identifiers for proper device grouping
			device_identifiers = None
			source_entity = entity_registry.async_get(source_sensor)
			if source_entity and source_entity.device_id:
				device_registry = dr.async_get(hass)
				device = device_registry.async_get(source_entity.device_id)
				if device:
					device_identifiers = device.identifiers
			
			# Recreate main energy sensor if it exists
			if "main" in sensor_types:
				energy_sensor = EnergySensor(hass, base_name, source_sensor, storage_path, device_identifiers)
				entities_to_add.append(energy_sensor)
				_LOGGER.debug(f"Recreated main energy sensor for {base_name}")
			
			# Recreate daily sensor if it exists
			if "daily" in sensor_types:
				daily_sensor = DailyEnergySensor(hass, base_name, f"sensor.{base_name}_energy", storage_path, device_identifiers)
				entities_to_add.append(daily_sensor)
				_LOGGER.debug(f"Recreated daily energy sensor for {base_name}")
			
			# Recreate monthly sensor if it exists  
			if "monthly" in sensor_types:
				monthly_sensor = MonthlyEnergySensor(hass, base_name, f"sensor.{base_name}_energy", storage_path, device_identifiers)
				entities_to_add.append(monthly_sensor)
				_LOGGER.debug(f"Recreated monthly energy sensor for {base_name}")
		
		# Add all recreated entities
		if entities_to_add:
			async_add_entities(entities_to_add, True)  # True = update_before_add
			_LOGGER.info(f"Successfully recreated {len(entities_to_add)} energy sensors during setup")
	
	return

class EnergySensor(SensorEntity):
    """Custom sensor to calculate kWh from power (Watts)."""

    def __init__(self, hass, base_name, source_sensor, storage_path, device_identifiers=None):
        """Initialize the sensor."""
        self._hass = hass
        self._base_name = base_name
        self._source_sensor = source_sensor
        self._storage_path = storage_path
        
        # Initialize conversion factor - will be determined when state becomes available
        self._power_to_kw_factor = None
        
        # Get friendly name from the source sensor
        friendly_name = get_friendly_name(hass, source_sensor)
        
        # Generate unique entity name to avoid conflicts
        proposed_name = f"{friendly_name} Energy"
        unique_name = get_unique_entity_name(hass, proposed_name)
        
        # Generate entity attributes
        self._attr_unique_id = f"{base_name}_energy"
        self._attr_name = unique_name
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        
        # Set device info directly if provided, otherwise get from source sensor
        if device_identifiers:
            self._attr_device_info = DeviceInfo(identifiers=device_identifiers)
        else:
            # Get source sensor information to link to its device if possible
            entity_registry = er.async_get(hass)
            source_entity = entity_registry.async_get(source_sensor)
            
            # Set device info to match the source sensor's device
            if source_entity and source_entity.device_id:
                device_registry = dr.async_get(hass)
                device = device_registry.async_get(source_entity.device_id)
                if device:
                    # Use the exact same device_info as the source sensor
                    self._attr_device_info = DeviceInfo(identifiers=device.identifiers)
        
        self._state = 0.0
        self._last_power = None
        self._last_update = None
        self._min_calculation_interval = 1.0  # Minimum seconds between calculations
        self._storage_key = f"{base_name}_energy"
        self._interval_tracker = None
        self._calculating_energy = False  # Flag to prevent concurrent calculations
        # State will be loaded in async_added_to_hass

    def _get_power_conversion_factor(self, hass, source_sensor):
        """Determine the conversion factor from source power unit to kW."""
        try:
            # Get the source sensor state and attributes
            state = hass.states.get(source_sensor)
            if not state:
                # Default to Watts if we can't determine the unit
                _LOGGER.warning(f"UNIT DETECTION: Could not get state for {source_sensor}, assuming Watts")
                return 1000
                
            # Check unit of measurement
            unit = state.attributes.get("unit_of_measurement", "").strip()
            device_class = state.attributes.get("device_class", "")
            
            _LOGGER.warning(f"UNIT DETECTION: {source_sensor} | unit='{unit}' | device_class='{device_class}' | state={state.state}")
            
            # Normalise unit to lowercase for comparison
            unit_lower = unit.lower()
            
            if unit_lower in ["kw", "kilowatt", "kilowatts"]:
                # Source is already in kW, no conversion needed
                _LOGGER.warning(f"UNIT DETECTION: {source_sensor} detected as kW unit, using factor 1")
                return 1
            elif unit_lower in ["w", "watt", "watts"]:
                # Source is in Watts, need to divide by 1000 to get kW
                _LOGGER.warning(f"UNIT DETECTION: {source_sensor} detected as W unit, using factor 1000")
                return 1000
            else:
                # Unknown or missing unit, assume Watts for backwards compatibility
                _LOGGER.warning(f"UNIT DETECTION: {source_sensor} unknown unit '{unit}', assuming Watts (factor 1000)")
                return 1000
        except Exception as e:
            _LOGGER.error(f"Error determining power conversion factor for {source_sensor}: {e}")
            return 1000

    def _ensure_conversion_factor(self):
        """Ensure the power conversion factor is set."""
        if self._power_to_kw_factor is None:
            self._power_to_kw_factor = self._get_power_conversion_factor(self._hass, self._source_sensor)
            unit_name = "kW" if self._power_to_kw_factor == 1 else "W"
            _LOGGER.warning(f"CONVERSION FACTOR SET: {self._source_sensor} -> {self._attr_name} | Unit: {unit_name} | Factor: {self._power_to_kw_factor}")
        # Note: Factor is now set during _load_state() to enable data migration

    async def _load_state(self):
        """Load state from storage."""
        storage = await load_storage(self._storage_path)
        state_data = storage.get(self._storage_key, {})
        
        if isinstance(state_data, dict):
            self._state = state_data.get("value", 0.0)
            last_power = state_data.get("last_power")
            last_update = state_data.get("last_update")
            stored_conversion_factor = state_data.get("conversion_factor")
            
            # Determine current conversion factor
            current_conversion_factor = self._get_power_conversion_factor(self._hass, self._source_sensor)
            
            # Set the current conversion factor
            self._power_to_kw_factor = current_conversion_factor
            
            if last_power is not None:
                self._last_power = last_power
            
            if last_update:
                try:
                    parsed_dt = datetime.fromisoformat(last_update)
                    # If the datetime is timezone-naive, make it timezone-aware
                    if parsed_dt.tzinfo is None:
                        self._last_update = dt_util.as_utc(parsed_dt)
                    else:
                        self._last_update = parsed_dt
                except (ValueError, TypeError):
                    self._last_update = None
        else:
            # Legacy format where state_data is just a float
            legacy_value = float(state_data) if state_data else 0.0
            current_conversion_factor = self._get_power_conversion_factor(self._hass, self._source_sensor)
            
            self._state = legacy_value
            self._power_to_kw_factor = current_conversion_factor

    async def _save_state(self):
        """Save state to storage."""
        storage = await load_storage(self._storage_path)
        storage[self._storage_key] = {
            "value": self._state,
            "last_power": self._last_power,
            "last_update": self._last_update.isoformat() if self._last_update else None,
            "conversion_factor": self._power_to_kw_factor
        }
        await save_storage(self._storage_path, storage)

    async def async_added_to_hass(self):
        """Handle entity addition."""
        # Load state from storage first
        await self._load_state()
        
        # Track state changes to the power sensor
        async_track_state_change_event(
            self._hass, [self._source_sensor], self._handle_state_change
        )
        
        # Get sampling interval from options
        sample_interval = 60  # Default 60 seconds if not specified
        
        # Try to get the configured sample interval from the integration's options
        for entry_id, entry_data in self._hass.data[DOMAIN].items():
            if "options" in entry_data:
                sample_interval = entry_data["options"].get("sample_interval", 60)
                break
        
        _LOGGER.debug(f"Setting up energy calculation with {sample_interval} second interval for {self._attr_name}")
        
        # Initialise power tracking if not already set (e.g., on first startup or reload)
        if self._last_power is None or self._last_update is None:
            state = self._hass.states.get(self._source_sensor)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    power = float(state.state)
                except (ValueError, TypeError):
                    _LOGGER.warning(f"Unable to initialise {self._attr_name} - invalid power state (cannot convert to float): {state.state}")
                else:
                    # Successfully converted to float, now try to save state
                    try:
                        self._last_power = power
                        self._last_update = dt_util.utcnow()
                        await self._save_state()
                        # Ensure conversion factor is set for logging
                        self._ensure_conversion_factor()
                        unit_display = "kW" if self._power_to_kw_factor == 1 else "W"
                        _LOGGER.debug(f"Initialised {self._attr_name} with current power: {power}{unit_display}")
                    except Exception as e:
                        _LOGGER.error(f"Error saving state during initialization for {self._attr_name}: {e}", exc_info=True)
                        # Still set the values even if saving fails
                        self._last_power = power
                        self._last_update = dt_util.utcnow()
            else:
                # Source sensor not yet available - this is normal during startup
                _LOGGER.info(f"Source sensor {self._source_sensor} not yet available for {self._attr_name} - will initialise when available")
        
        # Set up regular sampling interval for reliable energy calculation
        self._interval_tracker = async_track_time_interval(
            self._hass,
            self._handle_interval_update,
            timedelta(seconds=sample_interval)
        )
        
        # Also set up a midnight update to ensure we get regular updates
        async_track_time_change(
            self._hass,
            self._handle_midnight_update,
            hour=0,
            minute=0,
            second=0
        )

    async def _handle_interval_update(self, now):
        """Handle regular interval updates."""
        # Prevent concurrent calculations
        if self._calculating_energy:
            _LOGGER.debug(f"Skipping interval update, calculation already in progress")
            return
            
        self._calculating_energy = True
        try:
            # Get current power value
            state = self._hass.states.get(self._source_sensor)
            if not state or state.state in ("unknown", "unavailable"):
                return
                
            try:
                power = float(state.state)
            except (ValueError, TypeError):
                _LOGGER.warning(f"Invalid power state (cannot convert to float): {state.state}")
                return
                
            # If this is the first time we're getting a valid power reading, initialise tracking
            if self._last_power is None or self._last_update is None:
                _LOGGER.info(f"Source sensor {self._source_sensor} state change detected, initialising tracking for {self._attr_name}")
                self._last_power = power
                self._last_update = now
                await self._save_state()
                self.async_write_ha_state()
                return
            
            if self._last_power is not None and self._last_update is not None:
                # Calculate time delta in hours since last update
                time_delta = (now - self._last_update).total_seconds()
                delta_hours = time_delta / 3600
                
                # Ensure conversion factor is set
                self._ensure_conversion_factor()
                
                # Safety check for conversion factor
                if not self._power_to_kw_factor or self._power_to_kw_factor <= 0:
                    _LOGGER.error(f"Invalid conversion factor {self._power_to_kw_factor} for {self._source_sensor}, skipping calculation")
                    return
                
                # Trapezoidal rule: average power * time (kWh)
                avg_power = (self._last_power + power) / 2
                energy_kwh = (avg_power * delta_hours) / self._power_to_kw_factor
                
                # Detailed debugging for factor of 10 issue
                _LOGGER.warning(f"DETAILED CALC DEBUG: {self._attr_name}")
                _LOGGER.warning(f"  Source sensor: {self._source_sensor}")
                _LOGGER.warning(f"  Current power: {power}")
                _LOGGER.warning(f"  Last power: {self._last_power}")
                _LOGGER.warning(f"  Average power: {avg_power}")
                _LOGGER.warning(f"  Time delta seconds: {time_delta}")
                _LOGGER.warning(f"  Time delta hours: {delta_hours}")
                _LOGGER.warning(f"  Conversion factor: {self._power_to_kw_factor}")
                _LOGGER.warning(f"  Energy calculation: ({avg_power} * {delta_hours}) / {self._power_to_kw_factor} = {energy_kwh}")
                
                # Ensure we're not adding negative energy
                if energy_kwh > 0:
                    self._state += energy_kwh
                    unit_display = "kW" if self._power_to_kw_factor == 1 else "W"
                    _LOGGER.info(f"ENERGY CALC: {self._attr_name} | Power: {power:.3f}{unit_display} | Avg: {avg_power:.3f}{unit_display} | Time: {delta_hours:.6f}h | Factor: {self._power_to_kw_factor} | Energy: {energy_kwh:.6f}kWh | Total: {self._state:.6f}kWh")
                else:
                    unit_display = "kW" if self._power_to_kw_factor == 1 else "W"
                    _LOGGER.debug(f"Interval update: No energy added (delta too small), avg power: {avg_power:.2f}{unit_display}, time: {delta_hours:.4f}h")
            else:
                _LOGGER.debug(f"Interval update: Skipping calculation - missing previous power/time data for {self._attr_name}")
            
            # Update values
            self._last_power = power
            self._last_update = now
            await self._save_state()
            self.async_write_ha_state()
                
        except Exception as e:
            _LOGGER.error(f"Unexpected error in interval update for {self._attr_name}: {e}", exc_info=True)
        finally:
            self._calculating_energy = False
            
    async def _handle_midnight_update(self, now):
        """Handle daily update at midnight."""
        # Prevent concurrent calculations
        if self._calculating_energy:
            _LOGGER.debug(f"Skipping midnight update, calculation already in progress")
            return
            
        self._calculating_energy = True
        try:
            # Save current state
            await self._save_state()
            
            # Force an immediate calculation based on the most recent power value
            state = self._hass.states.get(self._source_sensor)
            if state and state.state not in ("unknown", "unavailable") and self._last_power is not None and self._last_update is not None:
                try:
                    power = float(state.state)
                except (ValueError, TypeError):
                    _LOGGER.warning(f"Invalid power state at midnight (cannot convert to float): {state.state}")
                else:
                    # Successfully converted to float, now try calculations and state saving
                    try:
                        # Ensure conversion factor is set
                        self._ensure_conversion_factor()
                        
                        # Safety check for conversion factor
                        if not self._power_to_kw_factor or self._power_to_kw_factor <= 0:
                            _LOGGER.error(f"Invalid conversion factor {self._power_to_kw_factor} for {self._source_sensor}, skipping midnight calculation")
                            return
                        
                        # Calculate energy since last update
                        delta_hours = (now - self._last_update).total_seconds() / 3600
                        avg_power = (self._last_power + power) / 2
                        energy_kwh = (avg_power * delta_hours) / self._power_to_kw_factor
                        
                        if energy_kwh > 0:
                            self._state += energy_kwh
                            unit_display = "kW" if self._power_to_kw_factor == 1 else "W"
                            _LOGGER.info(f"MIDNIGHT CALC: {self._attr_name} | Power: {power:.3f}{unit_display} | Avg: {avg_power:.3f}{unit_display} | Time: {delta_hours:.6f}h | Factor: {self._power_to_kw_factor} | Energy: {energy_kwh:.6f}kWh | Total: {self._state:.6f}kWh")
                        
                        # Update values
                        self._last_power = power
                        self._last_update = now
                        await self._save_state()
                        self.async_write_ha_state()
                    except Exception as e:
                        _LOGGER.error(f"Error during midnight calculation for {self._attr_name}: {e}", exc_info=True)
        finally:
            self._calculating_energy = False

    async def _handle_state_change(self, event):
        """Update energy when power changes."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in ("unknown", "unavailable"):
            return

        now = dt_util.utcnow()
        try:
            power = float(new_state.state)
        except ValueError:
            _LOGGER.warning(f"Invalid power value: {new_state.state}")
            return

        # If this is the first time we're getting a valid power reading, initialise tracking
        if self._last_power is None or self._last_update is None:
            _LOGGER.info(f"Source sensor {self._source_sensor} state change detected, initialising tracking for {self._attr_name}")
            self._last_power = power
            self._last_update = now
            await self._save_state()
            self.async_write_ha_state()
            return

        # Only update tracking variables, do not perform energy calculations here
        # Energy calculations are handled exclusively by the interval timer to prevent double counting
        _LOGGER.debug(f"State change detected: {power}W - tracking only, calculation handled by interval timer")
        
        self._last_power = power
        self._last_update = now
        # Still update the state for UI feedback
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self):
        """Clean up resources when entity is removed."""
        # Cancel interval tracking
        if self._interval_tracker:
            self._interval_tracker()
            self._interval_tracker = None
        
        # Save state one last time
        await self._save_state()

    @property
    def native_value(self):
        """Return the current state."""
        return round(self._state, 4)  # More decimal places for accuracy

    @property
    def state(self):
        """Return the current state."""
        return round(self._state, 4)  # More decimal places for accuracy
        
    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        attrs = {}
        if self._last_power is not None:
            attrs["last_power"] = round(self._last_power, 3)
        if self._last_update is not None:
            attrs["last_update"] = self._last_update.isoformat()
        
        # Add conversion factor for debugging
        if self._power_to_kw_factor is not None:
            attrs["power_to_kw_factor"] = self._power_to_kw_factor
            attrs["source_unit"] = "kW" if self._power_to_kw_factor == 1 else "W"
        
        # Get interval from options
        sample_interval = 60  # Default 
        # Try to get the configured sample interval from the integration's options
        for entry_id, entry_data in self._hass.data[DOMAIN].items():
            if "options" in entry_data:
                sample_interval = entry_data["options"].get("sample_interval", 60)
                break
                
        attrs["sample_interval"] = sample_interval
        return attrs

class DailyEnergySensor(SensorEntity):
    """Custom sensor for daily energy tracking."""

    def __init__(self, hass, base_name, source_sensor, storage_path, device_identifiers=None):
        """Initialize the sensor."""
        self._hass = hass
        self._base_name = base_name
        self._source_sensor = source_sensor
        self._storage_path = storage_path
        
        # Get friendly name - for daily/monthly sensors, derive from base_name
        # since the source_sensor is the energy sensor, not the original power sensor
        # Try different possible patterns to find the original power sensor
        friendly_name = get_friendly_name_from_base(hass, base_name)
        
        # Generate unique entity name to avoid conflicts
        proposed_name = f"{friendly_name} Daily Energy"
        unique_name = get_unique_entity_name(hass, proposed_name)
        
        # Generate entity attributes
        self._attr_unique_id = f"{base_name}_daily_energy"
        self._attr_name = unique_name
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        
        # Set device info directly if provided, otherwise get from source sensor
        if device_identifiers:
            self._attr_device_info = DeviceInfo(identifiers=device_identifiers)
        else:
            # Get source sensor information to link to its device if possible
            entity_registry = er.async_get(hass)
            source_entity = entity_registry.async_get(source_sensor)
            
            # Set device info to match the source sensor's device
            if source_entity and source_entity.device_id:
                device_registry = dr.async_get(hass)
                device = device_registry.async_get(source_entity.device_id)
                if device:
                    # Use the exact same device_info as the source sensor
                    self._attr_device_info = DeviceInfo(identifiers=device.identifiers)
        
        self._state = 0.0
        self._last_energy = 0.0
        self._last_reset = None
        self._storage_key = f"{base_name}_daily_energy"
        # State will be loaded in async_added_to_hass

    async def _load_state(self):
        """Load state from storage."""
        storage = await load_storage(self._storage_path)
        state_data = storage.get(self._storage_key, {})
        self._state = state_data.get("value", 0.0)
        self._last_reset = state_data.get("last_reset", dt_util.utcnow().isoformat())
        self._last_energy = state_data.get("last_energy", 0.0)

    async def _save_state(self):
        """Save state to storage."""
        storage = await load_storage(self._storage_path)
        storage[self._storage_key] = {
            "value": self._state,
            "last_reset": self._last_reset,
            "last_energy": self._last_energy
        }
        await save_storage(self._storage_path, storage)

    async def async_added_to_hass(self):
        """Handle entity addition."""
        # Load state from storage first
        await self._load_state()
        
        # Track state changes to the power sensor
        async_track_state_change_event(
            self._hass, [self._source_sensor], self._handle_state_change
        )
        
        # Set up midnight reset
        async_track_time_change(
            self._hass,
            self._handle_midnight_reset,
            hour=0,
            minute=0,
            second=0
        )

    async def _handle_midnight_reset(self, now):
        """Reset at midnight."""
        _LOGGER.info(f"Midnight reset for {self._attr_name}")
        self._state = 0.0
        self._last_reset = now.isoformat()
        # Get current energy value to track from zero
        state = self._hass.states.get(self._source_sensor)
        if state and state.state not in ("unknown", "unavailable"):
            try:
                self._last_energy = float(state.state)
            except (ValueError, TypeError):
                self._last_energy = 0.0
        else:
            self._last_energy = 0.0
            
        await self._save_state()
        self.async_write_ha_state()

    async def _handle_state_change(self, event):
        """Update daily energy when source changes."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in ("unknown", "unavailable"):
            return

        try:
            energy = float(new_state.state)
        except ValueError:
            _LOGGER.warning(f"Invalid energy value: {new_state.state}")
            return

        # If this is the first time we're getting a valid energy reading, initialise tracking
        if self._last_energy == 0.0:
            _LOGGER.info(f"Source energy sensor {self._source_sensor} became available, initialising daily tracking for {self._attr_name}")
            self._last_energy = energy
            await self._save_state()
            self.async_write_ha_state()
            return

        # Calculate the energy change
        energy_change = max(0, energy - self._last_energy)
        self._state += energy_change
        self._last_energy = energy
        
        await self._save_state()
        self.async_write_ha_state()

    @property
    def native_value(self):
        """Return the current state."""
        return round(self._state, 2)

    @property
    def state(self):
        """Return the current state."""
        return round(self._state, 2)

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return {
            "last_reset": self._last_reset
        }

class MonthlyEnergySensor(SensorEntity):
    """Custom sensor for monthly energy tracking."""

    def __init__(self, hass, base_name, source_sensor, storage_path, device_identifiers=None):
        """Initialize the sensor."""
        self._hass = hass
        self._base_name = base_name
        self._source_sensor = source_sensor
        self._storage_path = storage_path
        
        # Get friendly name - for daily/monthly sensors, derive from base_name
        # since the source_sensor is the energy sensor, not the original power sensor
        # Try different possible patterns to find the original power sensor
        friendly_name = get_friendly_name_from_base(hass, base_name)
        
        # Generate unique entity name to avoid conflicts
        proposed_name = f"{friendly_name} Monthly Energy"
        unique_name = get_unique_entity_name(hass, proposed_name)
        
        # Generate entity attributes
        self._attr_unique_id = f"{base_name}_monthly_energy"
        self._attr_name = unique_name
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        
        # Set device info directly if provided, otherwise get from source sensor
        if device_identifiers:
            self._attr_device_info = DeviceInfo(identifiers=device_identifiers)
        else:
            # Get source sensor information to link to its device if possible
            entity_registry = er.async_get(hass)
            source_entity = entity_registry.async_get(source_sensor)
            
            # Set device info to match the source sensor's device
            if source_entity and source_entity.device_id:
                device_registry = dr.async_get(hass)
                device = device_registry.async_get(source_entity.device_id)
                if device:
                    # Use the exact same device_info as the source sensor
                    self._attr_device_info = DeviceInfo(identifiers=device.identifiers)
        
        self._state = 0.0
        self._last_energy = 0.0
        self._last_reset = None
        self._storage_key = f"{base_name}_monthly_energy"
        # State will be loaded in async_added_to_hass

    async def _load_state(self):
        """Load state from storage."""
        storage = await load_storage(self._storage_path)
        state_data = storage.get(self._storage_key, {})
        self._state = state_data.get("value", 0.0)
        self._last_reset = state_data.get("last_reset", dt_util.utcnow().isoformat())
        self._last_energy = state_data.get("last_energy", 0.0)

    async def _save_state(self):
        """Save state to storage."""
        storage = await load_storage(self._storage_path)
        storage[self._storage_key] = {
            "value": self._state,
            "last_reset": self._last_reset,
            "last_energy": self._last_energy
        }
        await save_storage(self._storage_path, storage)

    async def async_added_to_hass(self):
        """Handle entity addition."""
        # Load state from storage first
        await self._load_state()
        
        # Track state changes to the power sensor
        async_track_state_change_event(
            self._hass, [self._source_sensor], self._handle_state_change
        )
        
        # Set up first-of-month reset (check at midnight each day)
        async_track_time_change(
            self._hass,
            self._handle_month_reset,
            hour=0,
            minute=0,
            second=0
        )

    async def _handle_month_reset(self, now):
        """Reset at first day of month."""
        # Check if it's the first day of the month
        if now.day == 1:
            _LOGGER.info(f"Monthly reset for {self._attr_name}")
            self._state = 0.0
            self._last_reset = now.isoformat()
            
            # Get current energy value to track from zero
            state = self._hass.states.get(self._source_sensor)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    self._last_energy = float(state.state)
                except (ValueError, TypeError):
                    self._last_energy = 0.0
            else:
                self._last_energy = 0.0
                
            await self._save_state()
            self.async_write_ha_state()

    async def _handle_state_change(self, event):
        """Update monthly energy when source changes."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in ("unknown", "unavailable"):
            return

        try:
            energy = float(new_state.state)
        except ValueError:
            _LOGGER.warning(f"Invalid energy value: {new_state.state}")
            return

        # If this is the first time we're getting a valid energy reading, initialise tracking
        if self._last_energy == 0.0:
            _LOGGER.info(f"Source energy sensor {self._source_sensor} became available, initialising monthly tracking for {self._attr_name}")
            self._last_energy = energy
            await self._save_state()
            self.async_write_ha_state()
            return

        # Calculate the energy change
        energy_change = max(0, energy - self._last_energy)
        self._state += energy_change
        self._last_energy = energy
        
        await self._save_state()
        self.async_write_ha_state()

    @property
    def native_value(self):
        """Return the current state."""
        return round(self._state, 2)

    @property
    def state(self):
        """Return the current state."""
        return round(self._state, 2)
        
    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return {
            "last_reset": self._last_reset
        }
