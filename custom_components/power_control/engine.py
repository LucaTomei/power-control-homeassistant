"""Load-shedding engine: switches loads off before the meter trips, and back on later."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
import contextlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
import math
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_OFF,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CONTRACT_KW,
    DOMAIN,
    EVENT_ALERT,
    EVENT_RESTORE,
    EVENT_SHED,
    EVENT_WARNING,
    LOAD_DOMAINS,
    SERVICE_TIMEOUT,
    STATUS_DISABLED,
    STATUS_LOADS_OFF,
    STATUS_METER_UNAVAILABLE,
    STATUS_OK,
    STATUS_OVER,
    STATUS_RESTORING,
    STATUS_SHEDDING,
    STATUS_WARNING,
    STORAGE_VERSION,
    TICK_SECONDS,
    URGENT_WARNING_COOLDOWN,
)
from .notifier import Notifier

_LOGGER = logging.getLogger(__name__)

# States meaning "not drawing power / switched off" for the supported domains
OFF_STATES = {STATE_OFF, "standby"}
NOT_AVAILABLE = {STATE_UNAVAILABLE, STATE_UNKNOWN}

# Settings changed from the dashboard (numbers), saved with the engine state
THRESHOLD_ATTRS = ("shed_w", "urgent_w", "restore_w", "delay_minutes")
SWITCHES = ("enabled", "push", "voice")


@dataclass
class Settings:
    """Everything the engine needs to decide."""

    meter: str
    loads: list[str] = field(default_factory=list)  # priority order
    load_sensors: dict[str, str] = field(default_factory=dict)
    no_restore: list[str] = field(default_factory=list)
    shed_w: float = 3300
    urgent_w: float = 3900
    restore_w: float = 2800
    delay_minutes: float = 60
    urgent_delay: float = 60
    shed_interval: float = 20
    restore_delay: float = 60
    cancel_delay: float = 60
    warning_minutes: float = 2
    min_active_w: float = 15


def duration_text(seconds: float) -> str:
    """60 -> "1 min", 90 -> "90 s"."""
    seconds = round(seconds)
    return f"{seconds // 60} min" if seconds >= 60 and seconds % 60 == 0 else f"{seconds} s"


def backup_store(hass: HomeAssistant) -> Store[dict[str, Any]]:
    """The whole configuration, kept even if the integration is removed and added again."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.backup")


def power_from_state(state: State | None) -> float | None:
    """Return the power of a sensor state in W, or None if not usable."""
    if state is None or state.state in NOT_AVAILABLE:
        return None
    try:
        value = float(state.state)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    if unit == "kW":
        value *= 1000
    elif unit == "mW":
        value /= 1000
    return value


def auto_power_sensor(hass: HomeAssistant, entity_id: str) -> str | None:
    """Find the power sensor of a load on its own device, if unambiguous.

    Works for Zigbee (Zigbee2MQTT/ZHA), Shelly, Tuya and any integration that puts
    the switch and its power sensor on the same device. A device with several
    controllable outputs and one meter (a multi-socket strip) is ambiguous: None.
    """
    reg = er.async_get(hass)
    entry = reg.async_get(entity_id)
    if not entry or not entry.device_id:
        return None
    device_entities = [e for e in er.async_entries_for_device(reg, entry.device_id) if not e.disabled_by]
    power = []
    for e in device_entities:
        if e.domain != "sensor":
            continue
        device_class = e.device_class or e.original_device_class
        if device_class is None and (st := hass.states.get(e.entity_id)):
            device_class = st.attributes.get("device_class")
        if device_class == "power":
            power.append(e.entity_id)
    controls = [e for e in device_entities if e.domain in LOAD_DOMAINS and e.entity_category is None]
    if len(power) == 1 and len(controls) <= 1:
        return power[0]
    return None


class PowerControlEngine:
    """Watches the house meter and switches loads off/on in priority order."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        settings: Settings,
        notifier: Notifier,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.settings = settings
        self.notifier = notifier
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._backup = backup_store(hass)
        self._default_thresholds = self._thresholds()  # before the entities restore theirs

        self.enabled = True
        self.power: float | None = None
        self.deadline: datetime | None = None  # when loads will be switched off
        self.shed: list[dict[str, Any]] = []  # switched-off loads, in switch-off order
        self.phase: str | None = None  # "shed" / "restore" while an action runs
        self.last_event: str | None = None
        self.last_event_at: datetime | None = None

        self._warned = False
        self._over_since: datetime | None = None
        self._urgent_since: datetime | None = None
        self._urgent_warned_at: datetime | None = None
        self._below_since: datetime | None = None
        self._restore_since: datetime | None = None
        self._no_load_alerted = False
        self._failed: set[str] = set()
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._listeners: list[CALLBACK_TYPE] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._status = STATUS_OK
        self._sensor_cache: dict[str, str | None] = {}
        self._started = False

    # ------------------------------------------------------------------ lifecycle
    async def async_start(self) -> None:
        """Restore the saved state and start watching."""
        data = await self._store.async_load()
        if data is None:
            # First start of this entry. Set up from scratch: the defaults, not what Home
            # Assistant restores for the same entity ids. Previous configuration restored at
            # setup: its thresholds and switches. Otherwise keep what the entities restored.
            backup = await self._backup.async_load() or {}
            if backup.get("fresh"):
                data = {"thresholds": self._default_thresholds, "switches": dict.fromkeys(SWITCHES, True)}
            else:
                previous = backup.get("data") if isinstance(backup.get("data"), dict) else {}
                same_contract = previous.get(CONF_CONTRACT_KW) == self.entry.data.get(CONF_CONTRACT_KW)
                data = {
                    "shed": backup.get("shed") or [],  # loads it had switched off and not back on
                    # a different contract power: the thresholds suggested for it
                    "thresholds": backup.get("thresholds") if same_contract else self._default_thresholds,
                    "switches": backup.get("switches"),
                }
        self.shed = [i for i in data.get("shed", []) if isinstance(i, dict) and i.get("entity_id")]
        if deadline := data.get("deadline"):
            self.deadline = dt_util.parse_datetime(deadline)
        self._warned = bool(data.get("warned"))
        self.last_event = data.get("last_event")
        if last := data.get("last_event_at"):
            self.last_event_at = dt_util.parse_datetime(last)
        self._apply_saved_settings(data.get("thresholds"), data.get("switches"))

        watched = [self.settings.meter, *self.settings.loads]
        watched += [s for s in (self.resolve_sensor(e) for e in self.settings.loads) if s]
        self._unsubs.append(async_track_state_change_event(self.hass, list(dict.fromkeys(watched)), self._on_state))
        self._unsubs.append(async_track_time_interval(self.hass, self._on_tick, timedelta(seconds=TICK_SECONDS)))
        self._started = True
        self._save()
        self._evaluate()

    async def async_stop(self) -> None:
        """Stop watching (entry unload)."""
        self._started = False
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._task and not self._task.done():
            self._task.cancel()
            # Let it clean up now, so its delayed save cannot overwrite the final one
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        await self._store.async_save(self._data())
        await self._backup.async_save(self._backup_data())

    # ------------------------------------------------------------------ listeners
    @callback
    def async_add_listener(self, update: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Register an entity update callback."""
        self._listeners.append(update)

        @callback
        def remove() -> None:
            if update in self._listeners:
                self._listeners.remove(update)

        return remove

    @callback
    def _notify_listeners(self) -> None:
        for update in list(self._listeners):
            update()

    # ------------------------------------------------------------------ persistence
    def _thresholds(self) -> dict[str, float]:
        return {attr: getattr(self.settings, attr) for attr in THRESHOLD_ATTRS}

    def _switches(self) -> dict[str, bool]:
        return {"enabled": self.enabled, "push": self.notifier.push_enabled, "voice": self.notifier.voice_enabled}

    def _data(self) -> dict[str, Any]:
        return {
            "shed": self.shed,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "warned": self._warned,
            "last_event": self.last_event,
            "last_event_at": self.last_event_at.isoformat() if self.last_event_at else None,
            "thresholds": self._thresholds(),
            "switches": self._switches(),
        }

    def _backup_data(self) -> dict[str, Any]:
        return {
            "data": dict(self.entry.data),
            "options": dict(self.entry.options),
            "shed": self.shed,
            "thresholds": self._thresholds(),
            "switches": self._switches(),
        }

    def _apply_saved_settings(self, thresholds: Any, switches: Any) -> None:
        """Thresholds and switches saved by the engine win over the entities' restored state.

        They are saved at every change, while the restored state is written by Home
        Assistant only every 15 minutes and on a clean shutdown.
        """
        if isinstance(thresholds, dict):
            values = self._thresholds()
            for attr in THRESHOLD_ATTRS:
                if isinstance(thresholds.get(attr), int | float):
                    values[attr] = float(thresholds[attr])
            if values["restore_w"] < values["shed_w"] < values["urgent_w"] and values["delay_minutes"] >= 0:
                for attr, value in values.items():
                    setattr(self.settings, attr, value)
        if isinstance(switches, dict):
            if isinstance(switches.get("enabled"), bool):
                self.enabled = switches["enabled"]
            if isinstance(switches.get("push"), bool):
                self.notifier.push_enabled = switches["push"]
            if isinstance(switches.get("voice"), bool):
                self.notifier.voice_enabled = switches["voice"]

    @callback
    def _save(self) -> None:
        if not self._started:
            return  # still restoring, or stopped: async_start/async_stop save
        self._store.async_delay_save(self._data, 1)
        self._backup.async_delay_save(self._backup_data, 1)

    @callback
    def async_settings_changed(self) -> None:
        """A switch or a threshold changed from the dashboard: save it now."""
        self._save()

    # ------------------------------------------------------------------ helpers
    def _read_meter(self) -> float | None:
        return power_from_state(self.hass.states.get(self.settings.meter))

    def resolve_sensor(self, entity_id: str) -> str | None:
        """Power sensor of a load: the one chosen in the options, or found on its device.

        A sensor is found automatically only when it is the only power sensor of the
        device and the device controls a single load (a multi-socket strip with one
        meter is ambiguous, so it is ignored unless chosen by hand).
        """
        chosen = self.settings.load_sensors.get(entity_id)
        if chosen:
            return chosen
        if entity_id not in self._sensor_cache:
            self._sensor_cache[entity_id] = auto_power_sensor(self.hass, entity_id)
        return self._sensor_cache[entity_id]

    def load_power(self, entity_id: str) -> float | None:
        sensor = self.resolve_sensor(entity_id)
        return power_from_state(self.hass.states.get(sensor)) if sensor else None

    def load_name(self, entity_id: str) -> str:
        state = self.hass.states.get(entity_id)
        return state.name if state else entity_id

    @staticmethod
    def _is_on(state: State | None) -> bool:
        return state is not None and state.state not in OFF_STATES | NOT_AVAILABLE

    def is_active(self, entity_id: str) -> bool:
        """True if the load is on and actually drawing power (when measurable)."""
        if not self._is_on(self.hass.states.get(entity_id)):
            return False
        power = self.load_power(entity_id)
        if power is not None:
            return power >= self.settings.min_active_w
        return True

    def _shed_ids(self) -> set[str]:
        return {item["entity_id"] for item in self.shed}

    def next_load_to_shed(self) -> str | None:
        shed = self._shed_ids()
        for entity_id in self.settings.loads:
            if entity_id in shed or entity_id in self._failed:
                continue
            if self.is_active(entity_id):
                return entity_id
        return None

    @callback
    def _start(self, coro: Coroutine[Any, Any, None], name: str) -> None:
        self._task = self.entry.async_create_background_task(self.hass, coro, f"{DOMAIN}_{name}")

    def _busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def _record(self, message: str) -> None:
        self.last_event = message
        self.last_event_at = dt_util.utcnow()
        self._save()

    @callback
    def _send(self, kind: str, message: str, critical: bool = False) -> None:
        """Notify in the background: a slow phone or speaker must not delay a switch-off."""
        self.entry.async_create_background_task(
            self.hass, self.notifier.async_send(kind, message, critical=critical), f"{DOMAIN}_notify"
        )

    # ------------------------------------------------------------------ state machine
    @callback
    def _on_state(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        new_state = event.data["new_state"]
        old_state = event.data["old_state"]
        # A switched-off load turned back on by someone (or something else): forget it.
        # Only a real off -> on change counts: right after a switch-off a Zigbee/Wi-Fi
        # device may still report "on" (attribute updates) before confirming "off".
        if (
            self.phase != "restore"
            and entity_id in self._shed_ids()
            and self._is_on(new_state)
            and not self._is_on(old_state)
            and old_state is not None
            and old_state.state not in NOT_AVAILABLE
        ):
            self.shed = [i for i in self.shed if i["entity_id"] != entity_id]
            self._save()
        self._evaluate()

    @callback
    def _on_tick(self, _now: datetime) -> None:
        self._evaluate()

    @callback
    def async_refresh(self) -> None:
        """Re-evaluate now (after a setting changed)."""
        self._save()
        if self.deadline is not None and self._over_since is not None and not self._busy():
            # The delay may have changed: count it from when the consumption went over.
            deadline = self._over_since + timedelta(minutes=self.settings.delay_minutes)
            if deadline != self.deadline:
                self.deadline = deadline
                self._warned = False
                self._save()
        self._evaluate()

    @callback
    def _evaluate(self) -> None:
        if not self._started:
            return  # settings are still being restored
        now = dt_util.utcnow()
        self.power = power = self._read_meter()
        s = self.settings

        if self._busy():
            self._update_status()
            return
        if not self.enabled:
            self._over_since = self._urgent_since = self._below_since = self._restore_since = None
            if self.deadline is not None:
                self.deadline = None
                self._warned = False
                self._save()
            self._update_status()
            return
        if power is None:
            # Never act on a missing reading: 'unavailable' must not look like 0 W.
            self._urgent_since = self._below_since = self._restore_since = None
            self._update_status()
            return

        if power > s.urgent_w:
            if self._urgent_since is None:
                self._urgent_since = now
                if s.urgent_delay > 0:
                    self._warn_urgent(power, now)
            if (now - self._urgent_since).total_seconds() >= s.urgent_delay and (
                self.next_load_to_shed() or not self._no_load_alerted
            ):
                self._start(self._async_shed(urgent=True), "shed")
                self._update_status()
                return
        else:
            self._urgent_since = None

        if power > s.shed_w:
            self._below_since = None
            self._restore_since = None
            if self._over_since is None:
                self._over_since = now
            if self.deadline is None:
                self.deadline = now + timedelta(minutes=s.delay_minutes)
                self._warned = False
                self._save()
            can_act = self.next_load_to_shed() is not None or not self._no_load_alerted
            if now >= self.deadline:
                if can_act:
                    self._start(self._async_shed(urgent=False), "shed")
            elif not self._warned and now >= self.deadline - timedelta(minutes=s.warning_minutes):
                self._warned = True
                self._save()
                if can_act:
                    self._warn(power)
        else:
            self._over_since = None
            self._no_load_alerted = False
            if self.deadline is not None:
                if self._below_since is None:
                    self._below_since = now
                if (now - self._below_since).total_seconds() >= s.cancel_delay:
                    self.deadline = None
                    self._warned = False
                    self._below_since = None
                    self._save()
            if power < s.restore_w and self.shed and self.deadline is None:
                if self._restore_since is None:
                    self._restore_since = now
                if (now - self._restore_since).total_seconds() >= s.restore_delay:
                    self._start(self._async_restore(force=False), "restore")
            else:
                self._restore_since = None
        self._update_status()

    @callback
    def _update_status(self) -> None:
        if self.phase == "shed":
            status = STATUS_SHEDDING
        elif self.phase == "restore":
            status = STATUS_RESTORING
        elif not self.enabled:
            status = STATUS_DISABLED
        elif self.power is None:
            status = STATUS_METER_UNAVAILABLE
        elif self.deadline is not None:
            status = STATUS_WARNING if self._warned else STATUS_OVER
        elif self.shed:
            status = STATUS_LOADS_OFF
        else:
            status = STATUS_OK
        self._status = status
        self._notify_listeners()

    @property
    def status(self) -> str:
        return self._status

    @property
    def urgent_deadline(self) -> datetime | None:
        """When a load is switched off if consumption stays over the urgent threshold."""
        if self._urgent_since is None or not self.enabled or self.next_load_to_shed() is None:
            return None
        return self._urgent_since + timedelta(seconds=self.settings.urgent_delay)

    # ------------------------------------------------------------------ actions
    @callback
    def _warn(self, power: float) -> None:
        s = self.settings
        minutes_left = max(0, round((self.deadline - dt_util.utcnow()).total_seconds() / 60)) if self.deadline else 0
        nxt = self.next_load_to_shed()
        msg = self.notifier.text(
            "warning",
            power=round(power),
            shed=round(s.shed_w),
            minutes=minutes_left,
            load=self.load_name(nxt) if nxt else None,
        )
        self._record(msg)
        self._send(EVENT_WARNING, msg)

    @callback
    def _warn_urgent(self, power: float, now: datetime) -> None:
        """Above the urgent threshold: say at once what will be switched off if it stays there."""
        nxt = self.next_load_to_shed()
        if nxt is None:
            return  # nothing to switch off: the alert comes when the delay is over
        if self._urgent_warned_at and (now - self._urgent_warned_at).total_seconds() < URGENT_WARNING_COOLDOWN:
            return
        self._urgent_warned_at = now
        msg = self.notifier.text(
            "urgent_warning",
            power=round(power),
            urgent=round(self.settings.urgent_w),
            delay=duration_text(self.settings.urgent_delay),
            load=self.load_name(nxt),
        )
        self._record(msg)
        self._send(EVENT_WARNING, msg, critical=True)

    async def _async_shed(self, urgent: bool) -> None:
        async with self._lock:
            self.phase = "shed"
            self._update_status()
            s = self.settings
            try:
                while self.enabled:
                    power = self._read_meter()
                    if power is None or power <= s.shed_w:
                        break
                    entity_id = self.next_load_to_shed()
                    if entity_id is None:
                        if not self._no_load_alerted:
                            self._no_load_alerted = True
                            msg = self.notifier.text("alert", power=round(power), shed=round(s.shed_w))
                            self._record(msg)
                            self._send(EVENT_ALERT, msg, critical=True)
                        break
                    name = self.load_name(entity_id)
                    load_power = self.load_power(entity_id)
                    item = {
                        "entity_id": entity_id,
                        "name": name,
                        "power": round(load_power) if load_power is not None else None,
                        "state": self._snapshot(entity_id),
                        "at": dt_util.utcnow().isoformat(),
                    }
                    # Recorded before the command: if this is interrupted (reload, restart)
                    # the load is still known and will be switched back on.
                    self.shed.append(item)
                    self._save()
                    if not await self._async_turn_off(entity_id):
                        self.shed.remove(item)
                        self._save()
                        self._failed.add(entity_id)
                        continue
                    msg = self.notifier.text(
                        "shed_urgent" if urgent else "shed",
                        power=round(power),
                        shed=round(s.shed_w),
                        urgent=round(s.urgent_w),
                        load=name,
                        load_power=round(load_power) if load_power is not None else None,
                    )
                    self._record(msg)
                    self._send(EVENT_SHED, msg, critical=urgent)
                    self._notify_listeners()
                    await asyncio.sleep(s.shed_interval)
            finally:
                self.deadline = None
                self._warned = False
                self._urgent_since = None
                self._restore_since = None
                self._failed.clear()
                self.phase = None
                self._save()
                self._update_status()

    async def _async_restore(self, force: bool) -> None:
        async with self._lock:
            self.phase = "restore"
            self._update_status()
            s = self.settings
            skipped: list[str] = []
            try:
                while self.shed and (force or self.enabled):
                    item = self.shed[-1]  # last switched off, first switched back on
                    entity_id = item["entity_id"]
                    state = self.hass.states.get(entity_id)
                    if entity_id in s.no_restore or entity_id not in s.loads or state is None:
                        self.shed.remove(item)
                        if state is not None:
                            skipped.append(item.get("name") or entity_id)
                        self._save()
                        continue
                    if state.state in NOT_AVAILABLE:
                        break  # wait until the device is reachable again
                    if self._is_on(state):
                        self.shed.remove(item)  # already switched on by someone
                        self._save()
                        continue
                    if not force:
                        power = self._read_meter()
                        expected = item.get("power") or 0
                        if power is None or power >= s.restore_w or power + expected >= s.shed_w:
                            break  # not enough headroom yet: try again later
                    if not await self._async_turn_on(entity_id, item.get("state") or {}):
                        break
                    if item in self.shed:
                        self.shed.remove(item)
                    self._save()
                    power = self._read_meter()
                    msg = self.notifier.text(
                        "restore_all" if not self.shed else "restore",
                        power=round(power) if power is not None else None,
                        load=item.get("name") or entity_id,
                    )
                    self._record(msg)
                    self._send(EVENT_RESTORE, msg)
                    self._notify_listeners()
                    if self.shed:
                        await asyncio.sleep(s.shed_interval)
                if skipped:
                    msg = self.notifier.text("restore_skipped", loads=", ".join(skipped))
                    self._record(msg)
                    self._send(EVENT_RESTORE, msg)
            finally:
                if not self.shed:
                    self._no_load_alerted = False
                self._restore_since = None
                self.phase = None
                self._save()
                self._update_status()

    async def async_restore_now(self) -> None:
        """Button: switch every load back on now, ignoring the thresholds."""
        if self._busy():
            _LOGGER.info("Power Control is busy, restore not started")
            return
        self._start(self._async_restore(force=True), "restore")

    @callback
    def async_forget(self) -> None:
        """Button: forget the switched-off loads without switching them on."""
        self.shed = []
        self._no_load_alerted = False
        self._save()
        self._update_status()

    @callback
    def async_set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self._save()
        self._evaluate()

    # ------------------------------------------------------------------ device control
    def _snapshot(self, entity_id: str) -> dict[str, Any]:
        state = self.hass.states.get(entity_id)
        if state and entity_id.startswith("climate."):
            return {"hvac_mode": state.state}
        return {}

    async def _async_call(self, domain: str, service: str, data: dict[str, Any]) -> bool:
        try:
            async with asyncio.timeout(SERVICE_TIMEOUT):
                await self.hass.services.async_call(domain, service, data, blocking=True)
        except Exception as err:  # noqa: BLE001 - a broken device must not stop the engine
            _LOGGER.warning("%s.%s on %s failed: %s", domain, service, data.get(ATTR_ENTITY_ID), err)
            return False
        return True

    async def _async_turn_off(self, entity_id: str) -> bool:
        if entity_id.startswith("climate."):
            ok = await self._async_call("climate", "set_hvac_mode", {ATTR_ENTITY_ID: entity_id, "hvac_mode": "off"})
        else:
            ok = await self._async_call("homeassistant", "turn_off", {ATTR_ENTITY_ID: entity_id})
        # A slow device may answer late: what matters is the resulting state.
        return ok or not self._is_on(self.hass.states.get(entity_id))

    async def _async_turn_on(self, entity_id: str, snapshot: dict[str, Any]) -> bool:
        if entity_id.startswith("climate.") and snapshot.get("hvac_mode") not in (None, STATE_OFF):
            ok = await self._async_call(
                "climate", "set_hvac_mode", {ATTR_ENTITY_ID: entity_id, "hvac_mode": snapshot["hvac_mode"]}
            )
        else:
            ok = await self._async_call("homeassistant", "turn_on", {ATTR_ENTITY_ID: entity_id})
        return ok or self._is_on(self.hass.states.get(entity_id))

    # ------------------------------------------------------------------ for entities
    def loads_info(self) -> list[dict[str, Any]]:
        shed = self._shed_ids()
        info = []
        for order, entity_id in enumerate(self.settings.loads, start=1):
            state = self.hass.states.get(entity_id)
            power = self.load_power(entity_id)
            info.append(
                {
                    "order": order,
                    "entity_id": entity_id,
                    "name": self.load_name(entity_id),
                    "state": state.state if state else "missing",
                    "power": round(power) if power is not None else None,
                    "sensor": self.resolve_sensor(entity_id),
                    "active": self.is_active(entity_id),
                    "switched_off": entity_id in shed,
                    "auto_restore": entity_id not in self.settings.no_restore,
                }
            )
        return info
