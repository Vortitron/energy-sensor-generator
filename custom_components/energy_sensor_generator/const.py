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
CONF_POWER_SUM_SENSORS = "power_sum_sensors"

# Constant power device dictionary keys
CONF_CONSTANT_DEVICE_SWITCH_ENTITY_ID = "switch_entity_id"
CONF_CONSTANT_DEVICE_POWER_W = "power_w"
CONF_CONSTANT_DEVICE_NAME = "name"
CONF_CONSTANT_DEVICE_INSTANCES = "instances"

# Internal safeguards
POINT_SAMPLING_MAX_GAP_SECONDS = 600  # Skip point sampling if the gap exceeds this

# Post-restart safety net: audit shortly after startup and roll back obvious overreads
RESTART_AUDIT_DELAY_SECONDS = 600
RESTART_AUDIT_MULTIPLIER = 3.0
RESTART_AUDIT_MIN_DELTA_KWH = 0.05