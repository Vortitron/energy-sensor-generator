"""Period (daily/weekly/monthly/annual) energy sensors.

Each one tracks the increase of a generated main energy sensor and resets at
its period boundary, replacing the utility_meter helper.
"""
import logging

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import (
	SensorEntity,
	SensorDeviceClass,
	SensorStateClass
)
from homeassistant.const import UnitOfEnergy
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.restore_state import RestoreEntity

from .entity_helpers import (
	debug_log,
	get_friendly_name_from_base,
	get_unique_entity_name,
	persist_storage_key,
)
from .utils import StorageManager

_LOGGER = logging.getLogger(__name__)


class PeriodEnergySensor(SensorEntity, RestoreEntity):
	"""Base class for period energy sensors (daily/weekly/monthly/annual).

	Tracks the increase of a generated main energy sensor and resets at the
	period boundary. Subclasses define the period label, unique_id suffix and
	reset condition.
	"""

	PERIOD_LABEL = ""  # e.g. "Daily" - used in the friendly name
	PERIOD_SUFFIX = ""  # e.g. "daily" - used in unique_id and storage key

	def __init__(self, hass, base_name, source_sensor, storage_path, device_identifiers=None):
		"""Initialize the sensor."""
		assert self.PERIOD_LABEL and self.PERIOD_SUFFIX, "Subclasses must define period metadata"
		self._hass = hass
		self._base_name = base_name
		self._source_sensor = source_sensor
		# Prefer StorageManager if provided
		self._storage_manager: StorageManager | None = storage_path if isinstance(storage_path, StorageManager) else None
		self._storage_path = storage_path

		# Derive friendly name from base_name since the source_sensor is the
		# generated energy sensor, not the original power sensor.
		friendly_name = get_friendly_name_from_base(hass, base_name)
		proposed_name = f"{friendly_name} {self.PERIOD_LABEL} Energy"
		self._attr_name = get_unique_entity_name(hass, proposed_name)
		self._attr_unique_id = f"{base_name}_{self.PERIOD_SUFFIX}_energy"
		self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
		self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
		self._attr_device_class = SensorDeviceClass.ENERGY
		self._attr_state_class = SensorStateClass.TOTAL_INCREASING
		self._attr_entity_registry_enabled_default = True

		# Set device info directly if provided, otherwise link to the source sensor's device
		if device_identifiers:
			self._attr_device_info = DeviceInfo(identifiers=device_identifiers)
		else:
			entity_registry = er.async_get(hass)
			source_entity = entity_registry.async_get(source_sensor)
			if source_entity and source_entity.device_id:
				device_registry = dr.async_get(hass)
				device = device_registry.async_get(source_entity.device_id)
				if device:
					self._attr_device_info = DeviceInfo(identifiers=device.identifiers)

		self._state = 0.0
		self._last_energy = 0.0
		self._last_reset = None
		self._storage_key = f"{base_name}_{self.PERIOD_SUFFIX}_energy"
		self._unsub_state = None
		self._unsub_reset = None
		# State will be loaded in async_added_to_hass

	def _should_reset(self, now) -> bool:
		"""Return True when the period boundary has been reached (checked at midnight)."""
		raise NotImplementedError

	async def _load_state(self):
		"""Load state from storage."""
		if self._storage_manager:
			storage = await self._storage_manager.async_load()
		else:
			from homeassistant.helpers import storage as ha_storage
			store = ha_storage.Store(self._hass, version=1, key="energy_sensor_generator")
			storage = await store.async_load() or {}
		state_data = storage.get(self._storage_key, {})
		self._state = state_data.get("value", 0.0)
		self._last_reset = state_data.get("last_reset", dt_util.utcnow().isoformat())
		self._last_energy = state_data.get("last_energy", 0.0)

	async def _save_state(self):
		"""Save state to storage."""
		await persist_storage_key(self._hass, self._storage_manager, self._storage_key, {
			"value": self._state,
			"last_reset": self._last_reset,
			"last_energy": self._last_energy
		}, self._attr_name)

	async def async_added_to_hass(self):
		"""Handle entity addition."""
		# Load state from storage first; fall back to the HA restore cache
		await self._load_state()
		if self._state == 0.0:
			last = await self.async_get_last_state()
			try:
				if last and last.state not in ("unknown", "unavailable", None):
					self._state = float(last.state)
			except (ValueError, TypeError):
				pass

		# Track state changes to the source energy sensor
		self._unsub_state = async_track_state_change_event(
			self._hass, [self._source_sensor], self._handle_state_change
		)

		# Check the period boundary at midnight each day
		self._unsub_reset = async_track_time_change(
			self._hass,
			self._handle_period_reset,
			hour=0,
			minute=0,
			second=0
		)
		self.safe_write_ha_state()

	async def _handle_period_reset(self, now):
		"""Reset the counter when the period boundary is reached."""
		if not self._should_reset(now):
			return
		_LOGGER.info(f"{self.PERIOD_LABEL} reset for {self._attr_name}")
		self._state = 0.0
		self._last_reset = now.isoformat()
		# Re-anchor tracking to the source sensor's current value
		state = self._hass.states.get(self._source_sensor)
		if state and state.state not in ("unknown", "unavailable"):
			try:
				self._last_energy = float(state.state)
			except (ValueError, TypeError):
				self._last_energy = 0.0
		else:
			self._last_energy = 0.0
		await self._save_state()
		self.safe_write_ha_state()

	async def async_will_remove_from_hass(self):
		"""Clean up resources when entity is removed."""
		for unsub in (self._unsub_state, self._unsub_reset):
			try:
				if unsub:
					unsub()
			except Exception:
				pass
		self._unsub_state = None
		self._unsub_reset = None

	async def _handle_state_change(self, event):
		"""Accumulate the source energy sensor's increase."""
		new_state = event.data.get("new_state")
		if new_state is None or new_state.state in ("unknown", "unavailable"):
			return
		try:
			energy = float(new_state.state)
		except ValueError:
			_LOGGER.warning(f"Invalid energy value: {new_state.state}")
			return

		# If this is the first valid reading, initialise tracking
		if self._last_energy == 0.0:
			debug_log(self.hass, f"Source energy sensor {self._source_sensor} became available, initialising {self.PERIOD_SUFFIX} tracking for {self._attr_name}")
			self._last_energy = energy
			await self._save_state()
			self.safe_write_ha_state()
			return

		# Only count increases; a decrease means the source was corrected/reset
		energy_change = max(0, energy - self._last_energy)
		self._state += energy_change
		self._last_energy = energy
		await self._save_state()
		self.safe_write_ha_state()

	@property
	def native_value(self):
		"""Return the current state."""
		return round(self._state, 4)  # Match main energy sensor precision

	@property
	def state(self):
		"""Return the current state."""
		return round(self._state, 4)  # Match main energy sensor precision

	@property
	def unit_of_measurement(self):
		"""Return the unit of measurement, ensuring it's always kWh."""
		return UnitOfEnergy.KILO_WATT_HOUR

	@property
	def native_unit_of_measurement(self):
		"""Return the native unit of measurement, ensuring it's always kWh."""
		return UnitOfEnergy.KILO_WATT_HOUR

	@property
	def extra_state_attributes(self):
		"""Return the state attributes."""
		return {
			"last_reset": self._last_reset
		}

	def safe_write_ha_state(self):
		"""Safely write HA state with error handling and unit verification."""
		try:
			if not getattr(self, '_attr_unit_of_measurement', None):
				self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
				_LOGGER.warning(f"Unit of measurement was missing for {self._attr_name}, restored to kWh")
			if self._attr_unit_of_measurement != UnitOfEnergy.KILO_WATT_HOUR:
				_LOGGER.warning(f"Unit of measurement was incorrect for {self._attr_name} ({self._attr_unit_of_measurement}), correcting to kWh")
				self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
			self.async_write_ha_state()
		except Exception as e:
			_LOGGER.error(f"Error writing HA state for {self._attr_name}: {e}", exc_info=True)


class DailyEnergySensor(PeriodEnergySensor):
	"""Daily energy tracking; resets at midnight."""

	PERIOD_LABEL = "Daily"
	PERIOD_SUFFIX = "daily"

	def _should_reset(self, now) -> bool:
		return True


class MonthlyEnergySensor(PeriodEnergySensor):
	"""Monthly energy tracking; resets on the first day of the month."""

	PERIOD_LABEL = "Monthly"
	PERIOD_SUFFIX = "monthly"

	def _should_reset(self, now) -> bool:
		return now.day == 1


class WeeklyEnergySensor(PeriodEnergySensor):
	"""Weekly energy tracking (ISO week); resets on Monday."""

	PERIOD_LABEL = "Weekly"
	PERIOD_SUFFIX = "weekly"

	def _should_reset(self, now) -> bool:
		return now.weekday() == 0


class AnnualEnergySensor(PeriodEnergySensor):
	"""Annual energy tracking; resets on 1 January."""

	PERIOD_LABEL = "Annual"
	PERIOD_SUFFIX = "annual"

	def _should_reset(self, now) -> bool:
		return now.month == 1 and now.day == 1
