"""Thresholds and delay, editable from the dashboard."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberDeviceClass, NumberMode, RestoreNumber
from homeassistant.const import UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PowerControlConfigEntry
from .const import DOMAIN
from .engine import PowerControlEngine, Settings
from .entity import PowerControlEntity


@dataclass(frozen=True)
class ThresholdDescription:
    key: str
    attr: str  # Settings attribute
    minimum: float
    maximum: float
    step: float
    unit: str
    device_class: NumberDeviceClass


DESCRIPTIONS = (
    ThresholdDescription("shed_threshold", "shed_w", 100, 30000, 50, UnitOfPower.WATT, NumberDeviceClass.POWER),
    ThresholdDescription("urgent_threshold", "urgent_w", 100, 30000, 50, UnitOfPower.WATT, NumberDeviceClass.POWER),
    ThresholdDescription("restore_threshold", "restore_w", 100, 30000, 50, UnitOfPower.WATT, NumberDeviceClass.POWER),
    ThresholdDescription("shed_delay", "delay_minutes", 0, 180, 1, UnitOfTime.MINUTES, NumberDeviceClass.DURATION),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PowerControlConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    engine = entry.runtime_data
    async_add_entities(PowerControlNumber(engine, entry, d) for d in DESCRIPTIONS)


def check_thresholds(settings: Settings, attr: str, value: float) -> None:
    """Restore < switch-off < urgent, otherwise the engine would flip on and off."""
    values = {"shed_w": settings.shed_w, "urgent_w": settings.urgent_w, "restore_w": settings.restore_w}
    if attr not in values:
        return
    values[attr] = value
    if not values["restore_w"] < values["shed_w"] < values["urgent_w"]:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_thresholds",
            translation_placeholders={
                "restore": f"{values['restore_w']:.0f}",
                "shed": f"{values['shed_w']:.0f}",
                "urgent": f"{values['urgent_w']:.0f}",
            },
        )


class PowerControlNumber(PowerControlEntity, RestoreNumber):
    """A threshold kept across restarts."""

    _attr_mode = NumberMode.BOX

    def __init__(self, engine: PowerControlEngine, entry: PowerControlConfigEntry, desc: ThresholdDescription) -> None:
        super().__init__(engine, entry, "number", desc.key)
        self._desc = desc
        self._attr_native_min_value = desc.minimum
        self._attr_native_max_value = desc.maximum
        self._attr_native_step = desc.step
        self._attr_native_unit_of_measurement = desc.unit
        self._attr_device_class = desc.device_class

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            setattr(self._engine.settings, self._desc.attr, float(last.native_value))

    @property
    def native_value(self) -> float:
        return getattr(self._engine.settings, self._desc.attr)

    async def async_set_native_value(self, value: float) -> None:
        check_thresholds(self._engine.settings, self._desc.attr, value)
        setattr(self._engine.settings, self._desc.attr, value)
        self._engine.async_refresh()
        self.async_write_ha_state()
