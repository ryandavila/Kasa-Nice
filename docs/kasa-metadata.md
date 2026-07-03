# Kasa-native device metadata: what we can read, and can we import rooms?

Research question: the Kasa mobile app lets a user name devices, sort them into
**rooms**, and set a home **location**. Can Kasa-Nice *read* any of that back —
especially rooms/groups — through a **read-only** path we already have (local
python-kasa, or the TP-Link cloud client in `api/cloud_service.py`), so we could
seed Kasa-Nice's own rooms from the Kasa side?

**Verdict (TL;DR): No — Kasa-app rooms/groups are NOT retrievable via any
read-only path available to us.** Device *names* (aliases) and a single home
*lat/lon* are readable, but the room/group structure a user builds in the Kasa
app is stored account-side and is never returned by `getDeviceList`, by a
device's `get_sysinfo`, or by any python-kasa attribute. Phase B (import rooms
from Kasa) is therefore **not implemented** — see the closing section.

All values below are redacted (ids/MACs/URLs shown as `xx…xx`); raw redacted
caches live in the `scratch/` working dir and are **not** committed.

---

## 1. Local protocol / python-kasa (pinned `python-kasa==0.10.2`)

Inspected the `kasa.Device` interface (and its IOT/SMART subclasses) plus the
fake devices in `api/testing/fake_devices.py`. Every metadata-ish member of the
`Device` surface:

| Attribute | Kind | Meaning | User-meaningful? | Room/group? |
| --- | --- | --- | --- | --- |
| `alias` | `str` | The device's display name the user typed in the app (e.g. `"Living Room Lamp"`). | **Yes** | No — a name, not a room |
| `children[].alias` | `str` | Per-outlet name on a strip (e.g. `"TV Right 1"`). | **Yes** | No |
| `location` | `dict` | `{"latitude": …, "longitude": …}` — one home geo-point (IOT reads `latitude_i`/`longitude_i` from sys_info; SMART reads `latitude`/`longitude`). | Somewhat (a point, not a room) | No |
| `region` | `str \| None` | Locale/region string, derived from the model suffix (e.g. `"US"` from `HS300(US)`). Not user-set. | No | No |
| `timezone` | tz | Device timezone. Not user-set as a room. | No | No |
| `mac`, `model`, `device_type`, `hw_info`, `rssi`, `device_id` | — | Identity/hardware/signal. | No | No |

**There is no room, group, home, zone, category, or tag attribute anywhere on
the python-kasa `Device` interface.** (Confirmed by enumerating the `Device` MRO
and grepping the installed package source for `room|group|home|zone|category|
tag|location|region`.)

One near-miss worth documenting: python-kasa's **new-discovery** (SMART-protocol
UDP) redaction map (`kasa/discover.py`) lists `group_id` / `group_name` /
`master_device_id`. These are **device-pairing** groups (e.g. bulbs bound into a
single light group, or a master/child device binding) surfaced only in a *local*
discovery result — **not** Kasa-app rooms, and not something our stack reads.
Moreover our strips are cloud-controlled (local port 9999 disabled, see
`api/cloud_service.py` module docstring), so we never receive these discovery
frames for them at all.

---

## 2. TP-Link Kasa cloud (`api/cloud_service.py`, real credentials, read-only)

Made a tiny, read-only probe against the live account: **one** `login` +
**one** `getDeviceList`, then **one** read-only `passthrough` `get_sysinfo` on a
single HS300. No control/write/rename calls. Account snapshot: **11 devices**
(9× `KP125M(US)` SMART plugs, 2× `HS300(US)` IOT strips), all owner-role.

### `getDeviceList` — every field returned per device

Complete key set (union across all 11 devices):

```
accountApiUrl, alias, appServerUrl, appServerUrlV2, deviceHwVer, deviceId,
deviceMac, deviceModel, deviceName, deviceRegion, deviceType, fwId, fwVer,
hwId, isSameRegion, lastBindTime, oemId, role, status
```

Sample entry (redacted):

```json
{
  "deviceType": "IOT.SMARTPLUGSWITCH",
  "deviceModel": "HS300(US)",
  "deviceName": "Smart Wi-Fi Power Strip",
  "alias": "TP-LINK_Power Strip_7A63",
  "deviceMac": "30…63",
  "deviceId": "80…14",
  "deviceRegion": "us-east-1",
  "isSameRegion": true,
  "role": 0,
  "status": 1,
  "fwVer": "1.1.2 Build 241220 Rel.171333",
  "deviceHwVer": "2.0"
}
```

| Field | Meaning | User-meaningful? | Room/group? |
| --- | --- | --- | --- |
| `alias` | User-typed device name. | **Yes** | No |
| `deviceName` | Marketing model name (`"Smart Wi-Fi Power Strip"`); same for all like devices. | No | No |
| `deviceModel` / `deviceType` | `HS300(US)` / `IOT.SMARTPLUGSWITCH`. | No | No |
| `deviceMac` | MAC — our stable id after normalization. | (id) | No |
| `deviceRegion` / `isSameRegion` | **Cloud server region** (`"us-east-1"`) for RPC routing — NOT a user room. | No | **No** |
| `role` | 0=owner / (1=shared). | No | No |
| `status` | 1=online in the cloud's view. | No | No |
| `appServerUrl(V2)`, `accountApiUrl`, `deviceId`, `fwVer`, `hwId`, `oemId`, `fwId`, `lastBindTime`, `*HwVer` | Routing / firmware / binding metadata. | No | No |

**`getDeviceList` returns no room, group, home, zone, category, folder, or tag
field.** The only key matching those search terms was `deviceRegion`, which is a
cloud data-center region, not a Kasa-app room.

### HS300 `get_sysinfo` (read-only cloud passthrough)

Full key set:

```
alias, child_num, children, deviceId, err_code, feature, hwId, hw_ver,
latitude_i, led_off, lnk_on, longitude_i, mac, mic_type, model, obd_src,
oemId, rssi, status, sw_ver, updating
```

- `alias` — user device name (**meaningful**).
- `children[]` — 6 outlets, each `{id, state, alias, on_time, next_action}`; the
  `alias` is the user's per-outlet name (**meaningful**, e.g. `"TV Right 1"`).
- `latitude_i` / `longitude_i` — the home geo-point the user set in the app
  (observed `406666` / `-739922` ⇒ ~`40.67, -73.99`). A **single lat/lon**, so
  it can distinguish "at home" but cannot reconstruct which *room* a device is
  in — every device in the account shares (at most) one home point.
- Everything else is hardware/firmware/link state.

**No room/group/zone key in `get_sysinfo` either** (verified against the same
keyword search).

### Account-level room/home RPC?

python-kasa's `cloud` modules (`kasa/{smart,iot}/modules/cloud.py`) are
per-device connectivity toggles (bind/unbind), not an account home/room listing.
The library has no `getHome`/`roomList`/`getAppSettings` call. Kasa-app rooms are
persisted in the mobile app's own account settings, behind an endpoint the
device-facing cloud API we authenticate against does not expose. Adding a call
to a speculative undocumented endpoint would violate the read-only, tiny-request
constraints for no guaranteed payload, so it was not attempted.

---

## 3. Verdict and consequence for Phase B

| Question | Answer |
| --- | --- |
| Can we read device **names** (aliases)? | Yes — local `alias` and cloud `alias`; per-outlet `children[].alias`. |
| Can we read a home **location**? | Yes — a single lat/lon (`location` / `latitude_i`,`longitude_i`). |
| Can we read Kasa-app **rooms/groups**? | **No.** Not in python-kasa, not in `getDeviceList`, not in `get_sysinfo`, not via any account RPC our client can reach. |

Because rooms/groups are **not retrievable** through any read-only path we have,
the conditional Phase B — `POST /api/groups/import-kasa` and an "Import from
Kasa" button — was **deliberately not implemented**. There is no Kasa-side room
data to import; a "sync rooms from Kasa" feature is not buildable on the reads
available to us. This findings document is the deliverable, and reaching that
conclusion is the success criterion.

(If TP-Link's app-settings endpoint that stores rooms is ever reverse-engineered
and confirmed stable/read-only, revisit: the import would still match devices by
**normalized MAC** — the same `_norm_mac` / `stable_device_id` keying rooms
already use — and merge additively, never deleting existing Kasa-Nice rooms.)
