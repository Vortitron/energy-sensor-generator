"""Export / import stay inside their backup folder and are for administrators only.

HACS review (hacs/default#11985): the services took a caller-supplied path,
and hass.config.path() is a plain join, so an absolute path or "../" left the
config directory, and any user could call them.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_sensor_generator import BACKUP_FOLDER, backup_path
from custom_components.energy_sensor_generator.const import DOMAIN
from custom_components.energy_sensor_generator.naming import SUBENTRY_POWER_SENSOR, subentry_unique_id

SOURCE = "sensor.kettle_power"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
	yield


@pytest.fixture(autouse=True)
def own_config_dir(hass: HomeAssistant, tmp_path):
	"""Each test writes its backups into its own config directory."""
	config_dir = tmp_path / "config"
	config_dir.mkdir()
	hass.config.config_dir = str(config_dir)
	yield


@pytest.fixture
def expected_lingering_timers() -> bool:
	return True


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
	hass.states.async_set(SOURCE, "0", {"unit_of_measurement": "W", "device_class": "power", "friendly_name": "Kettle Power"})
	entry = MockConfigEntry(
		domain=DOMAIN, unique_id=DOMAIN, version=2, data={}, options={},
		subentries_data=[{
			"subentry_type": SUBENTRY_POWER_SENSOR, "title": "Kettle",
			"unique_id": subentry_unique_id(SUBENTRY_POWER_SENSOR, SOURCE),
			"data": {"source_entity_id": SOURCE, "periods": []},
		}],
	)
	entry.add_to_hass(hass)
	assert await hass.config_entries.async_setup(entry.entry_id)
	await hass.async_block_till_done()
	return entry


@pytest.mark.parametrize(
	"name",
	[
		"/etc/passwd",
		"/config/configuration.yaml",
		"../configuration.yaml",
		"../../etc/passwd",
		"backups/energy.json",
		"..json",
		"energy.yaml",
		".hidden.json",
	],
)
async def test_paths_outside_the_backup_folder_are_refused(hass: HomeAssistant, name: str) -> None:
	await _setup(hass)
	with pytest.raises(ServiceValidationError):
		await hass.services.async_call(DOMAIN, "export_energy_data", {"target_path": name}, blocking=True)
	with pytest.raises(ServiceValidationError):
		await hass.services.async_call(DOMAIN, "import_energy_data", {"source_path": name}, blocking=True)
	# Nothing was written outside the backup folder
	assert not (Path(hass.config.path()) / "configuration.yaml").exists()


async def test_symlink_in_the_backup_folder_is_refused(hass: HomeAssistant, tmp_path) -> None:
	await _setup(hass)
	folder = Path(hass.config.path(BACKUP_FOLDER))
	folder.mkdir(exist_ok=True)
	outside = tmp_path / "outside.json"
	outside.write_text("{}")
	(folder / "link.json").symlink_to(outside)
	with pytest.raises(ServiceValidationError):
		await hass.services.async_call(DOMAIN, "export_energy_data", {"target_path": "link.json"}, blocking=True)
	assert outside.read_text() == "{}"


async def test_export_and_import_round_trip_in_the_backup_folder(hass: HomeAssistant) -> None:
	entry = await _setup(hass)
	await hass.services.async_call(DOMAIN, "adjust_energy", {"entity_id": "sensor.kettle_energy", "set_to_value": 7.5}, blocking=True)
	await hass.services.async_call(DOMAIN, "export_energy_data", {"target_path": "energy_backup.json"}, blocking=True)

	written = Path(hass.config.path(BACKUP_FOLDER)) / "energy_backup.json"
	assert written.is_file()
	assert json.loads(written.read_text())["kettle_energy"]["value"] == pytest.approx(7.5)

	await hass.services.async_call(DOMAIN, "adjust_energy", {"entity_id": "sensor.kettle_energy", "set_to_value": 1.0}, blocking=True)
	await hass.services.async_call(DOMAIN, "import_energy_data", {"source_path": "energy_backup.json"}, blocking=True)
	await hass.async_block_till_done()
	assert float(hass.states.get("sensor.kettle_energy").state) == pytest.approx(7.5)



async def test_default_export_name_lands_in_the_backup_folder(hass: HomeAssistant) -> None:
	await _setup(hass)
	await hass.services.async_call(DOMAIN, "export_energy_data", {}, blocking=True)
	files = list(Path(hass.config.path(BACKUP_FOLDER)).glob("energy_backup_*.json"))
	assert len(files) == 1


@pytest.mark.parametrize(
	("service", "data"),
	[
		("export_energy_data", {"target_path": "energy_backup.json"}),
		("import_energy_data", {"source_path": "energy_backup.json"}),
		("reset_energy_sensors", {"reset_to_zero": True}),
		("adjust_energy", {"entity_id": "sensor.kettle_energy", "set_to_value": 1}),
		("migrate_entity_ids", {}),
		("reset_statistical_tracking", {}),
		("copy_from_previous_hour", {}),
	],
)
async def test_data_changing_services_are_for_admins(hass: HomeAssistant, hass_read_only_user, service, data) -> None:
	await _setup(hass)
	with pytest.raises(Unauthorized):
		await hass.services.async_call(
			DOMAIN, service, data, blocking=True, context=Context(user_id=hass_read_only_user.id)
		)


async def test_admins_and_automations_can_still_export(hass: HomeAssistant, hass_admin_user) -> None:
	await _setup(hass)
	await hass.services.async_call(
		DOMAIN, "export_energy_data", {"target_path": "by_admin.json"}, blocking=True,
		context=Context(user_id=hass_admin_user.id),
	)
	# Automations and scripts run without a user
	await hass.services.async_call(DOMAIN, "export_energy_data", {"target_path": "by_automation.json"}, blocking=True)
	folder = Path(hass.config.path(BACKUP_FOLDER))
	assert (folder / "by_admin.json").is_file() and (folder / "by_automation.json").is_file()


async def test_read_only_services_stay_open(hass: HomeAssistant, hass_read_only_user) -> None:
	await _setup(hass)
	await hass.services.async_call(
		DOMAIN, "list_sensors", {}, blocking=True, context=Context(user_id=hass_read_only_user.id)
	)


async def test_import_needs_a_file_name(hass: HomeAssistant) -> None:
	await _setup(hass)
	with pytest.raises(ServiceValidationError):
		await hass.services.async_call(DOMAIN, "import_energy_data", {"source_path": ""}, blocking=True)


def test_backup_path_accepts_plain_names(hass: HomeAssistant) -> None:
	path = backup_path(hass, "energy_backup_20261025.json")
	assert path.parent == Path(hass.config.path(BACKUP_FOLDER)).resolve()
	assert path.name == "energy_backup_20261025.json"
