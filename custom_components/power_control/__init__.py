"""Power Control: switch loads off before the meter trips, and back on later."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    ALL_EVENTS,
    CONF_CANCEL_DELAY,
    CONF_CONTRACT_KW,
    CONF_EVENTS,
    CONF_LOAD_SENSORS,
    CONF_LOADS,
    CONF_METER,
    CONF_MIN_ACTIVE_W,
    CONF_NO_RESTORE,
    CONF_NOTIFY_ONLY_HOME,
    CONF_NOTIFY_SERVICES,
    CONF_RESTORE_DELAY,
    CONF_SHED_INTERVAL,
    CONF_TTS_ENTITY,
    CONF_URGENT_DELAY,
    CONF_VOICE_END,
    CONF_VOICE_PLAYERS,
    CONF_VOICE_START,
    CONF_WARNING_MINUTES,
    DEFAULT_CANCEL_DELAY,
    DEFAULT_CONTRACT_KW,
    DEFAULT_DELAY_MINUTES,
    DEFAULT_MIN_ACTIVE_W,
    DEFAULT_RESTORE_DELAY,
    DEFAULT_SHED_INTERVAL,
    DEFAULT_URGENT_DELAY,
    DEFAULT_VOICE_END,
    DEFAULT_VOICE_START,
    DEFAULT_WARNING_MINUTES,
    default_thresholds,
)
from .engine import PowerControlEngine, Settings
from .notifier import Notifier

PLATFORMS = [Platform.SWITCH, Platform.NUMBER, Platform.SENSOR, Platform.BUTTON]

type PowerControlConfigEntry = ConfigEntry[PowerControlEngine]


def build_settings(entry: ConfigEntry) -> Settings:
    """Settings from the config entry (thresholds are then set by the number entities)."""
    opts = entry.options
    thresholds = default_thresholds(entry.data.get(CONF_CONTRACT_KW, DEFAULT_CONTRACT_KW))
    return Settings(
        meter=opts.get(CONF_METER) or entry.data[CONF_METER],
        loads=list(opts.get(CONF_LOADS, [])),
        load_sensors={k: v for k, v in opts.get(CONF_LOAD_SENSORS, {}).items() if v},
        no_restore=list(opts.get(CONF_NO_RESTORE, [])),
        shed_w=thresholds["shed"],
        urgent_w=thresholds["urgent"],
        restore_w=thresholds["restore"],
        delay_minutes=DEFAULT_DELAY_MINUTES,
        urgent_delay=opts.get(CONF_URGENT_DELAY, DEFAULT_URGENT_DELAY),
        shed_interval=opts.get(CONF_SHED_INTERVAL, DEFAULT_SHED_INTERVAL),
        restore_delay=opts.get(CONF_RESTORE_DELAY, DEFAULT_RESTORE_DELAY),
        cancel_delay=opts.get(CONF_CANCEL_DELAY, DEFAULT_CANCEL_DELAY),
        warning_minutes=opts.get(CONF_WARNING_MINUTES, DEFAULT_WARNING_MINUTES),
        min_active_w=opts.get(CONF_MIN_ACTIVE_W, DEFAULT_MIN_ACTIVE_W),
    )


def build_notifier(hass: HomeAssistant, entry: ConfigEntry) -> Notifier:
    opts = entry.options
    return Notifier(
        hass,
        services=list(opts.get(CONF_NOTIFY_SERVICES, [])),
        only_home=opts.get(CONF_NOTIFY_ONLY_HOME, True),
        voice_players=list(opts.get(CONF_VOICE_PLAYERS, [])),
        tts_entity=opts.get(CONF_TTS_ENTITY) or None,
        voice_start=opts.get(CONF_VOICE_START, DEFAULT_VOICE_START),
        voice_end=opts.get(CONF_VOICE_END, DEFAULT_VOICE_END),
        events=list(opts.get(CONF_EVENTS, ALL_EVENTS)),
    )


async def async_setup_entry(hass: HomeAssistant, entry: PowerControlConfigEntry) -> bool:
    """Set up Power Control."""
    engine = PowerControlEngine(hass, entry, build_settings(entry), build_notifier(hass, entry))
    entry.runtime_data = engine
    # Entities first: they restore the thresholds and switches chosen by the user,
    # so the engine never acts on the default values.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await engine.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: PowerControlConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PowerControlConfigEntry) -> bool:
    """Unload Power Control (the list of switched-off loads is kept)."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded
