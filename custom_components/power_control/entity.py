"""Base entity for Power Control."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .engine import PowerControlEngine


class PowerControlEntity(Entity):
    """Entity bound to the Power Control engine."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, engine: PowerControlEngine, entry: ConfigEntry, platform: str, key: str) -> None:
        self._engine = engine
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        # Fixed, language-independent entity ids (used by the dashboard card)
        self.entity_id = f"{platform}.power_control_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Power Control",
            manufacturer="Power Control",
            model="Load shedding",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._engine.async_add_listener(self.async_write_ha_state))
