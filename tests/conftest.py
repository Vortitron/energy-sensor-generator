from __future__ import annotations

import sys
from pathlib import Path


# Ensure the repository root is on sys.path so imports like
# `custom_components.energy_sensor_generator...` work under pytest.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
	sys.path.insert(0, str(ROOT))

