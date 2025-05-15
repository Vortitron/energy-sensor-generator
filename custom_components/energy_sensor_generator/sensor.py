import logging
from datetime import datetime, timedelta
from homeassistant.components.sensor import (
    SensorEntity, 
    SensorDeviceClass,
    SensorStateClass
)
from homeassistant.const import UnitOfEnergy
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import device_registry as dr
from .utils import load_storage, save_storage
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the sensor platform."""
    hass.data[DOMAIN][entry.entry_id]["async_add_entities"] = async_add_entities
    return

class EnergySensor(SensorEntity):
    """Custom sensor to calculate kWh from power (Watts)."""

    def __init__(self, hass, base_name, source_sensor, storage_path):
        """Initialize the sensor."""
        self._hass = hass
        self._base_name = base_name
        self._source_sensor = source_sensor
        self._storage_path = storage_path
        
        # Generate entity attributes
        self._attr_unique_id = f"{base_name}_energy"
        self._attr_name = f"{base_name} Energy"
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        
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
        self._load_state()

    def _load_state(self):
        """Load state from storage."""
        storage = load_storage(self._storage_path)
        state_data = storage.get(self._storage_key, {})
        if isinstance(state_data, dict):
            self._state = state_data.get("value", 0.0)
            last_power = state_data.get("last_power")
            last_update = state_data.get("last_update")
            
            if last_power is not None:
                self._last_power = float(last_power)
            
            if last_update is not None:
                try:
                    self._last_update = datetime.fromisoformat(last_update)
                except (ValueError, TypeError):
                    self._last_update = None
        else:
            # Legacy format where state_data is just a float
            self._state = float(state_data) if state_data else 0.0

    def _save_state(self):
        """Save state to storage."""
        storage = load_storage(self._storage_path)
        storage[self._storage_key] = {
            "value": self._state,
            "last_power": self._last_power,
            "last_update": self._last_update.isoformat() if self._last_update else None
        }
        save_storage(self._storage_path, storage)

    async def async_added_to_hass(self):
        """Handle entity addition."""
        # Track state changes to the power sensor
        async_track_state_change_event(
            self._hass, [self._source_sensor], self._handle_state_change
        )
        
        # Also set up a midnight update to ensure we get regular updates
        async_track_time_change(
            self._hass,
            self._handle_midnight_update,
            hour=0,
            minute=0,
            second=0
        )

    async def _handle_midnight_update(self, now):
        """Handle daily update at midnight."""
        # Save current state
        self._save_state()
        
        # If we have valid power, calculate for the last day
        if self._last_power is not None and self._last_update is not None:
            # Get current power value
            state = self._hass.states.get(self._source_sensor)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    power = float(state.state)
                    # Calculate energy since last update
                    delta_hours = (now - self._last_update).total_seconds() / 3600
                    avg_power = (self._last_power + power) / 2
                    energy_kwh = (avg_power * delta_hours) / 1000
                    self._state += energy_kwh
                    
                    # Update values
                    self._last_power = power
                    self._last_update = now
                    self._save_state()
                    self.async_write_ha_state()
                except (ValueError, TypeError):
                    _LOGGER.warning(f"Invalid power state: {state.state}")

    async def _handle_state_change(self, event):
        """Update energy when power changes."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in ("unknown", "unavailable"):
            return

        now = datetime.now()
        try:
            power = float(new_state.state)
        except ValueError:
            _LOGGER.warning(f"Invalid power value: {new_state.state}")
            return

        if self._last_power is not None and self._last_update is not None:
            # Check if enough time has passed since last calculation
            time_delta = (now - self._last_update).total_seconds()
            
            if time_delta >= self._min_calculation_interval:
                # Calculate time delta in hours
                delta_hours = time_delta / 3600
                # Trapezoidal rule: average power * time (kWh)
                avg_power = (self._last_power + power) / 2
                energy_kwh = (avg_power * delta_hours) / 1000
                
                # Ensure we're not adding negative energy
                if energy_kwh > 0:
                    self._state += energy_kwh
                    self._save_state()
                    _LOGGER.debug(f"Added {energy_kwh:.4f} kWh, avg power: {avg_power:.2f}W, time: {delta_hours:.4f}h")
            else:
                _LOGGER.debug(f"Skipping calculation, too soon ({time_delta:.2f}s < {self._min_calculation_interval}s)")

        self._last_power = power
        self._last_update = now
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
        attrs = {}
        if self._last_power is not None:
            attrs["last_power"] = round(self._last_power, 2)
        if self._last_update is not None:
            attrs["last_update"] = self._last_update.isoformat()
        return attrs
        
class DailyEnergySensor(SensorEntity):
    """Custom sensor for daily energy tracking."""

    def __init__(self, hass, base_name, source_sensor, storage_path):
        """Initialize the sensor."""
        self._hass = hass
        self._base_name = base_name
        self._source_sensor = source_sensor
        self._storage_path = storage_path
        
        # Generate entity attributes
        self._attr_unique_id = f"{base_name}_daily_energy"
        self._attr_name = f"{base_name} Daily Energy"
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        
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
        self._load_state()

    def _load_state(self):
        """Load state from storage."""
        storage = load_storage(self._storage_path)
        state_data = storage.get(self._storage_key, {})
        self._state = state_data.get("value", 0.0)
        self._last_reset = state_data.get("last_reset", datetime.now().isoformat())
        self._last_energy = state_data.get("last_energy", 0.0)

    def _save_state(self):
        """Save state to storage."""
        storage = load_storage(self._storage_path)
        storage[self._storage_key] = {
            "value": self._state,
            "last_reset": self._last_reset,
            "last_energy": self._last_energy
        }
        save_storage(self._storage_path, storage)

    async def async_added_to_hass(self):
        """Handle entity addition."""
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
            
        self._save_state()
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

        # Calculate the energy change
        energy_change = max(0, energy - self._last_energy)
        self._state += energy_change
        self._last_energy = energy
        
        self._save_state()
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

    def __init__(self, hass, base_name, source_sensor, storage_path):
        """Initialize the sensor."""
        self._hass = hass
        self._base_name = base_name
        self._source_sensor = source_sensor
        self._storage_path = storage_path
        
        # Generate entity attributes
        self._attr_unique_id = f"{base_name}_monthly_energy"
        self._attr_name = f"{base_name} Monthly Energy"
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        
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
        self._load_state()

    def _load_state(self):
        """Load state from storage."""
        storage = load_storage(self._storage_path)
        state_data = storage.get(self._storage_key, {})
        self._state = state_data.get("value", 0.0)
        self._last_reset = state_data.get("last_reset", datetime.now().isoformat())
        self._last_energy = state_data.get("last_energy", 0.0)

    def _save_state(self):
        """Save state to storage."""
        storage = load_storage(self._storage_path)
        storage[self._storage_key] = {
            "value": self._state,
            "last_reset": self._last_reset,
            "last_energy": self._last_energy
        }
        save_storage(self._storage_path, storage)

    async def async_added_to_hass(self):
        """Handle entity addition."""
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
                
            self._save_state()
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

        # Calculate the energy change
        energy_change = max(0, energy - self._last_energy)
        self._state += energy_change
        self._last_energy = energy
        
        self._save_state()
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
