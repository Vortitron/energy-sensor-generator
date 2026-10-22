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


KW_UNITS = {"kw", "kilowatt", "kilowatts"}
WATT_UNITS = {"w", "watt", "watts"}


def conversion_factor_from_unit(unit: str | None) -> int:
	"""Return the factor that converts a power reading into kW.

	kW sources use 1; Watts (and unknown/missing units, for backwards
	compatibility) use 1000.
	"""
	if not unit:
		return 1000
	normalised = str(unit).strip().lower()
	if normalised in KW_UNITS:
		return 1
	return 1000


def point_sampling_window_energy_kwh(
	pending_kwh: float,
	last_power: float | None,
	delta_seconds: float,
	conversion_factor: float,
	max_gap_seconds: float,
) -> float | None:
	"""Energy for one interval tick: pending segments plus the held-power tail.

	``pending_kwh`` is energy already accumulated from source state changes
	since the last interval. ``delta_seconds`` must be the time from the
	*current* last-update anchor to now, so a state change that lands during
	an in-flight interval calculation cannot double-count the same window.

	Returns None when there is no last power reading, or when the gap is too
	large to bridge safely.
	"""
	assert conversion_factor > 0, "conversion_factor must be positive"
	if last_power is None:
		return None
	if delta_seconds > max_gap_seconds:
		return None
	if delta_seconds <= 0:
		return max(0.0, pending_kwh)
	tail = held_power_energy_kwh(last_power, delta_seconds, conversion_factor)
	return max(0.0, pending_kwh + tail)


# How far before a gap to look for the last valid reading of the meter counter
# and of the power sources sharing it.
GAP_FILL_LOOKBACK_SECONDS = 3600

# A counter that has recorded nothing since a gap is trusted at its current
# value only after this long, so a meter still catching up is not under-read.
GAP_FILL_SETTLE_SECONDS = 300

# Give up on a gap whose closing counter reading never arrives.
GAP_FILL_GIVE_UP_SECONDS = 24 * 3600


def last_value_at_or_before(
	samples: Sequence[Tuple[float, datetime]],
	when: datetime,
) -> float | None:
	"""Value of the latest sample taken at or before ``when``."""
	found = None
	for value, timestamp in sorted(samples, key=lambda item: item[1]):
		if timestamp > when:
			break
		found = value
	return found


def first_value_at_or_after(
	samples: Sequence[Tuple[float, datetime]],
	when: datetime,
) -> float | None:
	"""Value of the earliest sample taken at or after ``when``."""
	for value, timestamp in sorted(samples, key=lambda item: item[1]):
		if timestamp >= when:
			return value
	return None


def power_share(own_kw: float | None, all_kw: Sequence[float | None]) -> float:
	"""This source's fraction of a meter shared by ``all_kw`` sources (itself included).

	Unknown readings count as zero. When nothing is known, or every source
	read zero, the meter is split evenly.
	"""
	count = max(1, len(all_kw))
	if count == 1:
		return 1.0
	total = sum(max(0.0, value or 0.0) for value in all_kw)
	if total <= 0:
		return 1.0 / count
	return max(0.0, own_kw or 0.0) / total


def counter_gap_fill_kwh(
	counter_before: float | None,
	counter_after: float | None,
	share: float,
) -> float | None:
	"""Energy to credit for a gap from a cumulative meter counter (kWh).

	Returns None when either reading is missing or the counter went
	backwards (meter replaced or reset), so nothing is guessed.
	"""
	if counter_before is None or counter_after is None:
		return None
	delta = counter_after - counter_before
	if delta < 0:
		return None
	return delta * max(0.0, min(1.0, share))


# Readings above this (per source) are treated as glitches unless a source or
# the settings say otherwise: 50 kW is beyond any single household circuit.
DEFAULT_MAX_POWER_W = 50000.0

# A live state change younger than this may simply not be committed to the
# recorder yet, so it says nothing about the recorder lagging.
RECORDER_SETTLE_SECONDS = 15.0


def power_cap_native(max_power_w: float | None, conversion_factor: float) -> float | None:
	"""A cap in watts expressed in the source's unit (None or <= 0: no cap)."""
	if not max_power_w or max_power_w <= 0 or not conversion_factor:
		return None
	return (max_power_w / 1000.0) * conversion_factor


def hold_implausible(
	samples: Sequence[Tuple[float, datetime]],
	cap_native: float | None,
	last_plausible: float | None = None,
) -> Tuple[List[Tuple[float, datetime]], int]:
	"""Replace readings above ``cap_native`` with the previous plausible reading.

	A single bad value from a cloud API (183051 W from a 720 W heat pump) held
	for a quarter of an hour would otherwise add tens of kWh. Holding the last
	good reading keeps the time covered. ``last_plausible`` is the reading to
	hold when a window starts mid-glitch; without one, such readings are
	dropped. Returns the samples (time order) and how many were replaced or
	dropped.
	"""
	ordered = sorted(samples, key=lambda item: item[1])
	if cap_native is None:
		return ordered, 0
	result: List[Tuple[float, datetime]] = []
	rejected = 0
	last_good: float | None = last_plausible if last_plausible is not None and last_plausible <= cap_native else None
	for power, timestamp in ordered:
		if power > cap_native:
			rejected += 1
			if last_good is not None:
				result.append((last_good, timestamp))
			continue
		last_good = power
		result.append((power, timestamp))
	return result, rejected


def recorder_is_lagging(
	recorded: Sequence[Tuple[str, datetime]],
	live_value: str | None,
	live_updated_at: datetime | None,
	window_start: datetime,
	window_end: datetime,
	settle_seconds: float = RECORDER_SETTLE_SECONDS,
) -> bool:
	"""Whether the recorder is missing source states the state machine already has.

	When the recorder stalls, history keeps returning the last recorded value
	and a statistical calculation integrates that stale value for as long as
	the stall lasts. ``recorded`` is the window's history as (state, time).
	History can't be trusted for the window when, for a live state old enough
	to have been committed, either its change falls in the window with no
	recorded state at or after it, or the latest recorded value differs from
	the live one.
	"""
	if live_updated_at is None or live_value is None:
		return False
	if live_updated_at > window_end or (window_end - live_updated_at).total_seconds() < settle_seconds:
		return False
	tolerance_seconds = 2.0
	if live_updated_at > window_start and not any(
		(when - live_updated_at).total_seconds() >= -tolerance_seconds for _, when in recorded
	):
		return True
	if not recorded:
		return False
	latest_value = max(recorded, key=lambda item: item[1])[0]
	return str(latest_value) != str(live_value)
