"""Switches: Power Control on/off, phone notifications, voice announcements."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import PowerControlConfigEntry
from .engine import PowerControlEngine
from .entity import PowerControlEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PowerControlConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    engine = entry.runtime_data
    notifier = engine.notifier

    def set_push(value: bool) -> None:
        notifier.push_enabled = value

    def set_voice(value: bool) -> None:
        notifier.voice_enabled = value

    async_add_entities(
        [
            PowerControlSwitch(engine, entry, "enabled", lambda: engine.enabled, engine.async_set_enabled, None),
            PowerControlSwitch(
                engine, entry, "push_notifications", lambda: notifier.push_enabled, set_push, EntityCategory.CONFIG
            ),
            PowerControlSwitch(
                engine, entry, "voice_notifications", lambda: notifier.voice_enabled, set_voice, EntityCategory.CONFIG
            ),
        ]
    )


class PowerControlSwitch(PowerControlEntity, SwitchEntity, RestoreEntity):
    """A Power Control on/off setting, kept across restarts."""

    def __init__(
        self,
        engine: PowerControlEngine,
        entry: PowerControlConfigEntry,
        key: str,
        getter: Callable[[], bool],
        setter: Callable[[bool], None],
        category: EntityCategory | None,
    ) -> None:
        super().__init__(engine, entry, "switch", key)
        self._getter = getter
        self._setter = setter
        self._attr_entity_category = category

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self._setter(last.state != STATE_OFF)

    @property
    def is_on(self) -> bool:
        return self._getter()

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._setter(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._setter(False)
        self.async_write_ha_state()
