"""Behaviour of the load-shedding engine in a simulated house."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components.power_control.const import DOMAIN
from custom_components.power_control.engine import PowerControlEngine

from .house import METER, House

FAST = {
    "shed_interval": 0,
    "urgent_delay": 10,
    "restore_delay": 60,
    "cancel_delay": 60,
    "warning_minutes": 2,
    "min_active_w": 15,
    "notify_services": ["mobile_app_phone_luca"],
    "notify_only_home": False,
    "notify_events": ["warning", "shed", "restore", "alert"],
}


async def setup(hass: HomeAssistant, house: House, loads: list[str], **options: Any) -> PowerControlEngine:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"meter": METER, "contract_kw": 3},
        options={**FAST, "loads": loads, **options},
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


async def run(hass: HomeAssistant, engine: PowerControlEngine) -> None:
    engine._evaluate()
    await hass.async_block_till_done(wait_background_tasks=True)


async def advance(
    hass: HomeAssistant, engine: PowerControlEngine, freezer: FrozenDateTimeFactory, **delta: float
) -> None:
    """Let pending state changes reach the engine, move the clock, then tick."""
    await hass.async_block_till_done(wait_background_tasks=True)
    freezer.tick(timedelta(**delta))
    await run(hass, engine)


@pytest.fixture
async def house(hass: HomeAssistant) -> House:
    h = House(hass)
    await h.async_setup()
    return h


async def test_default_thresholds_for_3kw(hass: HomeAssistant, house: House) -> None:
    engine = await setup(hass, house, [])
    assert (engine.settings.shed_w, engine.settings.urgent_w, engine.settings.restore_w) == (3300, 3900, 2800)
    assert float(hass.states.get("number.power_control_shed_threshold").state) == 3300
    assert hass.states.get("sensor.power_control_status").state == "ok"
    assert hass.states.get("switch.power_control_enabled").state == "on"


async def test_switch_off_in_order_after_delay_then_back_on(
    hass: HomeAssistant, house: House, freezer: FrozenDateTimeFactory
) -> None:
    oven = house.add_plug("forno", 1200)
    washer = house.add_plug("lavatrice", 1000)
    dryer = house.add_plug("asciugatrice", 900)
    house.set_base(500)  # 500 + 3100 = 3600 W > 3300 W
    engine = await setup(hass, house, [washer, dryer, oven])

    await run(hass, engine)
    assert engine.status == "over_threshold"
    assert engine.deadline is not None
    assert house.off_calls() == []

    await advance(hass, engine, freezer, minutes=58)  # 2 minutes before: warning
    assert engine.status == "warning"
    assert "lavatrice" in house.notifications[-1][1]["message"]
    assert house.off_calls() == []

    await advance(hass, engine, freezer, minutes=2, seconds=5)
    # The first in the list goes off; 3600 - 1000 = 2600 W is enough, so it stops there
    assert house.off_calls() == [washer]
    assert hass.states.get("sensor.power_control_loads_off").state == "1"
    assert engine.status == "loads_off"

    # Below the restore threshold (2800 W) but 2600 + 1000 = 3600 W would go over again
    await advance(hass, engine, freezer, minutes=2)
    assert house.on_calls() == []

    house.switch(oven, False)  # oven finished: 1400 W
    await advance(hass, engine, freezer, seconds=10)
    await advance(hass, engine, freezer, seconds=61)
    assert house.on_calls() == [washer]
    assert engine.shed == []
    assert engine.status == "ok"
    assert (
        "Tutti i carichi" in house.notifications[-1][1]["message"]
        or "All loads" in house.notifications[-1][1]["message"]
    )


async def test_short_peak_cancels_the_countdown(
    hass: HomeAssistant, house: House, freezer: FrozenDateTimeFactory
) -> None:
    kettle = house.add_plug("bollitore", 2000)
    house.set_base(1500)
    engine = await setup(hass, house, [kettle])
    await run(hass, engine)
    assert engine.deadline is not None
    house.switch(kettle, False)
    await advance(hass, engine, freezer, seconds=30)
    assert engine.deadline is not None  # not yet: must stay below for cancel_delay
    await advance(hass, engine, freezer, seconds=31)
    assert engine.deadline is None
    assert engine.status == "ok"
    assert house.off_calls() == []


async def test_urgent_threshold_acts_without_waiting(
    hass: HomeAssistant, house: House, freezer: FrozenDateTimeFactory
) -> None:
    oven = house.add_plug("forno", 2000)
    heater = house.add_plug("stufetta", 1800)
    house.set_base(400)  # 4200 W > 3900 W urgent
    engine = await setup(hass, house, [heater, oven])
    await run(hass, engine)
    assert house.off_calls() == []  # a kettle-like peak is ignored for urgent_delay
    await advance(hass, engine, freezer, seconds=11)
    assert house.off_calls() == [heater]
    assert house.notifications[-1][1]["data"]["push"]["interruption-level"] == "time-sensitive"


async def test_meter_unavailable_never_switches_anything_on(
    hass: HomeAssistant, house: House, freezer: FrozenDateTimeFactory
) -> None:
    washer = house.add_plug("lavatrice", 2000)
    house.set_base(1800)
    engine = await setup(hass, house, [washer], restore_delay=0)
    await advance(hass, engine, freezer, minutes=61)
    assert house.off_calls() == [washer]

    house.meter_available = False
    house.update()
    for _ in range(5):
        await advance(hass, engine, freezer, minutes=5)
    assert house.on_calls() == []
    assert engine.status == "meter_unavailable"
    assert len(engine.shed) == 1


async def test_idle_loads_are_skipped(hass: HomeAssistant, house: House, freezer: FrozenDateTimeFactory) -> None:
    tv = house.add_plug("tv", 3)  # on but in standby
    oven = house.add_plug("forno", 2000)
    house.set_base(1500)
    engine = await setup(hass, house, [tv, oven])
    await advance(hass, engine, freezer, minutes=61)
    assert house.off_calls() == [oven]


async def test_multi_socket_strip_uses_state_only_unless_chosen(
    hass: HomeAssistant, house: House, freezer: FrozenDateTimeFactory
) -> None:
    sockets = house.add_strip("ciabatta", [10, 1500])
    engine = await setup(hass, house, sockets)
    # One meter for two outputs: not assigned automatically to either
    assert engine.resolve_sensor(sockets[0]) is None
    assert engine.resolve_sensor(sockets[1]) is None
    plug = house.add_plug("lavatrice", 1000)
    assert engine.resolve_sensor(plug) == "sensor.lavatrice_power"


async def test_chosen_sensor_is_used(hass: HomeAssistant, house: House) -> None:
    sockets = house.add_strip("ciabatta", [1500])
    engine = await setup(hass, house, sockets, load_sensors={sockets[0]: "sensor.ciabatta_power"})
    assert engine.resolve_sensor(sockets[0]) == "sensor.ciabatta_power"
    assert engine.load_power(sockets[0]) == 1500


async def test_switched_on_by_hand_is_forgotten(
    hass: HomeAssistant, house: House, freezer: FrozenDateTimeFactory
) -> None:
    washer = house.add_plug("lavatrice", 2000)
    house.set_base(1800)
    engine = await setup(hass, house, [washer])
    await advance(hass, engine, freezer, minutes=61)
    assert len(engine.shed) == 1
    house.switch(washer, True)
    await hass.async_block_till_done()
    assert engine.shed == []


async def test_excluded_load_is_not_switched_back_on(
    hass: HomeAssistant, house: House, freezer: FrozenDateTimeFactory
) -> None:
    washer = house.add_plug("lavatrice", 2000)
    house.set_base(1800)
    engine = await setup(hass, house, [washer], no_restore=[washer])
    await advance(hass, engine, freezer, minutes=61)
    house.set_base(300)
    await advance(hass, engine, freezer, seconds=10)
    await advance(hass, engine, freezer, seconds=61)
    assert house.on_calls() == []
    assert engine.shed == []
    assert "escluso" in house.notifications[-1][1]["message"] or "excluded" in house.notifications[-1][1]["message"]


async def test_climate_gets_its_mode_back(hass: HomeAssistant, house: House, freezer: FrozenDateTimeFactory) -> None:
    ac = house.add_climate("condizionatore", 1200, mode="heat")
    house.set_base(2500)
    engine = await setup(hass, house, [ac])
    await advance(hass, engine, freezer, minutes=61)
    assert house.off_calls() == [ac]
    house.set_base(800)
    await advance(hass, engine, freezer, seconds=10)
    await advance(hass, engine, freezer, seconds=61)
    assert ("climate", "set_hvac_mode:heat", ac) in house.calls
    assert hass.states.get(ac).state == "heat"


async def test_nothing_left_to_switch_off_alerts_once(
    hass: HomeAssistant, house: House, freezer: FrozenDateTimeFactory
) -> None:
    house.set_base(3600)  # nothing in the list is on
    engine = await setup(hass, house, [house.add_plug("lavatrice", 1000, on=False)])
    await advance(hass, engine, freezer, minutes=61)
    alerts = [n for n in house.notifications if "🚨" in n[1]["title"]]
    assert len(alerts) == 1
    for _ in range(3):
        await advance(hass, engine, freezer, minutes=61)
    assert len([n for n in house.notifications if "🚨" in n[1]["title"]]) == 1


async def test_disabled_does_nothing(hass: HomeAssistant, house: House, freezer: FrozenDateTimeFactory) -> None:
    washer = house.add_plug("lavatrice", 2000)
    house.set_base(1800)
    engine = await setup(hass, house, [washer])
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.power_control_enabled"}, blocking=True)
    assert engine.enabled is False
    await advance(hass, engine, freezer, minutes=61)
    assert house.off_calls() == []
    assert engine.status == "disabled"
    assert hass.states.get("sensor.power_control_status").state == "disabled"


async def test_survives_a_restart(hass: HomeAssistant, house: House, freezer: FrozenDateTimeFactory) -> None:
    washer = house.add_plug("lavatrice", 2000)
    house.set_base(1800)
    engine = await setup(hass, house, [washer])
    await advance(hass, engine, freezer, minutes=61)
    assert len(engine.shed) == 1
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    engine = entry.runtime_data
    assert [i["entity_id"] for i in engine.shed] == [washer]
    house.set_base(300)
    await advance(hass, engine, freezer, seconds=10)
    await advance(hass, engine, freezer, seconds=61)
    assert house.on_calls() == [washer]


async def test_restore_now_button(hass: HomeAssistant, house: House, freezer: FrozenDateTimeFactory) -> None:
    washer = house.add_plug("lavatrice", 2000)
    house.set_base(1800)
    engine = await setup(hass, house, [washer])
    await advance(hass, engine, freezer, minutes=61)
    entity_id = "button.power_control_restore_now"
    await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert house.on_calls() == [washer]


async def test_thresholds_must_be_in_order(hass: HomeAssistant, house: House) -> None:
    await setup(hass, house, [])
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "number", "set_value", {"entity_id": "number.power_control_restore_threshold", "value": 3500}, blocking=True
        )
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.power_control_shed_threshold", "value": 3500}, blocking=True
    )
    assert float(hass.states.get("number.power_control_shed_threshold").state) == 3500


async def test_notifications_only_to_people_at_home(
    hass: HomeAssistant, house: House, freezer: FrozenDateTimeFactory
) -> None:
    for name in ("phone_luca", "phone_elisa"):
        entry = MockConfigEntry(domain="mobile_app", data={"device_name": name.replace("_", " ")})
        entry.add_to_hass(hass)
        er.async_get(hass).async_get_or_create(
            "device_tracker", "mobile_app", name, suggested_object_id=name, config_entry=entry
        )
    hass.states.async_set("device_tracker.phone_luca", "home")
    hass.states.async_set("device_tracker.phone_elisa", "not_home")
    washer = house.add_plug("lavatrice", 2000)
    house.set_base(1800)
    engine = await setup(
        hass,
        house,
        [washer],
        notify_services=["mobile_app_phone_luca", "mobile_app_phone_elisa"],
        notify_only_home=True,
    )
    await advance(hass, engine, freezer, minutes=61)
    services = {s for s, _ in house.notifications}
    assert services == {"mobile_app_phone_luca"}


async def test_event_on_the_bus(hass: HomeAssistant, house: House, freezer: FrozenDateTimeFactory) -> None:
    events = []
    hass.bus.async_listen("power_control_event", lambda e: events.append(e.data))
    washer = house.add_plug("lavatrice", 2000)
    house.set_base(1800)
    engine = await setup(hass, house, [washer], notify_services=[])
    await advance(hass, engine, freezer, minutes=58)
    await advance(hass, engine, freezer, minutes=3)
    await hass.async_block_till_done()
    assert [e["type"] for e in events] == ["warning", "shed"]


async def test_late_confirmation_does_not_lose_the_load(
    hass: HomeAssistant, house: House, freezer: FrozenDateTimeFactory
) -> None:
    """A Zigbee plug may keep reporting "on" for a moment after the switch-off."""
    washer = house.add_plug("lavatrice", 2000)
    house.set_base(1800)
    house.slow.add(washer)
    engine = await setup(hass, house, [washer], shed_interval=0)
    await advance(hass, engine, freezer, minutes=61)
    assert [i["entity_id"] for i in engine.shed] == [washer]
    hass.states.async_set(washer, "on", {"linkquality": 120})  # stale report, still on
    await hass.async_block_till_done()
    assert [i["entity_id"] for i in engine.shed] == [washer]
    house.slow.clear()
    house.switch(washer, False)  # the plug finally confirms
    await hass.async_block_till_done()
    house.switch(washer, True)  # now someone really switches it on
    await hass.async_block_till_done()
    assert engine.shed == []


async def test_disabling_stops_a_running_switch_off(
    hass: HomeAssistant, house: House, freezer: FrozenDateTimeFactory
) -> None:
    washer = house.add_plug("lavatrice", 1000)
    dryer = house.add_plug("asciugatrice", 1000)
    house.set_base(2500)  # 4500 W: one load off is not enough
    engine = await setup(hass, house, [washer, dryer])

    @callback
    def disable_on_first_switch_off(event) -> None:
        if event.data["entity_id"] == washer and event.data["new_state"].state == "off":
            engine.async_set_enabled(False)

    hass.bus.async_listen("state_changed", disable_on_first_switch_off)
    await advance(hass, engine, freezer, minutes=61)
    assert house.off_calls() == [washer]
    assert engine.status == "disabled"


async def test_meter_not_a_number_is_unavailable(hass: HomeAssistant, house: House) -> None:
    engine = await setup(hass, house, [])
    hass.states.async_set(METER, "nan", {"unit_of_measurement": "W"})
    await run(hass, engine)
    assert engine.power is None
    assert engine.status == "meter_unavailable"


async def test_slow_notification_does_not_block_the_engine(
    hass: HomeAssistant, house: House, freezer: FrozenDateTimeFactory
) -> None:
    release = asyncio.Event()

    async def stuck(call) -> None:
        await release.wait()

    hass.services.async_register("notify", "mobile_app_slow_phone", stuck)
    washer = house.add_plug("lavatrice", 2000)
    house.set_base(1800)
    engine = await setup(hass, house, [washer], notify_services=["mobile_app_slow_phone"])
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=58))
    engine._evaluate()
    await hass.async_block_till_done()
    assert engine.status == "warning"
    house.set_base(2500)  # now above the urgent threshold
    freezer.tick(timedelta(seconds=11))
    engine._evaluate()
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=11))
    engine._evaluate()
    await hass.async_block_till_done()
    assert house.off_calls() == [washer]
    release.set()
    await hass.async_block_till_done(wait_background_tasks=True)
