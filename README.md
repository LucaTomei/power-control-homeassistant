# Power Control for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![GitHub Release](https://img.shields.io/github/v/release/LucaTomei/power-control-homeassistant)](https://github.com/LucaTomei/power-control-homeassistant/releases)
[![CI](https://github.com/LucaTomei/power-control-homeassistant/actions/workflows/ci.yml/badge.svg)](https://github.com/LucaTomei/power-control-homeassistant/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/LucaTomei/power-control-homeassistant)](LICENSE)

**Switch loads off before the meter trips, and back on when there is room again.**

Power Control watches the power of the whole house. When it stays above your
threshold it warns you, then switches loads off **in the order you choose**, one
at a time, until the consumption is back under the limit. When consumption is
normal again it switches them back on, last one first, **only if each one fits**.

Everything is set up from the Home Assistant UI: no YAML, no templates, no extra
cards needed.

🇮🇹 [Italiano più sotto](#-in-italiano)

## ✨ Highlights

- 🔢 **Your order, by drag and drop** (Settings → Devices & services → Power Control → Configure)
- 🔌 **Any plug or appliance**: Zigbee (Zigbee2MQTT, ZHA), Shelly, Tuya, Wi-Fi plugs, air conditioners, dehumidifiers, lights, fans, water heaters
- 🔍 **Power sensor found automatically** on the plug's own device; choose it by hand for multi-socket strips
- 💤 **Idle loads are skipped**: a TV in standby is not switched off for nothing
- ⏱️ **Two thresholds like a real meter**: a delayed one (the meter tolerates a small overload for a while) and an urgent one (notifies at once, switches off after 1 minute only if consumption is still over it)
- ✅ **Smart restore**: a load is switched back on only if its last measured power fits under the threshold
- ❄️ **Air conditioners get their mode back** (heat stays heat, cool stays cool)
- 🙋 **Switched on by hand?** It is removed from the list, it will not be touched again
- 🛡️ **Safe**: an unavailable meter is never read as 0 W, nothing is switched on or off without a valid reading
- 💾 **Survives restarts and updates**: the switched-off loads, the countdown, the thresholds and the switches are saved as soon as they change
- ♻️ **Configuration backup**: removed and added again? The setup offers to restore loads, sensors, notifications and thresholds
- 📲 **Notifications you choose**: phones (optionally only people at home), Alexa or any speaker with text-to-speech, quiet hours, which events
- 📢 **`power_control_event`** on the bus for your own automations
- 🇬🇧 🇮🇹 English and Italian

## ⚙️ How it works

```
consumption > switch-off threshold ─┬─ countdown starts (switch-off delay)
                                    ├─ warning a few minutes before the end
                                    └─ end of countdown: switch off the first active load
                                         └─ wait, measure again, next one … until under the threshold
consumption > urgent threshold ─── notification at once; still over it after 1 min: switch off, same order
consumption < restore threshold for a while ─── switch back on, last switched-off first,
                                                one at a time, only if it fits
```

Default thresholds come from the contract power (Italian meters allow +10% indefinitely
and up to +33% for a limited time). For 3 kW: **switch-off 3300 W** after **60 min**,
**urgent 3900 W**, **restore 2800 W**. You can change them at any time from the dashboard.

## 📦 Installation

### HACS

1. **HACS → ⋮ → Custom repositories**, add `https://github.com/LucaTomei/power-control-homeassistant` as **Integration**.
2. Download **Power Control** and restart Home Assistant.
3. **Settings → Devices & services → Add integration → Power Control**.

### Manual

Copy `custom_components/power_control` into `config/custom_components/` (or extract
`power_control.zip` from the [latest release](https://github.com/LucaTomei/power-control-homeassistant/releases/latest)) and restart.

## 🛠️ Configuration

1. **Setup**: choose the house power meter (W or kW) and the contract power. If Power
   Control was installed before, you can restore its whole configuration.
2. **Configure** (menu): each section is saved as soon as you confirm it.
   - **Loads and switch-off order**: pick the loads and drag them. The first is the first to be switched off.
   - **Power sensors and automatic restore**: the sensor found automatically is shown for each load; pick one by hand where needed (multi-socket strips), and choose the loads that must never be switched back on automatically.
   - **Notifications**: phones (`notify.mobile_app_…`), only people at home, which events, speakers and quiet hours.
   - **Presence at home**: for each phone, the sensor telling whether its owner is at home (a person,
     a device tracker, or a binary sensor / input boolean that is on at home). Used only with
     "Only to people at home". Left empty, it is found automatically for Companion app phones;
     a phone with no presence sensor always gets the notifications.
   - **Advanced**: warning time, urgent delay, pause between loads, restore delay, standby power, meter.

> [!WARNING]
> Switching off a washing machine or a dishwasher in the middle of a cycle may stop its
> program. Put them where you want in the order, or leave them out.

## 🧩 Entities

| Entity | What it is |
|---|---|
| `switch.power_control_enabled` | Load control on/off |
| `switch.power_control_push_notifications` / `voice_notifications` | Quick on/off for notifications |
| `number.power_control_shed_threshold` | Switch-off threshold (W) |
| `number.power_control_shed_delay` | How long above it before switching off (min) |
| `number.power_control_urgent_threshold` | Urgent threshold (W) |
| `number.power_control_restore_threshold` | Restore threshold (W) |
| `sensor.power_control_status` | `ok`, `over_threshold`, `warning`, `shedding`, `loads_off`, `restoring`, `disabled`, `meter_unavailable`. Attributes: `power`, `next_to_switch_off`, `urgent_at` (when a load is switched off if consumption stays over the urgent threshold), `loads` (order, power, state of every load) |
| `sensor.power_control_shed_at` | When the loads will be switched off |
| `sensor.power_control_loads_off` | How many loads are switched off (attribute `loads`) |
| `sensor.power_control_headroom` | Watts left before the threshold |
| `sensor.power_control_last_event` | The last message |
| `button.power_control_restore_now` | Switch every switched-off load back on now |
| `button.power_control_forget_loads_off` | Forget them without switching them on |

## 🖥️ Dashboard

Ready-made, standard cards only (the chart uses [ApexCharts card](https://github.com/RomRider/apexcharts-card), optional):

- [`dashboard/power-control-view.yaml`](dashboard/power-control-view.yaml): a full view (status, gauge, countdown, loads in order with their power, thresholds, chart, buttons).
- [`dashboard/power-control-alert-card.yaml`](dashboard/power-control-alert-card.yaml): an alert for your main view, shown only when something is happening.

Replace `sensor.YOUR_HOUSE_POWER` with your meter. The gauge colours use the default 3 kW thresholds: change them if you change the thresholds.

## 🤖 Automations

Every notification is also fired as an event:

```yaml
triggers:
  - trigger: event
    event_type: power_control_event
    event_data:
      type: shed  # warning, shed, restore, alert
actions:
  - action: light.turn_on
    target:
      entity_id: light.hallway
    data:
      color_name: red
```

## 🧪 Development

```bash
pip install -r requirements_test.txt
pytest
```

The tests simulate a house (meter, Zigbee/Shelly plugs, a multi-socket strip, an air
conditioner) and check every situation: delay and warning, urgent threshold, order,
idle loads, restore only when it fits, unavailable meter, manual switch-on, exclusions,
restart, notifications to people at home, voice, the dashboard templates.

Release: `./scripts/release.sh X.Y.Z` (GitHub Actions runs tests, hassfest and HACS
validation, then publishes the release).

## 🙏 Credits

Inspired by [Power Control](https://github.com/Home-Assistant-Pro-Team/Power-Control-HomeAssistant)
by Home Assistant Pro Team (YAML package). This integration is a complete rewrite.

---

## 🇮🇹 In italiano

**Spegne i carichi prima che salti il contatore e li riaccende quando c'è di nuovo posto.**

Power Control guarda il consumo di tutta la casa. Se resta sopra la soglia ti avvisa,
poi spegne i carichi **nell'ordine che scegli tu**, uno alla volta, finché il consumo
non torna sotto il limite. Quando i consumi tornano normali li riaccende, dall'ultimo
spento, **solo se ognuno ci sta**.

Si configura tutto da Home Assistant, senza YAML.

### Cosa fa

- **Ordine a trascinamento**: Impostazioni → Dispositivi e servizi → Power Control → Configura → *Carichi e ordine di spegnimento*.
- **Qualsiasi presa o elettrodomestico**: Zigbee (Zigbee2MQTT, ZHA), Shelly, Tuya, prese Wi-Fi, condizionatori, deumidificatori, luci.
- **Sensore di consumo trovato da solo** sul dispositivo della presa; sulle multiprese con un solo misuratore lo scegli tu.
- **Salta i carichi in standby** (sotto 15 W, regolabile).
- **Due soglie come il contatore**: una con ritardo (il contatore tollera un piccolo sforamento per un po') e una urgente (avvisa subito e, se dopo 1 minuto i consumi sono ancora sopra, spegne).
- **Riaccensione intelligente**: riaccende un carico solo se il suo consumo ci sta sotto la soglia.
- **I condizionatori tornano nella modalità di prima** (caldo resta caldo).
- **Riacceso a mano?** Esce dalla lista, non viene più toccato.
- **Sicuro**: se il misuratore è "non disponibile" non viene letto come 0 W e non si accende né si spegne niente.
- **Resiste a riavvii e aggiornamenti**: carichi spenti, conto alla rovescia, soglie e interruttori vengono salvati appena cambiano.
- **Backup della configurazione**: se rimuovi e riaggiungi l'integrazione, il setup propone di ripristinare carichi, sensori, avvisi e soglie.
- **Avvisi a scelta**: telefoni (anche solo a chi è in casa), Alexa o altoparlanti con sintesi vocale, fascia oraria, quali eventi.

### Soglie predefinite (contratto 3 kW)

Distacco **3300 W** dopo **60 minuti**, urgente **3900 W**, ripristino **2800 W**.
Si cambiano quando vuoi dalla dashboard.

### Installazione

HACS → ⋮ → **Custom repositories** → aggiungi `https://github.com/LucaTomei/power-control-homeassistant`
come **Integrazione**, scarica **Power Control**, riavvia e aggiungi l'integrazione da
Impostazioni → Dispositivi e servizi.

Nelle impostazioni (icona ingranaggio) ogni sezione viene salvata appena confermi:
**Carichi**, **Sensori di consumo**, **Avvisi**, **Presenza in casa** (per ogni telefono il
sensore che dice se il proprietario è in casa: persona, device tracker, binary sensor o
input boolean; se lasci vuoto viene cercato in automatico, e un telefono senza sensore di
presenza riceve sempre gli avvisi) e **Avanzate**.

> [!WARNING]
> Spegnere lavatrice o lavastoviglie a metà ciclo può interrompere il programma: mettile
> nell'ordine dove preferisci, oppure non metterle.

### Dashboard

Nella cartella [`dashboard`](dashboard) trovi una vista completa e un avviso da mettere
in cima alla vista principale (compare solo quando serve). Sostituisci
`sensor.YOUR_HOUSE_POWER` con il tuo misuratore.

## 📄 License

[MIT](LICENSE)
