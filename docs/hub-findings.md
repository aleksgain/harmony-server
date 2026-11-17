# Findings from a real hub

What a stock Harmony Hub actually does, observed on hardware. Each entry says what was
seen and how. Identifiers are redacted (`<SERIAL>`, `<KEY>`, ...).

**Test hardware:** Harmony Hub (`productId` "Pimento", model 106 / Elite hub), firmware
4.15.600, still provisioned on Logitech's cloud. Harmony Elite remote (`046d:c12b`, skin
`0x6f`, firmware 4.15.330). Tested on 2026-11-17.

## DNS redirect test (proxy mode)

**Setup:** all Logitech hostnames from [dns-rewrite-setup.md](dns-rewrite-setup.md) plus
`ping.myharmony.com` were pointed at harmony-server, running `SERVER_MODE=proxy`. The
Technitium query log, the server log and a `tcpdump` of the hub's traffic were recorded.
The hub was power-cycled to trigger its own cloud calls.

### Hostnames the hub resolves after boot

| Hostname | Notes |
|---|---|
| `svcs.myharmony.com` | Discovery. Retried about once a minute while it fails. |
| `home.myharmony.com` | `GET /time/current` |
| `sus.dhg.myharmony.com` | `POST .../getUpdates` |
| `ping.myharmony.com` | Connectivity check: connects, then closes without sending a request. **CNAME of `cf-svcs.myharmony.com`.** Override it explicitly: a resolver that already cached the `cf-svcs` CNAME will follow the cached chain to CloudFront, even with an override for `cf-svcs` in place. |
| `pubsub.pubnub.com` | **PubNub.** Not a Logitech host and not served by harmony-server. It is probably how the cloud pushes "sync now" and other events to the hub. Not yet captured. |

### Two TLS clients, two behaviours

The hub runs two separate HTTPS clients:

| Client | Used for | ClientHello | Verifies the certificate? |
|---|---|---|---|
| LuaSocket 2.0.2 (`User-Agent: LuaSocket/2.0.2 Pimento/4.15.600`) | `sus` update check, `home` time | large, many ciphers | **No.** Completed a handshake with a leaf signed by the private Harmony Revival CA. |
| Native client (probably an embedded C library) | `svcs` Discovery, some `home` calls | 109–116 bytes, TLS 1.2, a single cipher suite `ECDHE-RSA-AES128-GCM-SHA256`, extensions: sig_algs, ec_point_formats, supported_groups, encrypt_then_mac, SNI, extended_master_secret | **Yes.** It sends the fatal alert `unknown_ca` (`15 03 03 00 02 02 30`) right after the server's certificate. |

**What this means:** on stock firmware, a DNS rewrite alone cannot serve Discovery or
config sync to the hub. The update check and the time call can be served this way.
Discovery needs one of these:

- A rooted hub ([hub-root-repoint.md](hub-root-repoint.md)), with `ca.crt` added to its
  bundle or `discoveryServer` repointed.
- The traffic captured from the app side instead (MyHarmony desktop app behind mitmproxy,
  with its CA trusted on the PC). See the capture issue template.

**Security note:** the update check accepts any certificate, which also makes it a
possible way in for rooting. Firmware update responses can brick a hub, so do not experiment
with them on a hub you rely on.

### `POST https://sus.dhg.myharmony.com/SoftwareUpdatesPlatform/SoftwareUpdates/getUpdates`

Not the per-unit `.../product/{productId}/unit/{unitId}/info` route listed in Logitech's
service directory, and not implemented by harmony-server yet.

Request headers:

```
x-logitech-api-version: 1
logitech-sus-key: <KEY>            (40 chars; the same key may be built into every hub)
user-agent: LuaSocket/2.0.2 Pimento/4.15.600
content-type: application/json
accept: application/json
```

Request body: one entry per hub software component, here the hub plugins:

```json
{"requests":[
  {"displayName":"HEOS","name":"heos","masterProductId":106,"productId":"2101",
   "channel":"production","sysBuild":"1.1.7",
   "unitId":"106:<SERIAL>","masterUnitId":"<SERIAL>"},
  {"displayName":"SmartThings","name":"smartthings","masterProductId":106,"productId":"2005", ...},
  {"displayName":"Nest","name":"nest","masterProductId":106,"productId":"2002", ...},
  ...
]}
```

Response (no updates). It has one status per request, in order:

```json
{"responses":[{"status":204},{"status":204},{"status":204},{"status":204},{"status":204},{"status":204}]}
```

The hub sent this 4 times within 15 s of boot.

### `GET https://home.myharmony.com/time/current`

```
user-agent: LuaSocket 2.0.2
accept: application/json
```

```json
{"utcTimestamp":1794918867,"utc":"2026-11-17 12:34:26"}
```

Not implemented by harmony-server yet.

## Local API (no cloud involved)

- **WebSocket 8088:** `vnd.logitech.harmony.engine?config` returns the full config (about
  79 KB). Top level: `dataConsent`, `sequence`, `global` (`timeStampHash`, `locale`),
  `device[]`, `sla` (`latestSLAAccepted`, `latestSLAAcceptedDate`), `content`
  (`contentUserHost`, `contentDeviceHost`, `contentServiceHost`, `contentImageHost`,
  `householdUserProfileUri`), `activity[]`. **No `configVersion`/`contentVersion` at the
  top level.** Those live in `statedigest`. `config_compiler.py` currently emits them at
  the top level and does not emit `dataConsent`, `sla`, `global.timeStampHash` or
  `content.contentImageHost`.
- **XMPP 5222** (when enabled in the app): the guest login, `vnd.logitech.pair`, and then
  login as `<identity>` all work. The pair reply contains `hubId=106`. **The hub closes
  the socket if the post-SASL stream restart repeats the `<?xml?>` declaration.** Fixed
  in harmony-client.

## USB (Harmony Elite)

- Linux never binds `usbhid` to Harmony PIDs (`hid-quirks.c` ignore list), so there is
  no hidraw node. Use libusb.
- `/sys/sysinfo` is made of `key value` lines (space-separated).
- A Read that stops at `size` leaves a `0xFE` end-marker packet queued.
- Opening `/cfg/properties` for reading gets device error `0x0b`.
