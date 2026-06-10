"""Pure energy integration maths, free of Home Assistant imports so it can be unit tested.

All functions work on plain Python values. Power readings are expressed in the
source sensor's native unit; ``conversion_factor`` converts that unit to kW
(1 for kW sources, 1000 for W sources).
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Mapping, Sequence, Tuple

SECONDS_PER_HOUR = 3600.0

# Segments longer than this are treated as data gaps and skipped rather than
# bridged, so a stale reading cannot fabricate hours of phantom energy.
MAX_SEGMENT_HOURS = 6.0

# Statistical windows shorter than this are unreliable; callers should wait
# for the window to grow instead of calculating.
MIN_STATISTICAL_WINDOW_SECONDS = 10.0


def held_power_energy_kwh(
	power: float,
	delta_seconds: float,
	conversion_factor: float,
) -> float:
	"""Energy in kWh assuming ``power`` was held constant for ``delta_seconds``.

	This is the left Riemann assumption used throughout the integration: a
	sensor reports a new value only when the power changes, so the previous
	reading is correct for the whole interval up to the change.
	"""
	assert conversion_factor > 0, "conversion_factor must be positive"
	if delta_seconds <= 0:
		return 0.0
	return (power * (delta_seconds / SECONDS_PER_HOUR)) / conversion_factor


def trapezoid_energy_kwh(
	power_a: float,
	power_b: float,
	delta_seconds: float,
	conversion_factor: float,
) -> float:
	"""Energy of one trapezoidal segment in kWh.

	Used by point sampling, where power between two readings is assumed to
	change linearly.
	"""
	assert conversion_factor > 0, "conversion_factor must be positive"
	if delta_seconds <= 0:
		return 0.0
	avg_power = (power_a + power_b) / 2.0
	return (avg_power * (delta_seconds / SECONDS_PER_HOUR)) / conversion_factor


def left_riemann_energy(
	samples: Sequence[Tuple[float, datetime]],
	end_time: datetime,
	conversion_factor: float,
	max_segment_hours: float = MAX_SEGMENT_HOURS,
) -> Mapping[str, object]:
	"""Integrate power samples using a left Riemann sum (matches HA's integration helper).

	``samples`` are ``(power, timestamp)`` tuples; they will be sorted by time.
	The final segment from the last sample to ``end_time`` IS included, holding
	the last power constant. The caller must start the next window at this
	window's ``end_time`` so windows tile without gaps or overlaps.

	Returns a dict with ``total_energy`` (kWh, >= 0), ``segments`` (count),
	``max_power``, ``min_power`` and ``avg_power`` for diagnostics.
	"""
	assert conversion_factor > 0, "conversion_factor must be positive"

	ordered: List[Tuple[float, datetime]] = sorted(samples, key=lambda item: item[1])

	total_energy = 0.0
	segment_count = 0
	max_power = 0.0
	min_power = float("inf")
	power_time_product = 0.0
	covered_hours = 0.0

	def _add_segment(power: float, duration_hours: float) -> None:
		nonlocal total_energy, segment_count, power_time_product, covered_hours
		nonlocal max_power, min_power
		if duration_hours <= 0 or duration_hours >= max_segment_hours:
			return
		total_energy += (power * duration_hours) / conversion_factor
		segment_count += 1
		power_time_product += power * duration_hours
		covered_hours += duration_hours
		max_power = max(max_power, power)
		min_power = min(min_power, power)

	for index in range(1, len(ordered)):
		prev_power, prev_time = ordered[index - 1]
		_, curr_time = ordered[index]
		_add_segment(prev_power, (curr_time - prev_time).total_seconds() / SECONDS_PER_HOUR)

	# Close the window: hold the final reading until end_time so no slice of
	# the window is silently dropped (this previously caused a systematic
	# under-read of roughly source_interval / window_length).
	if ordered:
		last_power, last_time = ordered[-1]
		_add_segment(last_power, (end_time - last_time).total_seconds() / SECONDS_PER_HOUR)

	avg_power = (power_time_product / covered_hours) if covered_hours > 0 else 0.0

	return {
		"total_energy": max(0.0, total_energy),
		"segments": segment_count,
		"max_power": max_power,
		"min_power": min_power if min_power != float("inf") else 0.0,
		"avg_power": avg_power,
	}
