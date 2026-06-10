"""Constants for the Energy Sensor Generator integration."""

DOMAIN = "energy_sensor_generator"
STORAGE_FILE = "energy_sensor_generator.json" 

# Configuration keys
CONF_DEBUG_LOGGING = "debug_logging"
CONF_USE_STATISTICAL = "use_statistical_calculation"
# Removed redundant point sampling options - simplified to just use statistical calculation toggle 
CONF_CREATE_SYNTHETIC_GRID_TOTAL = "create_synthetic_grid_total"
CONF_FORCE_STATISTICAL_ONLY = "force_statistical_only"
CONF_STAT_LOOKBACK_MINUTES = "stat_initial_lookback_minutes"
CONF_MAX_ENERGY_PER_HOUR = "max_energy_per_hour"  # Maximum kWh per hour to prevent spikes
CONF_CONSTANT_POWER_DEVICES = "constant_power_devices"
CONF_PRICE_ADJUST_SENSORS = "price_adjust_sensors"

# Internal safeguards
# Maximum gap (seconds) we are willing to bridge with energy calculations.
# Gaps longer than max(this, 3 x sample_interval) indicate a restart or an
# offline source; we resync tracking instead of guessing the missing energy.
POINT_SAMPLING_MAX_GAP_SECONDS = 600