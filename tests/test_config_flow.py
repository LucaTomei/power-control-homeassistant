"""Setup and settings flows."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.power_control.const import DOMAIN

from .house import METER, House


async def _house(hass: HomeAssistant) -> House:
    house = House(hass)
    await house.async_setup()
    return house


async def test_setup_flow(hass: HomeAssistant) -> None:
    await _house(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"meter": "sensor.does_not_exist", "contract_kw": 3}
    )
    assert result["errors"] == {"meter": "meter_not_found"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"meter": METER, "contract_kw": 4.5})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"meter": METER, "contract_kw": 4.5}
    assert result["options"]["loads"] == []
    await hass.async_block_till_done()
    engine = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    assert (engine.settings.shed_w, engine.settings.urgent_w, engine.settings.restore_w) == (4950, 5850, 4200)

    # only one Power Control
    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert again["type"] is FlowResultType.ABORT


async def _options(hass: HomeAssistant, entry, step: str):
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["type"] is FlowResultType.MENU
    assert "save" not in flow["menu_options"]
    return await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": step})


async def _done(hass: HomeAssistant, result) -> None:
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_options_flow_every_step(hass: HomeAssistant) -> None:
    house = await _house(hass)
    washer = house.add_plug("lavatrice", 1000)
    oven = house.add_plug("forno", 1500)
    sockets = house.add_strip("ciabatta", [100, 200])

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"meter": METER, "contract_kw": 3})
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    # sensors before loads: explains what to do
    step = await _options(hass, entry, "sensors")
    assert step["type"] is FlowResultType.ABORT
    assert step["reason"] == "no_loads"

    # loads, in the order chosen (duplicates removed): saved as soon as confirmed
    step = await _options(hass, entry, "loads")
    assert step["step_id"] == "loads"
    await _done(
        hass,
        await hass.config_entries.options.async_configure(step["flow_id"], {"loads": [oven, *sockets, washer, oven]}),
    )
    assert entry.options["loads"] == [oven, *sockets, washer]

    # sensors: one field per load, automatic sensor shown in the description
    step = await _options(hass, entry, "sensors")
    keys = [str(k) for k in step["data_schema"].schema]
    assert keys[0].startswith("1. ") and oven in keys[0]
    assert "sensor.forno_power" in step["description_placeholders"]["auto"]
    socket_key = next(k for k in keys if sockets[1] in k)
    await _done(
        hass,
        await hass.config_entries.options.async_configure(
            step["flow_id"], {socket_key: "sensor.ciabatta_power", "no_restore": [washer]}
        ),
    )
    assert entry.options["load_sensors"] == {sockets[1]: "sensor.ciabatta_power"}
    assert entry.options["no_restore"] == [washer]

    # sensors alone: saved as soon as confirmed
    step = await _options(hass, entry, "sensors")
    assert "sensor.forno_power" in step["description_placeholders"]["auto"]
    keys = [str(k) for k in step["data_schema"].schema]
    socket_key = next(k for k in keys if sockets[1] in k)
    await _done(
        hass,
        await hass.config_entries.options.async_configure(
            step["flow_id"], {socket_key: "sensor.ciabatta_power", "no_restore": [washer, oven]}
        ),
    )
    assert entry.options["no_restore"] == [washer, oven]

    # notifications
    step = await _options(hass, entry, "notifications")
    values = [o["value"] for o in step["data_schema"].schema["notify_services"].config["options"]]
    assert values[:2] == ["mobile_app_phone_elisa", "mobile_app_phone_luca"]
    await _done(
        hass,
        await hass.config_entries.options.async_configure(
            step["flow_id"],
            {
                "notify_services": ["mobile_app_phone_luca"],
                "notify_only_home": True,
                "notify_events": ["shed", "alert"],
                "voice_start": "08:00:00",
                "voice_end": "21:00:00",
            },
        ),
    )

    # advanced
    step = await _options(hass, entry, "advanced")
    await _done(
        hass,
        await hass.config_entries.options.async_configure(
            step["flow_id"],
            {
                "meter": METER,
                "warning_minutes": 5,
                "urgent_delay": 15,
                "shed_interval": 30,
                "restore_delay": 120,
                "cancel_delay": 90,
                "min_active_w": 20,
            },
        ),
    )

    opts = entry.options
    assert opts["loads"] == [oven, *sockets, washer]
    assert opts["load_sensors"] == {sockets[1]: "sensor.ciabatta_power"}
    assert opts["notify_services"] == ["mobile_app_phone_luca"]
    assert opts["notify_events"] == ["shed", "alert"]
    assert opts["shed_interval"] == 30

    # reloaded with the new settings
    engine = entry.runtime_data
    assert engine.settings.loads == [oven, *sockets, washer]
    assert engine.resolve_sensor(sockets[1]) == "sensor.ciabatta_power"
    assert engine.resolve_sensor(sockets[0]) is None
    assert engine.settings.shed_interval == 30
    assert engine.notifier.events == {"shed", "alert"}

    # removing a load drops its sensor and exclusion
    step = await _options(hass, entry, "loads")
    await _done(hass, await hass.config_entries.options.async_configure(step["flow_id"], {"loads": [oven]}))
    assert entry.options["loads"] == [oven]
    assert entry.options["load_sensors"] == {}
    assert entry.options["no_restore"] == [oven]


async def test_removed_and_added_again_restores_everything(hass: HomeAssistant) -> None:
    house = await _house(hass)
    washer = house.add_plug("lavatrice", 1000)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    await hass.config_entries.flow.async_configure(result["flow_id"], {"meter": METER, "contract_kw": 3})
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    step = await _options(hass, entry, "notifications")
    await _done(
        hass,
        await hass.config_entries.options.async_configure(
            step["flow_id"], {"notify_services": ["mobile_app_phone_luca"], "notify_events": ["alert"]}
        ),
    )
    step = await _options(hass, entry, "advanced")
    await _done(
        hass,
        await hass.config_entries.options.async_configure(
            step["flow_id"],
            {
                "meter": METER,
                "warning_minutes": 5,
                "urgent_delay": 15,
                "shed_interval": 30,
                "restore_delay": 120,
                "cancel_delay": 90,
                "min_active_w": 20,
            },
        ),
    )
    step = await _options(hass, entry, "loads")
    await _done(hass, await hass.config_entries.options.async_configure(step["flow_id"], {"loads": [washer]}))
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.power_control_shed_threshold", "value": 3500}, blocking=True
    )
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.power_control_voice_notifications"}, blocking=True
    )

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("sensor.new_meter", "1000", {"unit_of_measurement": "W"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert "restore_previous" in [str(k) for k in result["data_schema"].schema]
    assert result["description_placeholders"]["loads"] == "1"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"meter": "sensor.new_meter", "contract_kw": 3, "restore_previous": True}
    )
    assert result["data"] == {"meter": "sensor.new_meter", "contract_kw": 3}
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.options["loads"] == [washer]
    assert entry.options["notify_services"] == ["mobile_app_phone_luca"]
    assert entry.options["notify_events"] == ["alert"]
    assert entry.options["shed_interval"] == 30
    assert "meter" not in entry.options
    engine = entry.runtime_data
    assert engine.settings.meter == "sensor.new_meter"
    assert engine.settings.shed_w == 3500
    assert engine.notifier.voice_enabled is False
    assert float(hass.states.get("number.power_control_shed_threshold").state) == 3500
    assert hass.states.get("switch.power_control_voice_notifications").state == "off"


async def test_added_again_from_scratch(hass: HomeAssistant) -> None:
    house = await _house(hass)
    washer = house.add_plug("lavatrice", 1000)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    await hass.config_entries.flow.async_configure(result["flow_id"], {"meter": METER, "contract_kw": 3})
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    step = await _options(hass, entry, "loads")
    await _done(hass, await hass.config_entries.options.async_configure(step["flow_id"], {"loads": [washer]}))
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.power_control_shed_threshold", "value": 3500}, blocking=True
    )
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"meter": METER, "contract_kw": 3, "restore_previous": False}
    )
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.options["loads"] == []
    assert entry.runtime_data.settings.shed_w == 3300


async def test_presence_step(hass: HomeAssistant) -> None:
    await _house(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    await hass.config_entries.flow.async_configure(result["flow_id"], {"meter": METER, "contract_kw": 3})
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    step = await _options(hass, entry, "presence")
    assert step["type"] is FlowResultType.ABORT
    assert step["reason"] == "no_phones"

    step = await _options(hass, entry, "notifications")
    await _done(
        hass,
        await hass.config_entries.options.async_configure(
            step["flow_id"], {"notify_services": ["mobile_app_phone_luca", "mobile_app_phone_elisa"]}
        ),
    )
    step = await _options(hass, entry, "presence")
    assert [str(k) for k in step["data_schema"].schema] == [
        "notify.mobile_app_phone_luca",
        "notify.mobile_app_phone_elisa",
    ]
    assert "notify.mobile_app_phone_luca: —" in step["description_placeholders"]["auto"]
    await _done(
        hass,
        await hass.config_entries.options.async_configure(
            step["flow_id"], {"notify.mobile_app_phone_luca": "person.luca"}
        ),
    )
    assert entry.options["notify_presence"] == {"mobile_app_phone_luca": "person.luca"}
    assert entry.runtime_data.notifier.presence == {"mobile_app_phone_luca": "person.luca"}

    # a phone removed from the notifications loses its presence sensor
    step = await _options(hass, entry, "notifications")
    await _done(
        hass,
        await hass.config_entries.options.async_configure(
            step["flow_id"], {"notify_services": ["mobile_app_phone_elisa"]}
        ),
    )
    assert entry.options["notify_presence"] == {}


async def test_restore_with_another_contract_suggests_new_thresholds(hass: HomeAssistant) -> None:
    house = await _house(hass)
    washer = house.add_plug("lavatrice", 1000)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    await hass.config_entries.flow.async_configure(result["flow_id"], {"meter": METER, "contract_kw": 3})
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    step = await _options(hass, entry, "loads")
    await _done(hass, await hass.config_entries.options.async_configure(step["flow_id"], {"loads": [washer]}))
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.power_control_shed_threshold", "value": 3500}, blocking=True
    )
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    await hass.config_entries.flow.async_configure(
        result["flow_id"], {"meter": METER, "contract_kw": 4.5, "restore_previous": True}
    )
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.options["loads"] == [washer]
    engine = entry.runtime_data
    assert (engine.settings.shed_w, engine.settings.urgent_w, engine.settings.restore_w) == (4950, 5850, 4200)
