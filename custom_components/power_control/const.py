"""Constants for Power Control."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "power_control"
STORAGE_VERSION: Final = 1

# Config entry data
CONF_METER: Final = "meter"
CONF_CONTRACT_KW: Final = "contract_kw"

# Options
CONF_LOADS: Final = "loads"  # ordered list: first = first to be switched off
CONF_LOAD_SENSORS: Final = "load_sensors"  # {load entity_id: power sensor entity_id}
CONF_NO_RESTORE: Final = "no_restore"  # loads never switched back on automatically
CONF_NOTIFY_SERVICES: Final = "notify_services"  # e.g. ["mobile_app_iphone"]
CONF_NOTIFY_ONLY_HOME: Final = "notify_only_home"
CONF_NOTIFY_PRESENCE: Final = "notify_presence"  # {notify service: presence entity_id}
CONF_VOICE_PLAYERS: Final = "voice_players"
CONF_TTS_ENTITY: Final = "tts_entity"
CONF_VOICE_START: Final = "voice_start"
CONF_VOICE_END: Final = "voice_end"
CONF_EVENTS: Final = "notify_events"
CONF_URGENT_DELAY: Final = "urgent_delay"
CONF_SHED_INTERVAL: Final = "shed_interval"
CONF_RESTORE_DELAY: Final = "restore_delay"
CONF_CANCEL_DELAY: Final = "cancel_delay"
CONF_WARNING_MINUTES: Final = "warning_minutes"
CONF_MIN_ACTIVE_W: Final = "min_active_w"

# Notification kinds
EVENT_WARNING: Final = "warning"  # threshold exceeded, switch-off coming
EVENT_SHED: Final = "shed"  # a load was switched off
EVENT_RESTORE: Final = "restore"  # a load was switched back on
EVENT_ALERT: Final = "alert"  # over the limit and nothing left to switch off
ALL_EVENTS: Final = [EVENT_WARNING, EVENT_SHED, EVENT_RESTORE, EVENT_ALERT]

# Event fired on the bus for every notification (for user automations)
BUS_EVENT: Final = "power_control_event"

DEFAULT_CONTRACT_KW: Final = 3.0
DEFAULT_DELAY_MINUTES: Final = 60  # meter tolerates up to +33% for ~180 min
DEFAULT_URGENT_DELAY: Final = 10  # s above the urgent threshold before acting
DEFAULT_SHED_INTERVAL: Final = 20  # s between two switch-offs / switch-ons
DEFAULT_RESTORE_DELAY: Final = 60  # s below the restore threshold before restoring
DEFAULT_CANCEL_DELAY: Final = 60  # s below the threshold before cancelling the countdown
DEFAULT_WARNING_MINUTES: Final = 2
DEFAULT_MIN_ACTIVE_W: Final = 15  # a load drawing less than this is ignored
DEFAULT_VOICE_START: Final = "09:00:00"
DEFAULT_VOICE_END: Final = "22:00:00"

# Domains that can be used as loads
LOAD_DOMAINS: Final = [
    "switch",
    "light",
    "climate",
    "humidifier",
    "fan",
    "water_heater",
    "media_player",
    "input_boolean",
]

# Domains that can tell whether the owner of a phone is at home
PRESENCE_DOMAINS: Final = ["person", "device_tracker", "binary_sensor", "input_boolean"]

# Engine status values (sensor "Stato")
STATUS_DISABLED: Final = "disabled"
STATUS_METER_UNAVAILABLE: Final = "meter_unavailable"
STATUS_OK: Final = "ok"
STATUS_OVER: Final = "over_threshold"
STATUS_WARNING: Final = "warning"
STATUS_SHEDDING: Final = "shedding"
STATUS_LOADS_OFF: Final = "loads_off"
STATUS_RESTORING: Final = "restoring"
ALL_STATUSES: Final = [
    STATUS_DISABLED,
    STATUS_METER_UNAVAILABLE,
    STATUS_OK,
    STATUS_OVER,
    STATUS_WARNING,
    STATUS_SHEDDING,
    STATUS_LOADS_OFF,
    STATUS_RESTORING,
]

TICK_SECONDS: Final = 5
SERVICE_TIMEOUT: Final = 15  # s, a slow Zigbee/Wi-Fi device must not block the engine


def default_thresholds(contract_kw: float) -> dict[str, float]:
    """Italian-style meter limits for a contract of `contract_kw`.

    The meter allows +10% indefinitely and up to +33% for a limited time:
    switch-off above +10%, urgent just below +33%, restore well below the contract.
    """
    watt = contract_kw * 1000
    return {
        "shed": round(watt * 1.10 / 50) * 50,  # 3 kW -> 3300 W
        "urgent": round(watt * 1.30 / 50) * 50,  # 3 kW -> 3900 W
        "restore": round(watt * 0.93 / 50) * 50,  # 3 kW -> 2800 W
    }
