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
- 📊 **Energy monitoring** — live power draw plus daily/monthly usage charts for
  devices with an energy meter
- 📈 **Energy history** — power and daily-usage trends recorded server-side over
  time, retained beyond what each device remembers
- 🔌 **Persistent discovery** — devices added by IP survive restarts
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
make run          # builds the frontend, then serves it from the API at :8080
```

## API

All endpoints are under `/api`; interactive docs live at `http://localhost:8080/docs`.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET`  | `/api/devices` | Cached device list |
| `GET`  | `/api/state` | Device list with live state re-read from hardware (polled) |
| `POST` | `/api/discover` | Discover devices (`{"target": "ip"}` to probe one host) |
| `POST` | `/api/devices/{id}/power` | `{"on": true\|false}` |
| `POST` | `/api/devices/{id}/brightness` | `{"value": 0-100}` |
| `POST` | `/api/devices/{id}/color` | `{"hex": "#rrggbb"}` or `{"hsv": [h,s,v]}` |
| `POST` | `/api/devices/{id}/children/{child}/power` | Toggle one outlet on a strip |
| `GET`  | `/api/devices/{id}/usage` | Energy data (live power + daily/monthly history) |
| `GET`  | `/api/devices/{id}/history` | Recorded history: recent power samples + persisted daily totals |
| `GET`  | `/api/events` | Live device-state stream (Server-Sent Events) |
| `GET` / `POST` | `/api/groups` | List rooms / create a room (`{"name": "..."}`) |
| `PATCH` / `DELETE` | `/api/groups/{id}` | Rename or set a room's devices / delete it |
| `GET` / `PUT` | `/api/favorites` | Read / set the starred device ids |

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `KASA_HOST` | `127.0.0.1` (`0.0.0.0` in Docker) | Bind address for the server |
| `KASA_PORT` | `8080` | Server port |
| `KASA_STATE_FILE` | `data/known_devices.json` | Where known device hosts are persisted |
| `KASA_GROUPS_FILE` | `data/groups.json` | Where rooms and favorites are persisted |
| `KASA_ENERGY_HISTORY_FILE` | `data/energy_history.db` | SQLite database of recorded energy samples |
| `KASA_ENERGY_SAMPLE_INTERVAL` | `300` | Seconds between energy-history samples (min `10`). Higher = fewer reads, coarser history |
| `TPLINK_USERNAME` | _(unset)_ | TP-Link cloud email, required for newer SMART-protocol devices (e.g. KP125M) |
| `TPLINK_PASSWORD` | _(unset)_ | TP-Link cloud password, paired with `TPLINK_USERNAME` |
| `KASA_SCAN_SUBNET` | _(unset)_ | CIDR subnet (e.g. `192.168.1.0/24`) swept by unicast on startup and offered as the default in the Discovery tab |
| `KASA_CLOUD_FALLBACK` | `0` | Set to `1` to control devices that no longer accept local auth (e.g. HS300 strips) via the TP-Link cloud — see below |
| `KASA_CLOUD_MODELS` | `HS300` | Comma-separated model prefixes routed through the cloud when the fallback is on |
| `KASA_CLOUD_POLL_INTERVAL` | `30` | Seconds between cloud-device state refreshes during the live poll (local devices refresh every poll). Higher = fewer TP-Link round-trips |
| `KASA_ENERGY_RATE` | _(unset)_ | Flat cost per kWh (a number, e.g. `0.18`) used to show energy cost — see below. Leave unset to hide cost |
| `KASA_ENERGY_CURRENCY` | `$` | Currency symbol/prefix shown before cost amounts |

Newer Kasa devices use TP-Link's SMART protocol and authenticate before they
can be discovered or controlled. Provide your TP-Link cloud credentials via a
`.env` file (copy `.env.example` to `.env` and fill it in); Docker Compose reads
it automatically. Without them, only legacy plugs are reachable. `.env` is
gitignored — never commit real credentials.

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

Broadcast discovery only reaches devices on the server's own subnet — it can't
cross VLAN boundaries. If your plugs live on a separate subnet (e.g. an isolated
IoT VLAN), set `KASA_SCAN_SUBNET` to that CIDR; the server then sweeps every
address by unicast on startup, and the Discovery tab's "Scan subnet" button does
the same on demand.

In Docker, `./logs` and `./data` are mounted as volumes so logs and the known
device list survive rebuilds.

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
- Docker (optional)

### Local development

The Vite dev server proxies `/api` to the backend, so the frontend always
fetches relative paths in both dev and production.

```bash
make setup     # one-time: install Python + frontend deps

make api-dev   # Terminal 1 — FastAPI with autoreload (http://localhost:8080)
make web-dev   # Terminal 2 — SvelteKit dev server (http://localhost:5173)
```

### Quality checks

```bash
make test         # backend tests (pytest)
make lint         # ruff (Python)
make web-lint     # prettier + eslint (frontend)
make check        # svelte-check (types + a11y)
```

## Testing

The backend has a pytest suite that fakes `python-kasa`, so it runs with no real
devices or network: color helpers, serialization, energy data, host
persistence, and every REST route (including error paths).

```bash
make test     # or: uv run pytest
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
4. Run `make test`, `make lint`, and `make web-lint`
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
