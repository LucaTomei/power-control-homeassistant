"""Sensors: status, countdown, switched-off loads, last event, headroom."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PowerControlConfigEntry
from .const import ALL_STATUSES
from .entity import PowerControlEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PowerControlConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    engine = entry.runtime_data
    async_add_entities(
        [
            StatusSensor(engine, entry, "sensor", "status"),
            ShedAtSensor(engine, entry, "sensor", "shed_at"),
            LoadsOffSensor(engine, entry, "sensor", "loads_off"),
            LastEventSensor(engine, entry, "sensor", "last_event"),
            HeadroomSensor(engine, entry, "sensor", "headroom"),
        ]
    )


class StatusSensor(PowerControlEntity, SensorEntity):
    """What Power Control is doing, with the list of loads in priority order."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ALL_STATUSES
    _unrecorded_attributes = frozenset({"loads", "next_to_switch_off"})

    @property
    def native_value(self) -> str:
        return self._engine.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        engine = self._engine
        nxt = engine.next_load_to_shed()
        return {
            "power": round(engine.power) if engine.power is not None else None,
            "next_to_switch_off": engine.load_name(nxt) if nxt else None,
            "loads": engine.loads_info(),
        }


class ShedAtSensor(PowerControlEntity, SensorEntity):
    """When the loads will be switched off (empty when consumption is normal)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        return self._engine.deadline


class LoadsOffSensor(PowerControlEntity, SensorEntity):
    """How many loads Power Control switched off and still has to switch back on."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int:
        return len(self._engine.shed)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "loads": [item.get("name") or item["entity_id"] for item in self._engine.shed],
            "entity_ids": [item["entity_id"] for item in self._engine.shed],
        }


class LastEventSensor(PowerControlEntity, SensorEntity):
    """The last message (switch-off, switch-on, warning)."""

    @property
    def native_value(self) -> str | None:
        msg = self._engine.last_event
        return msg[:255] if msg else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        at = self._engine.last_event_at
        return {"time": at.isoformat() if at else None}


class HeadroomSensor(PowerControlEntity, SensorEntity):
    """Watts still available before the switch-off threshold (negative = over)."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    @property
    def native_value(self) -> float | None:
        engine = self._engine
        if engine.power is None:
            return None
        return round(engine.settings.shed_w - engine.power)
