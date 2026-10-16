"""Notifications: voice routing, time window, texts."""

from __future__ import annotations

from datetime import datetime

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.power_control.notifier import Notifier


def _notifier(hass: HomeAssistant, **kwargs) -> Notifier:
    base = {
        "services": [],
        "only_home": False,
        "voice_players": [],
        "tts_entity": None,
        "voice_start": "09:00:00",
        "voice_end": "22:00:00",
        "events": ["warning", "shed", "restore", "alert"],
    }
    return Notifier(hass, **{**base, **kwargs})


async def test_voice_goes_to_alexa_and_tts(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    await hass.config.async_set_time_zone("Europe/Rome")
    freezer.move_to(datetime(2026, 10, 16, 12, 0, tzinfo=dt_util.get_default_time_zone()))
    entry = MockConfigEntry(domain="alexa_media")
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "media_player", "alexa_media", "echo", suggested_object_id="echo_salone", config_entry=entry
    )
    alexa = async_mock_service(hass, "notify", "alexa_media")
    tts = async_mock_service(hass, "tts", "speak")
    notifier = _notifier(hass, voice_players=["media_player.echo_salone", "media_player.nest"], tts_entity="tts.google")
    await notifier.async_send("shed", "ciao")
    assert alexa[0].data["target"] == ["media_player.echo_salone"]
    assert tts[0].data["media_player_entity_id"] == ["media_player.nest"]

    notifier.voice_enabled = False
    await notifier.async_send("shed", "ciao")
    assert len(alexa) == 1


async def test_voice_window(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    await hass.config.async_set_time_zone("Europe/Rome")
    tz = dt_util.get_default_time_zone()
    day = _notifier(hass)
    night = _notifier(hass, voice_start="22:00:00", voice_end="07:00:00")
    always = _notifier(hass, voice_start="00:00:00", voice_end="00:00:00")
    freezer.move_to(datetime(2026, 10, 16, 23, 30, tzinfo=tz))
    assert (day.in_voice_window(), night.in_voice_window(), always.in_voice_window()) == (False, True, True)
    freezer.move_to(datetime(2026, 10, 16, 10, 0, tzinfo=tz))
    assert (day.in_voice_window(), night.in_voice_window()) == (True, False)


async def test_events_filter_and_language(hass: HomeAssistant) -> None:
    calls = async_mock_service(hass, "notify", "mobile_app_phone")
    notifier = _notifier(hass, services=["mobile_app_phone"], events=["alert"])
    await notifier.async_send("shed", "x")
    assert calls == []
    await notifier.async_send("alert", "y", critical=True)
    assert calls[0].data["title"].startswith("🚨")
    assert calls[0].data["data"]["push"]["interruption-level"] == "time-sensitive"

    hass.config.language = "it"
    assert notifier.text("shed", power=3500, shed=3300, urgent=3900, load="Forno", load_power=1200) == (
        "Consumo 3500 W, oltre 3300 W: ho spento Forno (consumava 1200 W)."
    )
    hass.config.language = "en"
    assert notifier.text("warning", power=3500, shed=3300, minutes=2, load=None).startswith("Using 3500 W")
