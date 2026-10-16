"""Buttons: switch the loads back on now, or forget them."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PowerControlConfigEntry
from .entity import PowerControlEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PowerControlConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    engine = entry.runtime_data
    async_add_entities(
        [
            RestoreNowButton(engine, entry, "button", "restore_now"),
            ForgetButton(engine, entry, "button", "forget_loads_off"),
        ]
    )


class RestoreNowButton(PowerControlEntity, ButtonEntity):
    """Switch every load Power Control switched off back on, now."""

    async def async_press(self) -> None:
        await self._engine.async_restore_now()


class ForgetButton(PowerControlEntity, ButtonEntity):
    """Forget the switched-off loads without switching them on."""

    async def async_press(self) -> None:
        self._engine.async_forget()
