"""Notifications: push to phones, voice announcements, and an event on the bus."""

from __future__ import annotations

import asyncio
from datetime import time
import logging
from typing import Any

from homeassistant.const import STATE_HOME, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util, slugify

from .const import BUS_EVENT, SERVICE_TIMEOUT

_LOGGER = logging.getLogger(__name__)

TEXTS: dict[str, dict[str, str]] = {
    "it": {
        "title_warning": "⚡ Consumi elevati",
        "title_shed": "⚡ Carico spento",
        "title_restore": "✅ Consumi normali",
        "title_alert": "🚨 Rischio distacco",
        "warning": "Consumo {power} W, oltre la soglia di {shed} W: tra {minutes} min spengo {load}.",
        "warning_noload": "Consumo {power} W, oltre la soglia di {shed} W: tra {minutes} min inizio a spegnere i carichi.",
        "shed": "Consumo {power} W, oltre {shed} W: ho spento {load}{load_power}.",
        "shed_urgent": "Consumo {power} W, sopra il limite urgente di {urgent} W: ho spento subito {load}{load_power}.",
        "load_power": " (consumava {w} W)",
        "alert": "Consumo {power} W, oltre {shed} W, e non ci sono altri carichi da spegnere: spegni qualcosa a mano per evitare il distacco del contatore.",
        "restore": "Consumo sceso a {power} W: ho riacceso {load}.",
        "restore_all": "Consumo sceso a {power} W: ho riacceso {load}. Tutti i carichi sono di nuovo accesi.",
        "restore_skipped": "{loads}: non riacceso in automatico (escluso dal ripristino).",
    },
    "en": {
        "title_warning": "⚡ High consumption",
        "title_shed": "⚡ Load switched off",
        "title_restore": "✅ Consumption back to normal",
        "title_alert": "🚨 Risk of power cut",
        "warning": "Using {power} W, above the {shed} W threshold: in {minutes} min I will switch off {load}.",
        "warning_noload": "Using {power} W, above the {shed} W threshold: in {minutes} min I will start switching loads off.",
        "shed": "Using {power} W, above {shed} W: switched off {load}{load_power}.",
        "shed_urgent": "Using {power} W, above the urgent limit of {urgent} W: switched off {load}{load_power} immediately.",
        "load_power": " (it was using {w} W)",
        "alert": "Using {power} W, above {shed} W, and there is nothing left to switch off: switch something off by hand to avoid a power cut.",
        "restore": "Consumption down to {power} W: switched {load} back on.",
        "restore_all": "Consumption down to {power} W: switched {load} back on. All loads are on again.",
        "restore_skipped": "{loads}: not switched back on automatically (excluded from restore).",
    },
}

KIND_TITLE = {
    "warning": "title_warning",
    "shed": "title_shed",
    "restore": "title_restore",
    "alert": "title_alert",
}


def _parse_time(value: str | None, default: time) -> time:
    if not value:
        return default
    parsed = dt_util.parse_time(value)
    return parsed or default


class Notifier:
    """Sends Power Control messages where the user chose."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        services: list[str],
        only_home: bool,
        voice_players: list[str],
        tts_entity: str | None,
        voice_start: str | None,
        voice_end: str | None,
        events: list[str],
    ) -> None:
        self.hass = hass
        self.services = services
        self.only_home = only_home
        self.voice_players = voice_players
        self.tts_entity = tts_entity
        self.voice_start = _parse_time(voice_start, time(9, 0))
        self.voice_end = _parse_time(voice_end, time(22, 0))
        self.events = set(events)
        self.push_enabled = True
        self.voice_enabled = True
        self._lock = asyncio.Lock()  # one message at a time, in order

    # ------------------------------------------------------------------ texts
    def _lang(self) -> str:
        return "it" if (self.hass.config.language or "").lower().startswith("it") else "en"

    def text(self, key: str, **values: Any) -> str:
        texts = TEXTS[self._lang()]
        if key == "warning" and not values.get("load"):
            key = "warning_noload"
        if "load_power" in values:
            w = values.pop("load_power")
            values["load_power"] = texts["load_power"].format(w=w) if w else ""
        if values.get("power") is None:
            values["power"] = "?"
        return texts[key].format(**values)

    def title(self, kind: str) -> str:
        return TEXTS[self._lang()][KIND_TITLE[kind]]

    # ------------------------------------------------------------------ sending
    async def async_send(self, kind: str, message: str, *, critical: bool = False) -> None:
        """Fire the bus event and send the enabled notifications. Never raises."""
        title = self.title(kind)
        self.hass.bus.async_fire(BUS_EVENT, {"type": kind, "title": title, "message": message})
        if kind not in self.events:
            return
        async with self._lock:
            await self._async_deliver(title, message, critical)

    async def _async_deliver(self, title: str, message: str, critical: bool) -> None:
        if self.push_enabled:
            for service in self.services:
                if self.only_home and not self.is_home(service):
                    continue
                await self._call(
                    "notify",
                    service,
                    {
                        "title": title,
                        "message": message,
                        "data": {
                            "tag": "power_control",
                            "group": "power_control",
                            "push": {"interruption-level": "time-sensitive" if critical else "active"},
                            "ttl": 0,
                            "priority": "high",
                        },
                    },
                )
        if self.voice_enabled and self.voice_players and self.in_voice_window():
            await self._async_speak(message)

    async def _async_speak(self, message: str) -> None:
        reg = er.async_get(self.hass)
        alexa = [p for p in self.voice_players if (e := reg.async_get(p)) and e.platform == "alexa_media"]
        others = [p for p in self.voice_players if p not in alexa]
        if alexa:
            if self.hass.services.has_service("notify", "alexa_media"):
                await self._call(
                    "notify",
                    "alexa_media",
                    {"message": message, "target": alexa, "data": {"type": "announce"}},
                )
            else:
                _LOGGER.warning("Alexa Media Player not found: voice announcement skipped")
        if others:
            if self.tts_entity:
                await self._call(
                    "tts",
                    "speak",
                    {"entity_id": self.tts_entity, "media_player_entity_id": others, "message": message},
                )
            else:
                _LOGGER.warning("No text-to-speech engine chosen: voice announcement on %s skipped", others)

    async def _call(self, domain: str, service: str, data: dict[str, Any]) -> None:
        if not self.hass.services.has_service(domain, service):
            _LOGGER.warning("Service %s.%s not found: notification skipped", domain, service)
            return
        try:
            async with asyncio.timeout(SERVICE_TIMEOUT):
                await self.hass.services.async_call(domain, service, data, blocking=True)
        except Exception as err:  # noqa: BLE001 - a failed notification must not stop the engine
            _LOGGER.warning("Notification %s.%s failed: %s", domain, service, err)

    # ------------------------------------------------------------------ conditions
    def in_voice_window(self) -> bool:
        now = dt_util.now().time()
        start, end = self.voice_start, self.voice_end
        if start == end:
            return True
        if start < end:
            return start <= now < end
        return now >= start or now < end  # overnight window

    def is_home(self, service: str) -> bool:
        """Whether the person owning the phone behind notify.<service> is at home.

        Unknown phones (not a mobile app, or no tracker) always get the notification.
        """
        if not service.startswith("mobile_app_"):
            return True
        slug = service.removeprefix("mobile_app_")
        reg = er.async_get(self.hass)
        for entry in self.hass.config_entries.async_entries("mobile_app"):
            if slugify(entry.data.get("device_name") or "") != slug:
                continue
            trackers = [
                e.entity_id
                for e in er.async_entries_for_config_entry(reg, entry.entry_id)
                if e.domain == "device_tracker"
            ]
            if not trackers:
                return True
            # Prefer the person using this phone (combines all their trackers)
            for person in self.hass.states.async_all("person"):
                if set(person.attributes.get("device_trackers", [])) & set(trackers):
                    if person.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                        return True
                    return person.state == STATE_HOME
            states = [self.hass.states.get(t) for t in trackers]
            known = [s for s in states if s and s.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)]
            if not known:
                return True
            return any(s.state == STATE_HOME for s in known)
        return True
