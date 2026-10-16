# Project outline

Energy Sensor Generator is a Home Assistant custom integration (`custom_components/energy_sensor_generator`) that creates `total_increasing` kWh sensors from power sensors, constant-power switches, and optional price add-ons.

## Layout

- `config_flow.py` — setup (pick power sensors), one subentry flow per source type (power sensor, constant load, price add-on), and the Settings options flow.
- `naming.py` — subentry → base name / unique ID planning and the version 1 → 2 migration mapping (no Home Assistant imports).
- `energy_math.py` — left Riemann / held-power / point-sampling maths (unit tested).
- `sensor.py` / `period_sensors.py` — live entities and interval updates.
- `utils.py` — debounced Store persistence.
- `custom_components/energy_sensor_generator/brand/icon.png` — HACS brand asset.
- `hacs.json` — HACS manifest (name, min HA 2025.3, render README).

## Configure UI

Each source is a config subentry (`power_sensor`, `constant_load`, `price_adjust`), so Home Assistant renders the add buttons and per-source edit/delete on the integration page. Entities are added with `config_subentry_id`, so deleting a subentry removes them. Subentry changes fire the entry's update listener, which reloads the integration.

Global settings (sampling, statistical options, spike cap, synthetic grid total, debug logging) are the entry's options.

Config entry version 2 stores sources as subentries. `async_migrate_entry` converts version 1 option lists without changing unique IDs or storage keys.

## Calculations (checked in 0.0.86)

- Statistical path: `left_riemann_energy` over recorder history, including the last sample → window end, with windows tiling.
- Point sampling: pending left-Riemann segments from state changes plus a held-power tail from the current `last_update` to now.
- Restarts / offline sources: gaps longer than `max(10 minutes, 3 × sample interval)` are not bridged.
- Units: `conversion_factor_from_unit` maps kW → 1 and W (or unknown) → 1000.

## HACS default listing (remaining steps)

Repo-side files are in place. After pushing 0.0.86 and CI is green:

1. GitHub repo description and topics (`home-assistant`, `hacs`, `energy`, `integration`, `custom-component`).
2. Publish a GitHub **release** (not just a tag) for `0.0.86` once hassfest and HACS actions pass.
3. Confirm the repo is public, issues enabled, and not archived.
4. Open a PR against [hacs/default](https://github.com/hacs/default) adding `vortitron/energy-sensor-generator` alphabetically to `integration`. The submitter must be the owner. Review often takes months.
5. Optional: add the domain to [home-assistant/brands](https://github.com/home-assistant/brands) so the icon also appears in Settings → Devices & Services. HACS itself is satisfied by `custom_components/energy_sensor_generator/brand/icon.png`.

Users can already install it as a **custom repository** without that PR.
