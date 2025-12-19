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


_price = _load_module("energy_sensor_generator_price_adjustment", "price_adjustment.py")
compute_adjusted_value = _price.compute_adjusted_value
normalise_numeric_state = _price.normalise_numeric_state
is_price_attribute_key = _price.is_price_attribute_key
adjust_attribute_value = _price.adjust_attribute_value


def test_normalise_numeric_state_handles_unavailable():
	assert normalise_numeric_state("unknown") is None
	assert normalise_numeric_state("unavailable") is None
	assert normalise_numeric_state("") is None
	assert normalise_numeric_state(None) is None


def test_normalise_numeric_state_parses_numbers():
	assert normalise_numeric_state("1") == 1.0
	assert normalise_numeric_state(" 2.5 ") == 2.5
	assert normalise_numeric_state(3) == 3.0


def test_compute_adjusted_value_returns_none_when_source_missing():
	assert compute_adjusted_value("unknown", 1.2) is None
	assert compute_adjusted_value(None, 1.2) is None
	assert compute_adjusted_value("bad", 1.2) is None


def test_compute_adjusted_value_adds_amount():
	assert compute_adjusted_value("10", 0.25) == 10.25
	assert compute_adjusted_value(5.5, -0.5) == 5.0


def test_is_price_attribute_key_basic_cases():
	assert is_price_attribute_key("average") is True
	assert is_price_attribute_key("today") is True
	assert is_price_attribute_key("raw_today") is False
	assert is_price_attribute_key("price_percent_to_average") is False


def test_adjust_attribute_value_adjusts_lists_and_dicts():
	assert adjust_attribute_value([1.0, "2.0", "bad"], 0.5) == [1.5, 2.5, "bad"]
	assert adjust_attribute_value({"start": "x", "value": 1.0}, 0.25)["value"] == 1.25

