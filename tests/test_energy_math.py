"""Unit tests for the pure energy integration maths."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

# Load the module directly by path (like the other tests) so the package
# __init__, which imports Home Assistant, is not executed.
BASE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "energy_sensor_generator"
_spec = spec_from_file_location("energy_sensor_generator_energy_math", BASE_DIR / "energy_math.py")
_energy_math = module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(_energy_math)

MAX_SEGMENT_HOURS = _energy_math.MAX_SEGMENT_HOURS
held_power_energy_kwh = _energy_math.held_power_energy_kwh
left_riemann_energy = _energy_math.left_riemann_energy
trapezoid_energy_kwh = _energy_math.trapezoid_energy_kwh
conversion_factor_from_unit = _energy_math.conversion_factor_from_unit
point_sampling_window_energy_kwh = _energy_math.point_sampling_window_energy_kwh

T0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _ts(seconds: float) -> datetime:
	return T0 + timedelta(seconds=seconds)


class TestHeldPowerEnergy:
	def test_one_kw_for_one_hour_is_one_kwh(self):
		assert held_power_energy_kwh(1000.0, 3600.0, 1000.0) == pytest.approx(1.0)

	def test_kw_source_uses_factor_one(self):
		assert held_power_energy_kwh(2.5, 3600.0, 1.0) == pytest.approx(2.5)

	def test_zero_or_negative_duration_gives_zero(self):
		assert held_power_energy_kwh(1000.0, 0.0, 1000.0) == 0.0
		assert held_power_energy_kwh(1000.0, -5.0, 1000.0) == 0.0

	def test_invalid_conversion_factor_asserts(self):
		with pytest.raises(AssertionError):
			held_power_energy_kwh(1000.0, 60.0, 0.0)


class TestTrapezoidEnergy:
	def test_average_of_endpoints(self):
		# 0 W ramping to 2000 W over an hour averages 1000 W -> 1 kWh
		assert trapezoid_energy_kwh(0.0, 2000.0, 3600.0, 1000.0) == pytest.approx(1.0)

	def test_flat_power_matches_held_power(self):
		assert trapezoid_energy_kwh(500.0, 500.0, 1800.0, 1000.0) == pytest.approx(
			held_power_energy_kwh(500.0, 1800.0, 1000.0)
		)

	def test_zero_duration_gives_zero(self):
		assert trapezoid_energy_kwh(100.0, 200.0, 0.0, 1000.0) == 0.0


class TestLeftRiemannEnergy:
	def test_constant_power_full_window(self):
		# 1000 W held over a 10-minute window = 1/6 kWh
		samples = [(1000.0, _ts(0)), (1000.0, _ts(300))]
		result = left_riemann_energy(samples, _ts(600), 1000.0)
		assert result["total_energy"] == pytest.approx(1000.0 * (600 / 3600) / 1000.0)
		assert result["segments"] == 2

	def test_final_segment_is_included(self):
		"""Regression test: the slice between the last sample and end_time
		must be counted, otherwise every window under-reads."""
		samples = [(1200.0, _ts(0))]
		result = left_riemann_energy(samples, _ts(3600), 1000.0)
		assert result["total_energy"] == pytest.approx(1.2)
		assert result["segments"] == 1

	def test_windows_tile_without_loss(self):
		"""Two consecutive windows must sum to the same energy as one big window."""
		samples = [
			(500.0, _ts(0)),
			(1500.0, _ts(600)),
			(1000.0, _ts(1200)),
			(2000.0, _ts(1800)),
		]
		whole = left_riemann_energy(samples, _ts(2400), 1000.0)["total_energy"]
		first = left_riemann_energy(samples[:2], _ts(1200), 1000.0)["total_energy"]
		second = left_riemann_energy(samples[2:], _ts(2400), 1000.0)["total_energy"]
		assert first + second == pytest.approx(whole)

	def test_unsorted_samples_are_sorted(self):
		samples = [(1000.0, _ts(300)), (1000.0, _ts(0))]
		result = left_riemann_energy(samples, _ts(600), 1000.0)
		assert result["total_energy"] == pytest.approx(1000.0 * (600 / 3600) / 1000.0)

	def test_left_riemann_uses_previous_power(self):
		# Power jumps to 9999 at the very end; the left Riemann sum should
		# value the whole window at the earlier reading.
		samples = [(100.0, _ts(0)), (9999.0, _ts(3600))]
		result = left_riemann_energy(samples, _ts(3600), 1000.0)
		assert result["total_energy"] == pytest.approx(0.1)

	def test_long_gap_segment_is_skipped(self):
		# A segment longer than MAX_SEGMENT_HOURS must not be bridged
		gap_seconds = (MAX_SEGMENT_HOURS + 1) * 3600
		samples = [(1000.0, _ts(0)), (1000.0, _ts(gap_seconds))]
		result = left_riemann_energy(samples, _ts(gap_seconds + 600), 1000.0)
		# Only the final 600 s segment counts
		assert result["total_energy"] == pytest.approx(1000.0 * (600 / 3600) / 1000.0)

	def test_empty_samples(self):
		result = left_riemann_energy([], _ts(600), 1000.0)
		assert result["total_energy"] == 0.0
		assert result["segments"] == 0
		assert result["min_power"] == 0.0

	def test_negative_total_clamped_to_zero(self):
		# Negative power readings cannot drive the window total below zero
		samples = [(-500.0, _ts(0))]
		result = left_riemann_energy(samples, _ts(600), 1000.0)
		assert result["total_energy"] == 0.0

	def test_diagnostics(self):
		samples = [(200.0, _ts(0)), (800.0, _ts(1800))]
		result = left_riemann_energy(samples, _ts(3600), 1000.0)
		assert result["max_power"] == 800.0
		assert result["min_power"] == 200.0
		# Each power level held for half the window
		assert result["avg_power"] == pytest.approx(500.0)

	def test_kw_source(self):
		samples = [(1.5, _ts(0))]
		result = left_riemann_energy(samples, _ts(3600), 1.0)
		assert result["total_energy"] == pytest.approx(1.5)


class TestConversionFactorFromUnit:
	def test_kw_units(self):
		assert conversion_factor_from_unit("kW") == 1
		assert conversion_factor_from_unit("kilowatt") == 1

	def test_watt_and_unknown_assume_watts(self):
		assert conversion_factor_from_unit("W") == 1000
		assert conversion_factor_from_unit("") == 1000
		assert conversion_factor_from_unit(None) == 1000
		assert conversion_factor_from_unit("VA") == 1000


class TestPointSamplingWindow:
	def test_pending_plus_tail_is_left_riemann(self):
		# 500 W for 30 s already pending, plus 500 W held for another 30 s
		pending = held_power_energy_kwh(500.0, 30.0, 1000.0)
		result = point_sampling_window_energy_kwh(pending, 500.0, 30.0, 1000.0, 600.0)
		assert result == pytest.approx(held_power_energy_kwh(500.0, 60.0, 1000.0))

	def test_state_change_during_interval_does_not_double_count(self):
		"""If a state change already consumed t0→t1 into pending, the tail
		must only cover t1→now — not the original full window."""
		pending = held_power_energy_kwh(1000.0, 20.0, 1000.0)
		# Remaining 40 s at the new power
		result = point_sampling_window_energy_kwh(pending, 500.0, 40.0, 1000.0, 600.0)
		expected = (
			held_power_energy_kwh(1000.0, 20.0, 1000.0)
			+ held_power_energy_kwh(500.0, 40.0, 1000.0)
		)
		assert result == pytest.approx(expected)
		# Using the stale full-window duration would over-read
		stale = point_sampling_window_energy_kwh(pending, 500.0, 60.0, 1000.0, 600.0)
		assert stale > expected

	def test_gap_returns_none(self):
		assert point_sampling_window_energy_kwh(0.0, 1000.0, 601.0, 1000.0, 600.0) is None

	def test_missing_last_power_returns_none(self):
		assert point_sampling_window_energy_kwh(0.1, None, 60.0, 1000.0, 600.0) is None

	def test_kw_source(self):
		result = point_sampling_window_energy_kwh(0.0, 2.0, 3600.0, 1.0, 7200.0)
		assert result == pytest.approx(2.0)
