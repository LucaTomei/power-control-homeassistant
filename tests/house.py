"""A fake house: meter, plugs and appliances that react to switch on/off."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component

METER = "sensor.house_power"


@dataclass
class Load:
    entity_id: str
    power: float  # W drawn when on
    on: bool
    sensor: str | None  # its own power sensor, if any
    mode: str = "cool"  # climate only


class House:
    """Meter = base consumption + every load that is on."""

    def __init__(self, hass: HomeAssistant, base: float = 500) -> None:
        self.hass = hass
        self.base = base
        self.loads: dict[str, Load] = {}
        self.calls: list[tuple[str, str, str]] = []
        self.notifications: list[tuple[str, dict[str, Any]]] = []
        self.meter_available = True
        self.slow: set[str] = set()  # devices that confirm a command only later
        self._entry = MockConfigEntry(domain="test")

    async def async_setup(self) -> None:
        hass = self.hass
        self._entry.add_to_hass(hass)
        assert await async_setup_component(hass, "homeassistant", {})
        for domain in ("light", "humidifier"):
            for service in ("turn_on", "turn_off"):
                hass.services.async_register(domain, service, self._handle_switch)
        hass.services.async_register("climate", "set_hvac_mode", self._handle_climate)
        hass.services.async_register("climate", "turn_on", self._handle_switch)
        hass.services.async_register("climate", "turn_off", self._handle_switch)
        for service in ("mobile_app_phone_luca", "mobile_app_phone_elisa"):
            hass.services.async_register("notify", service, self._handle_notify)
        self.update()

    # ------------------------------------------------------------- building
    def _device(self, name: str) -> str:
        return (
            dr.async_get(self.hass)
            .async_get_or_create(config_entry_id=self._entry.entry_id, identifiers={("test", name)}, name=name)
            .id
        )

    def _entity(self, entity_id: str, device_id: str | None, device_class: str | None = None, category=None) -> None:
        domain, object_id = entity_id.split(".")
        er.async_get(self.hass).async_get_or_create(
            domain,
            "test",
            entity_id,
            suggested_object_id=object_id,
            device_id=device_id,
            original_device_class=device_class,
            entity_category=category,
            config_entry=self._entry,
        )

    def add_plug(self, name: str, power: float, on: bool = True, with_sensor: bool = True) -> str:
        """A Zigbee/Shelly/Tuya plug: output + power sensor on the same device.

        The output is a light entity in the tests only because the switch domain is
        used by Power Control itself (its services cannot be replaced by fakes).
        """
        device = self._device(name)
        entity_id = f"light.{name}"
        self._entity(entity_id, device)
        sensor = None
        if with_sensor:
            sensor = f"sensor.{name}_power"
            self._entity(sensor, device, "power")
            # typical extra entities that must not confuse the detection
            self._entity(f"sensor.{name}_energy", device, "energy")
            self._entity(f"switch.{name}_child_lock", device, None, er.EntityCategory.CONFIG)
        self.loads[entity_id] = Load(entity_id, power, on, sensor)
        self.update()
        return entity_id

    def add_strip(self, name: str, powers: list[float]) -> list[str]:
        """A multi-socket strip: several switches and one power sensor for all."""
        device = self._device(name)
        sensor = f"sensor.{name}_power"
        self._entity(sensor, device, "power")
        ids = []
        for i, power in enumerate(powers, start=1):
            entity_id = f"light.{name}_socket_{i}"
            self._entity(entity_id, device)
            self.loads[entity_id] = Load(entity_id, power, True, None)
            ids.append(entity_id)
        self._strip = (sensor, ids)
        self.update()
        return ids

    def add_climate(self, name: str, power: float, mode: str = "cool") -> str:
        entity_id = f"climate.{name}"
        self._entity(entity_id, self._device(name))
        self.loads[entity_id] = Load(entity_id, power, True, None, mode)
        self.update()
        return entity_id

    # ------------------------------------------------------------- behaviour
    def total(self) -> float:
        return self.base + sum(load.power for load in self.loads.values() if load.on)

    def update(self) -> None:
        hass = self.hass
        for load in self.loads.values():
            if load.entity_id.startswith("climate."):
                hass.states.async_set(
                    load.entity_id, load.mode if load.on else "off", {"friendly_name": load.entity_id}
                )
            else:
                hass.states.async_set(load.entity_id, "on" if load.on else "off")
            if load.sensor:
                hass.states.async_set(
                    load.sensor,
                    str(load.power if load.on else 0),
                    {"unit_of_measurement": "W", "device_class": "power"},
                )
        if getattr(self, "_strip", None):
            sensor, ids = self._strip
            strip_power = sum(self.loads[i].power for i in ids if self.loads[i].on)
            hass.states.async_set(sensor, str(strip_power), {"unit_of_measurement": "W", "device_class": "power"})
        hass.states.async_set(
            METER,
            str(self.total()) if self.meter_available else "unavailable",
            {"unit_of_measurement": "W", "device_class": "power"},
        )

    def set_base(self, watts: float) -> None:
        self.base = watts
        self.update()

    def switch(self, entity_id: str, on: bool) -> None:
        self.loads[entity_id].on = on
        self.update()

    async def _handle_switch(self, call: ServiceCall) -> None:
        ids = call.data["entity_id"]
        for entity_id in [ids] if isinstance(ids, str) else ids:
            self.calls.append((call.domain, call.service, entity_id))
            if entity_id not in self.slow:
                self.loads[entity_id].on = call.service == "turn_on"
        self.update()

    async def _handle_climate(self, call: ServiceCall) -> None:
        ids = call.data["entity_id"]
        for entity_id in [ids] if isinstance(ids, str) else ids:
            mode = call.data["hvac_mode"]
            self.calls.append(("climate", f"set_hvac_mode:{mode}", entity_id))
            load = self.loads[entity_id]
            load.on = mode != "off"
            if load.on:
                load.mode = mode
        self.update()

    async def _handle_notify(self, call: ServiceCall) -> None:
        self.notifications.append((call.service, dict(call.data)))

    def off_calls(self) -> list[str]:
        return [e for d, s, e in self.calls if s in ("turn_off", "set_hvac_mode:off")]

    def on_calls(self) -> list[str]:
        return [
            e for d, s, e in self.calls if s == "turn_on" or (s.startswith("set_hvac_mode:") and not s.endswith(":off"))
        ]
