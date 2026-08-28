from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "energy_sensor_generator"


def _load_module(name: str, relative_path: str):
	module_path = BASE_DIR / relative_path
	spec = spec_from_file_location(name, module_path)
	module = module_from_spec(spec)
	assert spec and spec.loader
	spec.loader.exec_module(module)
	return module


_picker = _load_module("energy_sensor_generator_sensor_picker", "sensor_picker.py")
short_sensor_label = _picker.short_sensor_label
uniquify_labels = _picker.uniquify_labels
grouped_selector_options = _picker.grouped_selector_options
options_overview = _picker.options_overview
period_flags_from_selection = _picker.period_flags_from_selection
period_selection_from_flags = _picker.period_selection_from_flags
merge_saved_options = _picker.merge_saved_options


def test_short_label_strips_device_prefix_and_entity_id():
	label = short_sensor_label(
		"sensor.smart_plug_power",
		friendly_name="smart plug 8 - sensor.smart_plug_power",
		device_name="smart plug 8",
	)
	assert "sensor." not in label
	assert "—" not in label
	assert label.lower() in {"power", "smart plug power"}


def test_short_label_uses_pretty_object_id_when_friendly_name_is_entity_id():
	label = short_sensor_label(
		"sensor.hot_water_power",
		friendly_name="sensor.hot_water_power",
		device_name="hot water",
	)
	assert "sensor." not in label
	assert label.lower() in {"power", "hot water power", "hot_water_power"}


def test_short_label_keeps_phase_names_under_a_device():
	label = short_sensor_label(
		"sensor.p1ib_l2_power",
		friendly_name="L2 Power",
		device_name="P1IB_a0b7655105e0",
	)
	assert label == "L2 Power"


def test_uniquify_labels_only_suffixes_collisions():
	result = uniquify_labels({
		"sensor.one_power": "Power",
		"sensor.two_power": "Power",
		"sensor.unique": "Hot water",
	})
	assert result["sensor.unique"] == "Hot water"
	assert "one_power" in result["sensor.one_power"]
	assert "two_power" in result["sensor.two_power"]


def test_grouped_options_flatten_single_sensor_devices():
	options = grouped_selector_options([
		("sensor.plug_8_power", "Power", "smart plug 8"),
		("sensor.p1ib_l1", "L1", "P1IB"),
		("sensor.p1ib_l2", "L2", "P1IB"),
		("sensor.orphan_power", "Orphan", None),
	])
	flat_labels = [item.get("label") for item in options]
	assert "smart plug 8" in flat_labels

	p1ib = next(item for item in options if item.get("label") == "P1IB")
	assert "options" in p1ib
	assert {entry["label"] for entry in p1ib["options"]} == {"L1", "L2"}

	other = next(item for item in options if item.get("label") == "Other sensors")
	assert other["options"][0]["value"] == "sensor.orphan_power"


def test_options_overview_is_a_single_compact_line():
	text = options_overview(12, 64, 3, 1)
	assert "\n" not in text
	assert "12 of 64 power sensors selected" in text
	assert "3 constant loads" in text
	assert "1 price add-on" in text


def test_period_flags_round_trip():
	flags = period_flags_from_selection(["daily", "annual"])
	assert flags["create_daily_sensors"] is True
	assert flags["create_weekly_sensors"] is False
	assert flags["create_monthly_sensors"] is False
	assert flags["create_annual_sensors"] is True
	assert period_selection_from_flags(flags) == ["daily", "annual"]


def test_merge_saved_options_drops_ui_keys_and_keeps_advanced():
	saved = merge_saved_options(
		{
			"selected_power_sensors": ["sensor.a"],
			"sample_interval": 30,
			"stat_initial_lookback_minutes": 45,
			"max_energy_per_hour": 12,
			"debug_logging": True,
		},
		{
			"selected_power_sensors": ["sensor.a", "sensor.b"],
			"show_advanced": True,
			"configure_constant_devices": True,
			"custom_power_sensor": "sensor.extra",
			"period_sensors": ["daily"],
		},
	)
	assert saved["selected_power_sensors"] == ["sensor.a", "sensor.b"]
	assert saved["sample_interval"] == 30
	assert saved["stat_initial_lookback_minutes"] == 45
	assert saved["max_energy_per_hour"] == 12
	assert saved["debug_logging"] is True
	assert "show_advanced" not in saved
	assert "custom_power_sensor" not in saved
	assert "period_sensors" not in saved
