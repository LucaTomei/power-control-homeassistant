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


async def test_options_flow_every_step(hass: HomeAssistant) -> None:
    house = await _house(hass)
    washer = house.add_plug("lavatrice", 1000)
    oven = house.add_plug("forno", 1500)
    sockets = house.add_strip("ciabatta", [100, 200])

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"meter": METER, "contract_kw": 3})
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["type"] is FlowResultType.MENU
    assert "save" in flow["menu_options"]

    # sensors before loads: explains what to do
    step = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "sensors"})
    assert step["errors"] == {"base": "no_loads"}
    step = await hass.config_entries.options.async_configure(flow["flow_id"], {})
    assert step["type"] is FlowResultType.MENU

    # loads, in the order chosen (duplicates removed)
    step = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "loads"})
    assert step["step_id"] == "loads"
    step = await hass.config_entries.options.async_configure(flow["flow_id"], {"loads": [oven, *sockets, washer, oven]})
    assert step["type"] is FlowResultType.MENU

    # sensors: one field per load, automatic sensor shown in the description
    step = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "sensors"})
    assert step["step_id"] == "sensors"
    keys = [str(k) for k in step["data_schema"].schema]
    assert keys[0].startswith("1. ") and oven in keys[0]
    assert "sensor.forno_power" in step["description_placeholders"]["auto"]
    socket_key = next(k for k in keys if sockets[1] in k)
    step = await hass.config_entries.options.async_configure(
        flow["flow_id"], {socket_key: "sensor.ciabatta_power", "no_restore": [washer]}
    )
    assert step["type"] is FlowResultType.MENU

    # notifications
    step = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "notifications"})
    values = [o["value"] for o in step["data_schema"].schema["notify_services"].config["options"]]
    assert values[:2] == ["mobile_app_phone_elisa", "mobile_app_phone_luca"]
    step = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {
            "notify_services": ["mobile_app_phone_luca"],
            "notify_only_home": True,
            "notify_events": ["shed", "alert"],
            "voice_start": "08:00:00",
            "voice_end": "21:00:00",
        },
    )
    assert step["type"] is FlowResultType.MENU

    # advanced
    step = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "advanced"})
    step = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {
            "meter": METER,
            "warning_minutes": 5,
            "urgent_delay": 15,
            "shed_interval": 30,
            "restore_delay": 120,
            "cancel_delay": 90,
            "min_active_w": 20,
        },
    )
    assert step["type"] is FlowResultType.MENU

    done = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "save"})
    assert done["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    opts = entry.options
    assert opts["loads"] == [oven, *sockets, washer]
    assert opts["load_sensors"] == {sockets[1]: "sensor.ciabatta_power"}
    assert opts["no_restore"] == [washer]
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
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "loads"})
    await hass.config_entries.options.async_configure(flow["flow_id"], {"loads": [oven]})
    await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "save"})
    await hass.async_block_till_done()
    assert entry.options["load_sensors"] == {}
    assert entry.options["no_restore"] == []
