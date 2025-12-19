"""Helpers for price adjustment sensors (standalone, testable).

These utilities avoid Home Assistant imports to keep unit tests simple.
"""

from __future__ import annotations

from typing import Any, Optional


_NON_NUMERIC_STATES = {"", "unknown", "unavailable", "none", "null"}
_PRICE_KEYS = {
	"average",
	"min",
	"max",
	"mean",
	"median",
	"peak",
	"off_peak_1",
	"off_peak_2",
	"today",
	"tomorrow",
	"current_price",
	"price",
	"additional_costs_current_hour",
}
_NON_PRICE_KEY_FRAGMENTS = (
	"percent",
	"percentage",
	"currency",
	"country",
	"region",
	"unit",
	"valid",
	"low_price",
)


def normalise_numeric_state(value: object) -> Optional[float]:
	"""Return a float for a HA-like state value, else None."""
	if value is None:
		return None
	text = str(value).strip()
	if text.lower() in _NON_NUMERIC_STATES:
		return None
	try:
		return float(text)
	except (TypeError, ValueError):
		return None


def compute_adjusted_value(source_value: object, add_amount: float) -> Optional[float]:
	"""Compute adjusted numeric value, or None if source is not numeric/available."""
	base = normalise_numeric_state(source_value)
	if base is None:
		return None
	try:
		return base + float(add_amount)
	except (TypeError, ValueError):
		# Treat invalid add amount as not computable
		return None


def _normalise_key(key: str) -> str:
	return (key or "").strip().lower().replace(" ", "_")


def is_price_attribute_key(key: str) -> bool:
	"""Heuristic: decide whether an attribute is a price series/value."""
	key_norm = _normalise_key(key)
	if not key_norm:
		return False
	if "raw" in key_norm:
		return False
	if key_norm in _PRICE_KEYS:
		return True
	if "price" in key_norm and not any(fragment in key_norm for fragment in ("percent", "percentage", "raw")):
		return True
	if any(fragment in key_norm for fragment in _NON_PRICE_KEY_FRAGMENTS):
		return False
	return False


def adjust_attribute_value(value: Any, add_amount: float) -> Any:
	"""Adjust numeric / numeric-lists / dicts-with-'value' by add_amount."""
	# Preserve bools (bool is subclass of int)
	if isinstance(value, bool):
		return value

	# Numeric primitives (and numeric strings)
	adjusted = compute_adjusted_value(value, add_amount)
	if adjusted is not None and isinstance(value, (int, float, str)):
		return adjusted

	# Lists / tuples of prices
	if isinstance(value, (list, tuple)):
		new_items = []
		for item in value:
			item_adj = compute_adjusted_value(item, add_amount)
			new_items.append(item_adj if item_adj is not None else item)
		return list(new_items) if isinstance(value, list) else tuple(new_items)

	# Dict payloads that contain a numeric 'value' field (but keep all other keys)
	if isinstance(value, dict) and "value" in value:
		item_adj = compute_adjusted_value(value.get("value"), add_amount)
		if item_adj is None:
			return dict(value)
		new_dict = dict(value)
		new_dict["value"] = item_adj
		return new_dict

	return value

