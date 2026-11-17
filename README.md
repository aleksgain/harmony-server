# harmony-server

A self-hosted replacement for Logitech's MyHarmony cloud backend (`svcs.myharmony.com`,
`setup.myharmony.com` and friends), part of the **Harmony Revival** project.

## 1. What this is and why it exists

Logitech has abandoned MyHarmony. The legacy desktop software's backend shut down on
2025-05-28. The cloud that Harmony Hubs depend on is still up as of 2026-11, but it has
no announced end date and could be switched off at any time. A hub that is already
configured keeps working locally (8088 WebSocket / 5222 XMPP). A **factory-reset or new
hub cannot be provisioned or configured** without that cloud, so it becomes a brick.

harmony-server answers the cloud's role on your LAN:

* **Provisioning**: hands a hub its `accountId`, `globalRemoteId`, `discoveryServer`
  and cloud token, and claims hubs to local accounts.
* **Discovery.svc**: Logitech's WCF service locator, served from a captured copy of the
  real directory.
* **Config sync**: stores devices and activities, compiles them into a versioned bundle,
  and answers sync checks. Best-effort; see [Known gaps](#8-known-gaps--help-wanted).
* **Accounts**: local sign-up/sign-in (JWT), plus Logitech's three-step sign-in chain
  re-implemented for legacy clients.
* **Device DB**: the full Harmony IR archive (278k devices, 13M commands), as static
  files plus a lookup API.
* **Firmware**: always answers "no update".
* **Proxy mode**: relays to the real Logitech cloud and records every request/response,
  to capture the remaining unknown schemas while the cloud is still alive.

## 2. Quick start

```sh
git clone <this repo> harmony-server && cd harmony-server
scripts/fetch-device-db.sh                      # ~2.3 GB IR archive into ./device-db
cp .env.example .env && sed -i "s/change-me-in-production/$(openssl rand -hex 32)/" .env
docker compose up -d --build
curl http://localhost/healthz                   # {"status":"ok",...,"deviceDb":true}
```

The CA certificate is generated on first start in the `harmony-data` volume
(`/data/certs/ca.crt`).

Without Docker: `uv sync && uv run harmony-server --no-tls --http-port 8080`.
Tests: `uv sync --extra dev && uv run pytest`.

## 3. DNS override

Point the Logitech hostnames at the server, using your router, Pi-hole, or a hosts file
on a single PC. Short version (dnsmasq / Pi-hole):

```
address=/myharmony.com/192.168.1.10
```

Hosts file (legacy-app PC):

```
192.168.1.10 svcs.myharmony.com lb-svcs.myharmony.com cf-svcs.myharmony.com setup.myharmony.com
192.168.1.10 sl.dhg.myharmony.com content.dhg.myharmony.com sus.dhg.myharmony.com accounts.logi.com
```

Full host list, Pi-hole CNAME records, UniFi notes, and the DNS loop in proxy mode:
**[docs/dns-rewrite-setup.md](docs/dns-rewrite-setup.md)**.

## 4. TLS certificate trust

The server generates a private CA and a leaf certificate covering every served hostname.
Trust `ca.crt` on PCs and phones (`certutil -addstore Root`, the macOS keychain,
`update-ca-certificates`).

**A stock hub will probably not trust it** (untested). If so, use the root-repoint route
(section 7). Details: **[docs/tls-setup.md](docs/tls-setup.md)**.

## 5. Legacy MyHarmony app

With the DNS rewrite and the CA trusted, the app reaches this server: Discovery,
sign-in, and account/device/activity reads are answered. Large parts of the app's ~30
WCF services are still stubs or unimplemented, and we do not host Logitech's Silverlight
`.xap` files. Its most useful job today is generating captures in **proxy mode**. See
**[docs/legacy-app-setup.md](docs/legacy-app-setup.md)**.

## 6. New client configuration

The cross-platform client takes a **server URL** setting, e.g. `https://harmony.lan` or
`http://192.168.1.10`. It uses only the hostname-independent REST API:

```
POST /api/auth/register | /api/auth/login   {email,password} -> {token, accountId, hubToken}
GET  /api/hubs/                             list hubs
POST /api/hubs/claim                        {hubId, hubName, ipAddress?}
GET  /api/hubs/{id}/config                  devices + activities (engine?config shape)
PUT  /api/hubs/{id}/config[?notify_hub=true]  save -> configVersion bump (-> setup.sync)
POST /api/hubs/{id}/config/import-from-hub  copy the live hub's config into the server
GET  /api/hubs/{id}/status                  live status via aioharmony
POST /api/hubs/{id}/activity                {activityId, action: start|stop}
POST /api/hubs/{id}/command                 {deviceId, command, delay, repeat}
GET  /api/hubs/{id}/provision-blob          account?provision payload (hbus + /sys/hlapi)
GET  /api/devices/?q=  /api/devices/{mfr}/  /api/devices/{mfr}/{model}  /api/protocols/{name}
GET  /device-db/...                         raw IR archive files
```

`{id}` is the hub's `globalRemoteId`. Interactive docs are at `/docs`. Live hub
operations need the hub's LAN IP, which comes from `claim`, or from the hub's own
check-ins.

## 7. Hub root-repoint option

On a hub rooted with
[harmony-hub-root](https://github.com/Ripthulhu/harmony-hub-root), you can rewrite its
provisioning (`discoveryServer`) to point at this server, and/or add our CA to its trust
store. `GET /api/hubs/{id}/provision-blob` produces the payload. See
**[docs/hub-root-repoint.md](docs/hub-root-repoint.md)**.

## 8. Known gaps / help wanted

What is known and what is guessed, plainly:

| Area | Status |
|---|---|
| Discovery.svc as a **service locator** (SOAP `IDiscovery`) | **Known.** Contract from the decompiled client, response from a real capture. |
| What a **hub** sends to Discovery.svc (`clientTypeId`?) and which service it syncs from next | **Unknown.** |
| Config bundle **format** the hub downloads | **Unknown.** We serve the inverse of the hub's local `engine?config` (`activity[]`, `device[]`, `configVersion`, `contentVersion`). A real hub may reject it. Marked `TODO: validate against captured Discovery.svc packet`. |
| `getProvisionInfo` / `account?provision` | Shapes known from aioharmony and the decompiled client. Note: those are calls *to the hub*. Whether the hub also calls the cloud this way is unconfirmed. |
| WCF json2 bodies (ProvisionHub, GetHubCloudToken, StartCompile, Security signin, ...) | Operation names and response field names are known from contracts. Request bodies are guesses. |
| Whether stock hub firmware accepts our TLS cert | Untested. Probably not. |
| Firmware endpoint schema | Unknown. Answers "no update". |

**The single most useful contribution is a capture of a hub syncing with Logitech's
cloud, while it still exists.** Run `SERVER_MODE=proxy`, factory-reset a spare hub, and
set it up with the official app. The files in `/data/captures/` will hold those exact
requests. What we need, concretely:

1. The hub's request to `Discovery.svc`: SOAPAction, `clientTypeId`, `clientId`, and any
   auth header.
2. Every request after it, in order: host, path, headers (including how the
   `cloudAuth` token is presented) and body.
3. The config download response, in full (content-type, encoding, compression,
   signature if any), plus the `configVersion` and `contentVersion` the hub reports in
   `connect.stateDigest?notify` before and after.
4. The same for a config change made in the app (the StartCompile ->
   download -> RegisterSuccessfulSync sequence).

Scrub account data before you share captures.

## 9. Architecture

```
                LAN DNS override: *.myharmony.com, accounts.logi.com -> server
                                         |
   Harmony Hub --https/http--+           |            +-- New client (any URL)
   Legacy MyHarmony app -----+           v            |   /api/*
                       +-------------------------------------------+
                       |  uvicorn :80 + :443 (generated CA/cert)   |
                       |  [ProxyMiddleware]  SERVER_MODE=proxy:    |
                       |      relay -> real Logitech, record JSONL |
                       |  HostnameRoutingMiddleware                |
                       |      Host -> /_vh/<vhost>/... ; /api/*,   |
                       |      /device-db/* shared on all hosts     |
                       +-------------------------------------------+
     svcs ----------------- setup ------ logi ------ sus ----- sl/content
     | hbus POST /            | hbus      | Logi ID   | "no     | SPA placeholder,
     |  getProvisionInfo      |           |  signin   |  update"| /device-db
     |  provision (claim)     |           |           |
     | Discovery.svc (SOAP locator, captured directory | JSON config-sync stub)
     | CompileManager / RemoteManager / UserAccountDirector json2
     | Security.svc signin -> GetAuthenticatedUserAuthToken
     | legacy app data stubs (AccountManager, ActivityManager, ...)
     | anything else -> 501 + capture to /data/captures/<vhost>.jsonl
                       |                                   |
            +----------v---------+             +-----------v-----------+
            | SQLite (SQLModel)  |             | IR archive (static,   |
            | Account Hub        |             |  ./device-db, CC0)    |
            | HarmonyConfig      |<-compile----| codesets -> controlGroups
            | Device Activity    |             +-----------------------+
            +--------------------+
                       |
            local_control (aioharmony) --8088/5222--> Hub: status, activities,
                                                       IR commands, setup.sync
```

Code map: `src/harmony_server/` has `main.py` (app + CLI), `config.py`, `middleware/`
(vhost routing, proxy, TLS, capture), `provisioning/`, `discovery/` (locator + config
compiler), `auth/`, `hub/`, `device_db/`, `firmware/`, `local_control/`, and `db/`.

## License

Apache-2.0 for this code. The IR archive in `device-db/` is CC0-1.0 (the underlying codes
originate with Logitech). `discovery/data/discovery_services.json` is a factual capture
of Logitech's public service URLs, with an embedded Logitech API key removed. No
Logitech client IDs or keys are used.
