"""The dashboard cards use only entities that exist and templates that render."""

from __future__ import annotations

from pathlib import Path
import re

from freezegun.api import FrozenDateTimeFactory
import yaml

from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template

from .house import House
from .test_engine import advance, setup

DASHBOARD = Path(__file__).parent.parent / "dashboard"


def _cards(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _cards(value)
    elif isinstance(node, list):
        for value in node:
            yield from _cards(value)


def _load() -> list[dict]:
    view = yaml.safe_load((DASHBOARD / "power-control-view.yaml").read_text())
    alert = yaml.safe_load((DASHBOARD / "power-control-alert-card.yaml").read_text())
    return [*view, *alert]


def _render_all(hass: HomeAssistant) -> list[str]:
    out = []
    for card in _cards(_load()):
        if card.get("type") == "markdown":
            out.append(Template(card["content"], hass).async_render(parse_result=False))
    return out


async def test_entities_in_the_dashboard_exist(hass: HomeAssistant) -> None:
    house = House(hass)
    await house.async_setup()
    await setup(hass, house, [])
    text = "\n".join(yaml.safe_dump(c) for c in _load())
    used = set(re.findall(r"\b(?:sensor|switch|number|button)\.power_control_[a-z_]+", text))
    assert used
    for entity_id in used:
        assert hass.states.get(entity_id) is not None, entity_id


async def test_templates_render_in_every_situation(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    house = House(hass)
    await house.async_setup()
    hass.config.language = "it"
    # the generic view uses a placeholder meter: point it to the fake one
    hass.states.async_set("sensor.YOUR_HOUSE_POWER".lower(), "0")
    washer = house.add_plug("lavatrice", 2000)
    oven = house.add_plug("forno", 600)
    house.set_base(500)
    engine = await setup(hass, house, [washer, oven], no_restore=[oven])

    texts = _render_all(hass)
    assert any("| 1 | lavatrice | 2000 W | ⚡ in uso |" in t for t in texts)
    assert any("🔒 non si riaccende da solo" in t for t in texts)
    assert any("Ancora nessun intervento" in t for t in texts)

    house.set_base(1500)  # 4100 W: over the threshold
    await advance(hass, engine, freezer, seconds=1)
    texts = _render_all(hass)
    assert any("Consumi alti: 4100 W" in t and "spengo **lavatrice**" in t for t in texts)

    await advance(hass, engine, freezer, minutes=61)
    texts = _render_all(hass)
    assert any("Carichi spenti per i consumi" in t and "lavatrice" in t for t in texts)
    assert any("⛔ spento da Power Control" in t for t in texts)
    assert any("Ultimo evento" in t and "spento subito lavatrice" in t for t in texts)  # 4100 W > urgent

    house.loads[washer].on = False
    hass.states.async_set(washer, "unavailable")
    texts = _render_all(hass)
    assert all("TemplateError" not in t for t in texts)
