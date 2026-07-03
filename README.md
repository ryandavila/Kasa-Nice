# Kasa-Nice

A modern, containerized web app for controlling TP-Link Kasa smart home devices
on your local network — no cloud account required.

A **FastAPI** backend ([`api/`](api/)) talks to your devices via
[python-kasa](https://github.com/python-kasa/python-kasa) and serves a
**SvelteKit** single-page frontend ([`web/`](web/)) that streams live state.

## Features

- 🏠 **Local control** — discover and control Kasa devices on your LAN, no cloud
- ⚡ **Live state** — the UI streams device state over Server-Sent Events, so
  changes made elsewhere (the Kasa app, a physical switch) show up automatically
- 🔧 **Full device support** — toggle plugs, dim, set bulb color, and control
  individual outlets on power strips
- 🗂️ **Rooms & favorites** — group devices into rooms and star the ones you reach
  for most; toggle between a by-type and a by-room view
- 🎬 **Scenes** — snapshot a set of devices' current state and re-apply it in one
  tap (power, brightness, and colour), tolerating an offline device
- 📊 **Energy monitoring** — live power draw plus daily/monthly usage charts for
  devices with an energy meter
- 📈 **Energy history** — power and daily-usage trends recorded server-side over
  time, retained beyond what each device remembers
- 🔔 **Alerts** — get notified when a device drops offline/recovers or draws more
  than a per-device wattage threshold, in-app (a header bell) and via an optional
  webhook
- 🔌 **Persistent discovery** — devices added by IP survive restarts
- 💾 **Backup & restore** — download everything the server persists as one JSON
  file, restore it later with a confirmation step, and separately download the
  raw energy-history database
- 🌗 **Light/dark theme** with an instant, no-flash toggle
- 🐳 **Docker-ready** — one multi-stage image builds the frontend and serves it
  from the API on a single port

## Quick Start

### Docker (recommended)

```bash
git clone https://github.com/ryandavila/Kasa-Nice.git
cd Kasa-Nice
docker compose up -d
# open http://localhost:8080
```

> **Discovery & networking:** the default bridge network can't carry UDP
> broadcast, so auto-discovery may find nothing in Docker. Either add devices by
> IP in the **Discovery** tab, or — on a Linux host — switch to host networking
> (see the commented `network_mode: host` in [`compose.yml`](compose.yml)).
> Host networking is Linux-only; on Docker Desktop (macOS/Windows) broadcast
> won't cross the VM boundary, so run outside Docker or add devices by IP.

### Local (without Docker)

```bash
git clone https://github.com/ryandavila/Kasa-Nice.git
cd Kasa-Nice
uv sync
just run          # builds the frontend, then serves it from the API at :8080
```

## API

All endpoints are under `/api`; interactive docs live at `http://localhost:8080/docs`.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET`  | `/api/devices` | Cached device list |
| `GET`  | `/api/state` | Device list with live state re-read from hardware (polled) |
| `POST` | `/api/discover` | Discover devices (`{"target": "ip"}` to probe one host) |
| `POST` | `/api/devices/{id}/power` | `{"on": true\|false}` |
| `POST` | `/api/power` | Switch every device (`{"on": true\|false}`); returns `{on, succeeded, failed}` |
| `POST` | `/api/devices/{id}/brightness` | `{"value": 0-100}` |
| `POST` | `/api/devices/{id}/color` | `{"hex": "#rrggbb"}` or `{"hsv": [h,s,v]}` |
| `POST` | `/api/devices/{id}/children/{child}/power` | Toggle one outlet on a strip |
| `PATCH` | `/api/devices/{id}` | Rename a device (`{"alias": "..."}`); 501 for cloud-only devices (`can_rename: false`) |
| `PATCH` | `/api/devices/{id}/children/{child}` | Rename one outlet on a strip (`{"alias": "..."}`) |
| `GET`  | `/api/devices/{id}/usage` | Energy data (live power + daily/monthly history) |
| `GET`  | `/api/devices/{id}/history` | Recorded history: recent power samples + persisted daily totals |
| `GET`  | `/api/energy/summary` | Whole-home energy totals across all metered devices (`{total_power_w, today_kwh, month_kwh, today_cost, month_cost, device_count}`) |
| `GET`  | `/api/energy/insights` | Derived insights over recorded history (`{projection, rooms, week, idle}`): month-end projection, per-room rollups, week-over-week delta, and overnight idle draw |
| `GET`  | `/api/events` | Live device-state stream (Server-Sent Events) |
| `GET` / `POST` | `/api/groups` | List rooms / create a room (`{"name": "..."}`) |
| `PATCH` / `DELETE` | `/api/groups/{id}` | Rename or set a room's devices / delete it |
| `POST` | `/api/groups/{id}/power` | Switch every device in a room (`{"on": true\|false}`); returns `{on, succeeded, failed}` |
| `GET` / `PUT` | `/api/favorites` | Read / set the starred device ids |
| `GET` / `POST` | `/api/schedules` | List schedule rules / create one (see [Schedules](#schedules)) |
| `PATCH` / `DELETE` | `/api/schedules/{id}` | Update (partial) / delete a schedule rule |
| `GET` / `POST` | `/api/scenes` | List scenes / create one (`{"name", "entries"}` or `{"name", "device_ids"}` — see [Scenes](#scenes)) |
| `PATCH` / `DELETE` | `/api/scenes/{id}` | Rename and/or replace a scene's entries / delete it |
| `POST` | `/api/scenes/{id}/apply` | Apply a scene; returns `{succeeded, failed}` |
| `GET`  | `/api/alerts/recent` | Recent alerts from the in-memory ring buffer, newest first (see [Alerts](#alerts)) |
| `GET` / `PUT` | `/api/alerts/thresholds` | Read / full-replace the per-device power-draw thresholds (`{"thresholds": {"<id>": watts}}`) |
| `GET`  | `/api/backup` | Download every JSON store as one versioned document (see [Backup & restore](#backup--restore)) |
| `POST` | `/api/backup/restore` | Replace every JSON store's contents from a backup document; validated whole, no partial writes |
| `GET`  | `/api/backup/energy.db` | Download a consistent snapshot of the energy-history SQLite database |
| `GET` / `PUT` | `/api/vacation` | Read / full-replace the vacation-mode (presence-simulation) config; GET also returns live status (`active`, `next_switch_ts`, `resolved_device_ids`) — see [Vacation mode](#vacation-mode) |

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `KASA_HOST` | `127.0.0.1` (`0.0.0.0` in Docker) | Bind address for the server |
| `KASA_PORT` | `8080` | Server port |
| `KASA_STATE_FILE` | `data/known_devices.json` | Where known device hosts are persisted |
| `KASA_GROUPS_FILE` | `data/groups.json` | Where rooms and favorites are persisted |
| `KASA_ENERGY_HISTORY_FILE` | `data/energy_history.db` | SQLite database of recorded energy samples |
| `KASA_SCHEDULES_FILE` | `data/schedules.json` | Where schedule (timer) rules are persisted |
| `KASA_SCENES_FILE` | `data/scenes.json` | Where scenes (saved per-device states) are persisted |
| `KASA_ALERTS_FILE` | `data/alerts.json` | Where per-device power-draw alert thresholds are persisted |
| `KASA_VACATION_FILE` | `data/vacation.json` | Where the vacation-mode (presence-simulation) config is persisted |
| `KASA_ALERT_INTERVAL` | `60` | Seconds between alert evaluations (min `10`) |
| `KASA_ALERT_WEBHOOK_URL` | _(unset)_ | Optional URL each alert is POSTed to ([ntfy](https://ntfy.sh)-compatible); leave unset for in-app alerts only — see [Alerts](#alerts) |
| `KASA_ENERGY_SAMPLE_INTERVAL` | `300` | Seconds between energy-history samples (min `10`). Higher = fewer reads, coarser history |
| `TPLINK_USERNAME` | _(unset)_ | TP-Link cloud email, required for newer SMART-protocol devices (e.g. KP125M) |
| `TPLINK_PASSWORD` | _(unset)_ | TP-Link cloud password, paired with `TPLINK_USERNAME` |
| `KASA_SCAN_SUBNET` | _(unset)_ | CIDR subnet (e.g. `192.168.1.0/24`) swept by unicast on startup and offered as the default in the Discovery tab |
| `KASA_CLOUD_FALLBACK` | `0` | Set to `1` to control devices that no longer accept local auth (e.g. HS300 strips) via the TP-Link cloud — see below |
| `KASA_CLOUD_MODELS` | `HS300` | Comma-separated model prefixes routed through the cloud when the fallback is on |
| `KASA_CLOUD_POLL_INTERVAL` | `30` | Seconds between cloud-device state refreshes during the live poll (local devices refresh every poll). Higher = fewer TP-Link round-trips |
| `KASA_ENERGY_RATE` | _(unset)_ | Flat cost per kWh (a number, e.g. `0.18`) used to show energy cost — see below. Leave unset to hide cost |
| `KASA_ENERGY_CURRENCY` | `$` | Currency symbol/prefix shown before cost amounts |
| `KASA_LATITUDE` | _(unset)_ | Latitude in decimal degrees (positive north, e.g. `40.7128`) for sunrise/sunset schedules. Both this and `KASA_LONGITUDE` are required for those rules to fire |
| `KASA_LONGITUDE` | _(unset)_ | Longitude in decimal degrees (positive east, e.g. `-74.0060`) — paired with `KASA_LATITUDE` |

Newer Kasa devices use TP-Link's SMART protocol and authenticate before they
can be discovered or controlled. Provide your TP-Link cloud credentials via a
`.env` file (copy `.env.example` to `.env` and fill it in). The server loads
`.env` from the repo root on every start — `just run`, `just api-dev`, and
Docker Compose alike — with real environment variables taking precedence.
Without credentials, only legacy plugs are reachable. `.env` is gitignored —
never commit real credentials.

### Cloud fallback for devices that dropped local control

Some older devices (notably the **HS300** power strip) shipped a firmware update
that replaced their local KLAP authentication with a token/certificate scheme
[python-kasa can't yet speak](https://github.com/python-kasa/python-kasa/issues/1604),
and disabled their legacy local port. They stay fully controllable through
TP-Link's cloud — the same path the Kasa app uses. Set `KASA_CLOUD_FALLBACK=1`
(reusing `TPLINK_USERNAME`/`TPLINK_PASSWORD`) and, after local discovery, the
server logs into the cloud and attaches any matching online devices that aren't
already reachable locally. They appear, toggle, and report energy
just like local devices; their LAN IP is recovered from the MAC so they show the
same `host`. Per-outlet energy is aggregated into a whole-strip total, using the
device's own clock for "today"/"this month". Control round-trips to TP-Link's
servers, so it's a little slower than local, and the live state poll refreshes
cloud devices only every `KASA_CLOUD_POLL_INTERVAL` seconds (default 30) — rather
than every few seconds like local devices — to avoid hammering the cloud API.

### Energy cost (optional)

Set `KASA_ENERGY_RATE` to your price per kWh (and optionally `KASA_ENERGY_CURRENCY`,
default `$`) to show an estimated cost next to energy usage — for today, this
month, and each bar in the charts. It applies globally to every device with an
energy meter (the HS300 strips and KP125M plugs alike). When the rate is unset,
all cost fields are null and the UI shows kWh only.

> **Note:** this is a **flat-rate approximation** — a single price per kWh. It does
> not model tiered pricing, time-of-use rates, fixed service charges, or taxes, so
> treat the figures as a rough estimate, not a bill.

### Schedules

The **Schedules** tab lets you create rules that fire on one of four **triggers**:

- **Fixed time** — _"at **HH:MM** on these **days of the week**."_
- **Sunrise** / **Sunset** — relative to the sun for the server's location, with
  an **offset** in minutes (e.g. `−30` = 30 minutes before), on selected days.
- **Once** — at a single **date & time**, then the rule auto-disables (it's kept,
  not deleted, so you can see it ran).

Each rule's **action** switches its **device** or **room** **on** / **off**, or
**applies a scene** (a scene owns its own device list, so a scene rule needs no
target). Toggle a rule on/off, edit it, or delete it inline; each shows when it
last ran and whether it succeeded.

Rules run **server-side**: a background task evaluates them once a minute against
the server's **local time**, so they fire whether or not a browser is open, and
work uniformly for both locally-controlled and cloud-fallback devices. Each tick
computes today's sunrise/sunset for the configured location (a small pure-Python
[NOAA](https://gml.noaa.gov/grad/solcalc/solareqns.PDF) calculation, no extra
dependency) and applies the offset. Room and scene rules reuse the same
partial-failure-tolerant fan-out as the manual controls, so one unreachable
device doesn't stop the rest. A rule fires once per scheduled minute (a brief
overrun is caught up; the server won't replay a backlog after a long suspend).
Rules persist to `KASA_SCHEDULES_FILE` (default `data/schedules.json`) — mount it
as a volume to keep them across rebuilds.

Sunrise/sunset rules need a **location**: set `KASA_LATITUDE` and `KASA_LONGITUDE`
(see [Configuration](#configuration)). Without them the API rejects creating such
a rule (422) and the composer shows a hint; any that already exist simply don't
fire (logged once).

Device cards also carry a quick **"turn off in N minutes"** menu (the moon icon
on an on device) with 15/30/60-minute presets — it just posts a one-shot `once`
rule computed in the browser, so the countdown runs on the server.

Weekdays are numbered `0`=Monday … `6`=Sunday. The rule schema carries a `kind`
discriminator (`fixed_time` / `sunrise` / `sunset` / `once`) and a flat `action`
(`on` / `off` / `scene`), both left open so further kinds and actions can be
added without breaking existing rules — an old `fixed_time` file loads unchanged.

### Scenes

The **Scenes** tab lets you save a per-device state and apply it in one tap —
e.g. a _"Movie night"_ scene that dims the lamp and turns the overhead off.
Create a scene from the current state of any devices you pick (the server
snapshots each device's on/off, plus brightness and colour where supported),
then rename, delete, or **apply** it. Applying fans out across the scene's
devices concurrently and tolerates per-device failure — one unreachable device
doesn't stop the rest — reporting how many devices reached their saved state
(`{succeeded, failed}`), the same shape as the room/global power actions.

Brightness and colour are re-applied only for entries that leave a device **on**
(they're meaningless on an off light). Applying is a plain server function
(`api/scene_service.py:apply_scene`) callable by id without going through HTTP,
so a future schedule can trigger a scene, not just an on/off. Scenes persist to
`KASA_SCENES_FILE` (default `data/scenes.json`) — mount it as a volume to keep
them across rebuilds.

### Alerts

A background task evaluates two detectors on an interval (`KASA_ALERT_INTERVAL`,
default 60s):

- **Reachability** — a device becoming unreachable, or recovering, versus the
  previous evaluation.
- **Power draw** — a device drawing more than a per-device wattage threshold. Set
  a threshold per metered device from the header **bell** dropdown (blank = off);
  thresholds persist to `KASA_ALERTS_FILE` (default `data/alerts.json`). Draw is
  read from the recorded energy samples, so this adds no extra device polling.

Alerts are **debounced** so one incident is one alert, not one per cycle: a
reachability alert fires on the transition into the bad state (and a recovery
alert on the way out), while a power alert fires only when draw crosses above the
threshold and re-arms once it drops back below.

Delivery is in-app and, optionally, outbound:

- **In-app** — the newest alerts (a bounded ring buffer, up to 100) are shown in
  the header bell dropdown, which polls `GET /api/alerts/recent`. The unseen count
  is tracked client-side. The buffer is **in-memory only** — it is *not* persisted
  across restarts in v1, so a restart starts with an empty alert history.
- **Webhook** — set `KASA_ALERT_WEBHOOK_URL` to POST each alert to a URL in an
  [ntfy](https://ntfy.sh)-compatible shape: the plain-text body is the alert
  message and the `Title` header is a short title. Webhook failures are logged,
  never fatal; leave it unset for in-app alerts only.

Broadcast discovery only reaches devices on the server's own subnet — it can't
cross VLAN boundaries. If your plugs live on a separate subnet (e.g. an isolated
IoT VLAN), set `KASA_SCAN_SUBNET` to that CIDR; the server then sweeps every
address by unicast on startup, and the Discovery tab's "Scan subnet" button does
the same on demand.

In Docker, `./logs` and `./data` are mounted as volumes so logs and the known
device list survive rebuilds.

### Backup & restore

The **Settings** panel (gear icon in the header) lets you download and restore
everything the server persists as JSON: rooms, favorites, scenes, schedules,
alert thresholds, and known devices (including the last-known identity of any
device that's gone offline). It's one versioned document — `GET /api/backup` —
tagged with a `backup_version` and the server's `app_version` for reference.

**Restoring is a two-step, destructive operation.** Picking a file parses it
client-side and shows a summary of what it contains (counts per section) before
anything is sent to the server; only after you confirm does the client
`POST` it to `/api/backup/restore`, which **replaces** the current contents of
every store. The server independently re-validates the entire document against
its schemas first — including `backup_version` — and rejects the whole request
with a 4xx (no partial writes) if anything is invalid or the version is one
this build doesn't understand. A successful restore pushes an immediate update
over the SSE stream so every connected client refreshes.

The energy-history database is **not** part of this JSON document — it's
downloaded separately via `GET /api/backup/energy.db` (also from the Settings
panel) since it can be much larger and is a different format (SQLite, not
JSON). That endpoint streams a consistent point-in-time snapshot (via SQLite's
own online backup API) so a download can't catch the file mid-write while the
energy recorder is running. There's currently no restore path for it — recorded
history is meant to be downloaded for your own analysis/archival, not
round-tripped back into the server.
### Vacation mode

Presence simulation: while enabled, the server randomly switches a configured
set of lights on and off inside a nightly window so an empty home looks
occupied. A background task (running alongside the scheduler) picks each device's
switch times independently — jittered per device — so the pattern never reads as
mechanical, and turns everything off when the window closes.

Configure it from the **Vacation** tab, or via `GET`/`PUT /api/vacation`:

- **Targets** — any mix of individual devices and rooms; rooms are resolved to
  their current members at run time, so editing a room updates the simulation.
- **Active window** — the `end_time` is a fixed local `HH:MM` (default `23:00`);
  the `start_time` may be a fixed `HH:MM` or left unset to begin at **sunset**
  for the server's configured location (`KASA_LATITUDE`/`KASA_LONGITUDE`),
  falling back to a fixed `19:00` when no location is set.
- **Interval** — each light waits a random gap between `min_interval_minutes` and
  `max_interval_minutes` (default 15–45) between its switches.

Vacation mode never fights you or your schedules: if a light's state changes from
another source (a schedule rule, the Kasa app, a wall switch) between its planned
switches, the simulation adopts that state and leaves the light alone for a short
cooldown instead of yanking it back. The config is stored at `KASA_VACATION_FILE`
(default `data/vacation.json`); `GET /api/vacation` also returns whether the
window is currently `active` and the `next_switch_ts` of the soonest planned
toggle, which the header's vacation indicator reflects.

## Project Structure

```
├── api/                  # FastAPI backend
│   ├── main.py           # App factory, lifespan discovery, SPA serving, entry point
│   ├── routes.py         # REST endpoints under /api
│   ├── kasa_service.py   # Device discovery & control (decoupled from any UI)
│   ├── device_store.py   # Persists known device hosts
│   ├── schemas.py        # Pydantic request/response models
│   └── logging_config.py
├── web/                  # SvelteKit frontend (Svelte 5, Tailwind v4, bun)
│   └── src/lib/          # Components, runes stores, and the typed API client
├── tests/                # pytest suite (python-kasa is faked; no devices needed)
├── pyproject.toml        # Python project + tooling config
├── Dockerfile            # Multi-stage: bun builds web/, Python serves it
└── compose.yml           # Docker Compose
```

## Development

### Prerequisites

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) (Python dependency management)
- [bun](https://bun.sh/) (frontend)
- [just](https://just.systems) (task runner — run `just` to list recipes)
- Docker (optional)

### Local development

The Vite dev server proxies `/api` to the backend, so the frontend always
fetches relative paths in both dev and production.

```bash
just setup     # one-time: install Python + frontend deps

just dev       # API autoreload + frontend HMR in one terminal (http://localhost:5173)
just dev 8090  # same, with the API on another port when 8080 is taken
```

Prefer separate terminals (e.g. to restart one side independently)? The two
halves are still available on their own:

```bash
just api-dev   # Terminal 1 — FastAPI with autoreload (http://localhost:8080)
just web-dev   # Terminal 2 — SvelteKit dev server (http://localhost:5173)
```

### Quality checks

```bash
just test         # backend tests (pytest)
just web-test     # frontend unit tests (vitest)
just lint         # lint & autofix Python (ruff) + frontend (prettier + eslint)
just typecheck    # svelte-check (types + a11y)
just format       # apply ruff + prettier formatting
```

`just fix` runs format, lint, typecheck, and all tests in one go, while
`just ci` runs the same checks in read-only mode (no file mutations) — the
exact suite CI enforces.

## Testing

The backend has a pytest suite that fakes `python-kasa`, so it runs with no real
devices or network: color helpers, serialization, energy data, host
persistence, and every REST route (including error paths).

```bash
just test     # or: uv run pytest
```

## Supported Devices

All devices supported by python-kasa, including:

- **Plugs:** HS100/103/105/107/110, KP105/115/125/401, EP10
- **Power strips:** EP40, HS300, KP303, KP200, KP400, KP405
- **Wall switches:** ES20M, HS200/210/220, KS200M/220M/230
- **Bulbs:** LB100/110/120/130/230, KL50/60/110/120/125/130/135
- **Light strips:** KL400/420/430

## Logging

Structured logging to the console and to `./logs/kasa_nice.log`, with rotation
(10 MB max, 5 backups).

## Troubleshooting

**No devices found**
- Confirm devices are powered on and on the same network segment as the host.
- In Docker (bridge mode), broadcast discovery won't work — add devices by IP in
  the Discovery tab, or use host networking on Linux.
- Verify with python-kasa directly: `docker compose exec kasa-nice uv run kasa discover`.

**Permission issues**
- Ensure `./logs` and `./data` are writable by the container/user.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes and add tests where applicable
4. Run `just ci` (or `just fix` to auto-format and fix as it goes)
5. Submit a pull request

## License

This project is licensed under the terms specified in the [LICENSE](LICENSE) file.

## Credits

This project is a fork and modernization of the original
[Kasa-Nice](https://github.com/uni-byte/Kasa-Nice) by
[uni-byte](https://github.com/uni-byte), which provided the foundation for
controlling TP-Link Kasa devices through a web interface.

This fork has since been rebuilt as a FastAPI + SvelteKit application
(replacing the original NiceGUI UI) with live state polling, native energy
charts, persistent discovery, modern Python packaging, uv-based dependency
management, a pytest suite, and Docker improvements.

## Acknowledgments

- [python-kasa](https://github.com/python-kasa/python-kasa) — TP-Link Kasa device control
- [FastAPI](https://fastapi.tiangolo.com/) — backend framework
- [SvelteKit](https://svelte.dev/) + [Tailwind CSS](https://tailwindcss.com/) — frontend

## Links

- [GitHub Repository](https://github.com/ryandavila/Kasa-Nice)
- [python-kasa Documentation](https://python-kasa.readthedocs.io/)
