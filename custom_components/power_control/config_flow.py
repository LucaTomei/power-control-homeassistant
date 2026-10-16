"""Config and options flow: everything is chosen from the Home Assistant UI."""

from __future__ import annotations

from typing import Any, Final

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TimeSelector,
)

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
    CONF_NOTIFY_PRESENCE,
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
    DEFAULT_MIN_ACTIVE_W,
    DEFAULT_RESTORE_DELAY,
    DEFAULT_SHED_INTERVAL,
    DEFAULT_URGENT_DELAY,
    DEFAULT_VOICE_END,
    DEFAULT_VOICE_START,
    DEFAULT_WARNING_MINUTES,
    DOMAIN,
    LOAD_DOMAINS,
    PRESENCE_DOMAINS,
)
from .engine import auto_power_sensor, backup_store
from .notifier import auto_presence

CONF_RESTORE: Final = "restore_previous"

# Any sensor: some integrations do not set device_class "power" on their sensors
POWER_SENSOR = EntitySelector(EntitySelectorConfig(filter=[{"domain": "sensor"}]))


def _number(minimum: float, maximum: float, step: float, unit: str) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(min=minimum, max=maximum, step=step, unit_of_measurement=unit, mode=NumberSelectorMode.BOX)
    )


def default_options() -> dict[str, Any]:
    return {
        CONF_LOADS: [],
        CONF_LOAD_SENSORS: {},
        CONF_NO_RESTORE: [],
        CONF_NOTIFY_SERVICES: [],
        CONF_NOTIFY_ONLY_HOME: True,
        CONF_NOTIFY_PRESENCE: {},
        CONF_VOICE_PLAYERS: [],
        CONF_VOICE_START: DEFAULT_VOICE_START,
        CONF_VOICE_END: DEFAULT_VOICE_END,
        CONF_EVENTS: list(ALL_EVENTS),
    }


class PowerControlConfigFlow(ConfigFlow, domain=DOMAIN):
    """First setup: the house meter and the contract power."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        # Configuration of a previous installation (removed and added again)
        store = backup_store(self.hass)
        backup = await store.async_load() or {}
        previous = backup.get("options") if isinstance(backup.get("options"), dict) else None
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {k: v for k, v in user_input.items() if k != CONF_RESTORE}
            if self.hass.states.get(data[CONF_METER]) is None:
                errors[CONF_METER] = "meter_not_found"
            else:
                options = default_options()
                if previous and user_input.get(CONF_RESTORE):
                    # the meter just chosen wins over the one saved in the advanced settings
                    options.update({k: v for k, v in previous.items() if k != CONF_METER})
                else:
                    await store.async_save({"fresh": True})  # start from scratch, thresholds included
                return self.async_create_entry(title="Power Control", data=data, options=options)
        fields: dict[Any, Any] = {
            vol.Required(CONF_METER): POWER_SENSOR,
            vol.Required(CONF_CONTRACT_KW, default=DEFAULT_CONTRACT_KW): _number(1, 50, 0.5, "kW"),
        }
        suggested = user_input
        if previous:
            fields[vol.Required(CONF_RESTORE, default=True)] = BooleanSelector()
            if suggested is None and isinstance(backup.get("data"), dict):
                suggested = {**backup["data"], CONF_METER: previous.get(CONF_METER) or backup["data"].get(CONF_METER)}
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(vol.Schema(fields), suggested),
            errors=errors,
            description_placeholders={"loads": str(len(previous.get(CONF_LOADS) or [])) if previous else "0"},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PowerControlOptionsFlow:
        return PowerControlOptionsFlow()


def _notify_services(hass: HomeAssistant, selected: list[str]) -> list[SelectOptionDict]:
    services = set(hass.services.async_services().get("notify", {})) - {"send_message"}
    services |= set(selected)  # keep a phone that is offline right now
    ordered = sorted(services, key=lambda s: (not s.startswith("mobile_app_"), s))
    return [SelectOptionDict(value=s, label=f"notify.{s}") for s in ordered]


class PowerControlOptionsFlow(OptionsFlow):
    """Settings menu: loads and their order, power sensors, notifications, advanced."""

    def __init__(self) -> None:
        self._options: dict[str, Any] | None = None
        self._sensor_keys: dict[str, str] = {}

    @property
    def options(self) -> dict[str, Any]:
        if self._options is None:
            self._options = {**default_options(), **self.config_entry.options}
        return self._options

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init", menu_options=["loads", "sensors", "notifications", "presence", "advanced"]
        )

    def _async_save(self) -> ConfigFlowResult:
        """Every section is saved as soon as it is confirmed: closing the dialog loses nothing."""
        return self.async_create_entry(data=self.options)

    # -------------------------------------------------------------- loads
    async def async_step_loads(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            loads = list(dict.fromkeys(user_input.get(CONF_LOADS, [])))
            self.options[CONF_LOADS] = loads
            self.options[CONF_LOAD_SENSORS] = {
                k: v for k, v in self.options.get(CONF_LOAD_SENSORS, {}).items() if k in loads
            }
            self.options[CONF_NO_RESTORE] = [e for e in self.options.get(CONF_NO_RESTORE, []) if e in loads]
            return self._async_save()
        schema = vol.Schema(
            {
                vol.Optional(CONF_LOADS): EntitySelector(
                    EntitySelectorConfig(filter=[{"domain": LOAD_DOMAINS}], multiple=True, reorder=True)
                )
            }
        )
        return self.async_show_form(
            step_id="loads", data_schema=self.add_suggested_values_to_schema(schema, self.options)
        )

    # -------------------------------------------------------------- sensors
    async def async_step_sensors(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        loads: list[str] = self.options.get(CONF_LOADS, [])
        if not loads:
            return self.async_abort(reason="no_loads")
        if user_input is not None:
            self.options[CONF_LOAD_SENSORS] = {
                entity: user_input[key] for key, entity in self._sensor_keys.items() if user_input.get(key)
            }
            self.options[CONF_NO_RESTORE] = [e for e in user_input.get(CONF_NO_RESTORE, []) if e in loads]
            return self._async_save()

        chosen = self.options.get(CONF_LOAD_SENSORS, {})
        fields: dict[Any, Any] = {}
        suggested: dict[str, Any] = {CONF_NO_RESTORE: self.options.get(CONF_NO_RESTORE, [])}
        self._sensor_keys = {}
        auto_lines = []
        for order, entity in enumerate(loads, start=1):
            state = self.hass.states.get(entity)
            name = state.name if state else entity
            key = f"{order}. {name} ({entity})"
            self._sensor_keys[key] = entity
            fields[vol.Optional(key)] = POWER_SENSOR
            if chosen.get(entity):
                suggested[key] = chosen[entity]
            auto = auto_power_sensor(self.hass, entity)
            auto_lines.append(f"- {name}: {auto or '—'}")
        fields[vol.Optional(CONF_NO_RESTORE)] = EntitySelector(
            EntitySelectorConfig(include_entities=loads, multiple=True)
        )
        return self.async_show_form(
            step_id="sensors",
            data_schema=self.add_suggested_values_to_schema(vol.Schema(fields), suggested),
            description_placeholders={"auto": "\n".join(auto_lines)},
        )

    # -------------------------------------------------------------- notifications
    async def async_step_notifications(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            services = user_input.get(CONF_NOTIFY_SERVICES, [])
            self.options[CONF_NOTIFY_SERVICES] = services
            self.options[CONF_NOTIFY_PRESENCE] = {
                k: v for k, v in self.options.get(CONF_NOTIFY_PRESENCE, {}).items() if k in services
            }
            self.options[CONF_NOTIFY_ONLY_HOME] = user_input.get(CONF_NOTIFY_ONLY_HOME, False)
            self.options[CONF_VOICE_PLAYERS] = user_input.get(CONF_VOICE_PLAYERS, [])
            self.options[CONF_TTS_ENTITY] = user_input.get(CONF_TTS_ENTITY)
            self.options[CONF_VOICE_START] = user_input.get(CONF_VOICE_START, DEFAULT_VOICE_START)
            self.options[CONF_VOICE_END] = user_input.get(CONF_VOICE_END, DEFAULT_VOICE_END)
            self.options[CONF_EVENTS] = user_input.get(CONF_EVENTS, [])
            return self._async_save()
        selected = self.options.get(CONF_NOTIFY_SERVICES, [])
        schema = vol.Schema(
            {
                vol.Optional(CONF_NOTIFY_SERVICES): SelectSelector(
                    SelectSelectorConfig(
                        options=_notify_services(self.hass, selected), multiple=True, mode=SelectSelectorMode.DROPDOWN
                    )
                ),
                vol.Optional(CONF_NOTIFY_ONLY_HOME): BooleanSelector(),
                vol.Optional(CONF_EVENTS): SelectSelector(
                    SelectSelectorConfig(
                        options=list(ALL_EVENTS), multiple=True, mode=SelectSelectorMode.LIST, translation_key="events"
                    )
                ),
                vol.Optional(CONF_VOICE_PLAYERS): EntitySelector(
                    EntitySelectorConfig(filter=[{"domain": "media_player"}], multiple=True)
                ),
                vol.Optional(CONF_TTS_ENTITY): EntitySelector(EntitySelectorConfig(filter=[{"domain": "tts"}])),
                vol.Optional(CONF_VOICE_START): TimeSelector(),
                vol.Optional(CONF_VOICE_END): TimeSelector(),
            }
        )
        return self.async_show_form(
            step_id="notifications", data_schema=self.add_suggested_values_to_schema(schema, self.options)
        )

    # -------------------------------------------------------------- presence
    async def async_step_presence(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        services: list[str] = self.options.get(CONF_NOTIFY_SERVICES, [])
        if not services:
            return self.async_abort(reason="no_phones")
        keys = {f"notify.{service}": service for service in services}
        if user_input is not None:
            self.options[CONF_NOTIFY_PRESENCE] = {
                service: user_input[key] for key, service in keys.items() if user_input.get(key)
            }
            return self._async_save()
        chosen = self.options.get(CONF_NOTIFY_PRESENCE, {})
        selector = EntitySelector(EntitySelectorConfig(filter=[{"domain": PRESENCE_DOMAINS}]))
        auto_lines = []
        for key, service in keys.items():
            found = auto_presence(self.hass, service)
            auto_lines.append(f"- {key}: {', '.join(found) if found else '—'}")
        return self.async_show_form(
            step_id="presence",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Optional(key): selector for key in keys}),
                {key: chosen[service] for key, service in keys.items() if chosen.get(service)},
            ),
            description_placeholders={"auto": "\n".join(auto_lines)},
        )

    # -------------------------------------------------------------- advanced
    async def async_step_advanced(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self.options.update(user_input)
            return self._async_save()
        defaults = {
            CONF_METER: self.options.get(CONF_METER) or self.config_entry.data[CONF_METER],
            CONF_URGENT_DELAY: DEFAULT_URGENT_DELAY,
            CONF_SHED_INTERVAL: DEFAULT_SHED_INTERVAL,
            CONF_RESTORE_DELAY: DEFAULT_RESTORE_DELAY,
            CONF_CANCEL_DELAY: DEFAULT_CANCEL_DELAY,
            CONF_WARNING_MINUTES: DEFAULT_WARNING_MINUTES,
            CONF_MIN_ACTIVE_W: DEFAULT_MIN_ACTIVE_W,
        }
        current = {k: self.options.get(k, v) for k, v in defaults.items()}
        schema = vol.Schema(
            {
                vol.Required(CONF_METER): POWER_SENSOR,
                vol.Required(CONF_WARNING_MINUTES): _number(0, 60, 1, "min"),
                vol.Required(CONF_URGENT_DELAY): _number(0, 300, 1, "s"),
                vol.Required(CONF_SHED_INTERVAL): _number(0, 300, 1, "s"),
                vol.Required(CONF_RESTORE_DELAY): _number(0, 3600, 5, "s"),
                vol.Required(CONF_CANCEL_DELAY): _number(0, 3600, 5, "s"),
                vol.Required(CONF_MIN_ACTIVE_W): _number(0, 500, 1, "W"),
            }
        )
        return self.async_show_form(
            step_id="advanced", data_schema=self.add_suggested_values_to_schema(schema, current)
        )
