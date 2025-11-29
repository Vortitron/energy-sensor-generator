"""Device-level helper utilities for Energy Sensor Generator."""

from typing import Callable, Dict, List, Tuple

try:
	from .const import DOMAIN
except ImportError:
	# Allow the helper to be imported in isolation during unit tests
	DOMAIN = "energy_sensor_generator"


def has_external_energy_sensors(
	device_id: str | None,
	device_energy_sensors: Dict[str, List[str]],
	entity_lookup: Callable[[str], object | None],
) -> Tuple[bool, List[str]]:
	"""Return whether the device already owns energy sensors from other integrations."""
	if not device_id or device_id not in device_energy_sensors:
		return False, []

	existing_sensors = device_energy_sensors.get(device_id, [])
	for entity_id in existing_sensors:
		entry = entity_lookup(entity_id)
		if entry and getattr(entry, "platform", None) != DOMAIN:
			return True, existing_sensors
	return False, existing_sensors

