# Repointing a rooted hub

If your hub does not trust our CA ([tls-setup.md](tls-setup.md)), or you would rather not
override DNS for the whole network, change where the hub looks instead.

**Risk:** this modifies your hub. A wrong provisioning blob can leave it unconfigurable.
Read the harmony-hub-root documentation first, and back up `/data` on the hub before you
change anything.

## 1. Root the hub

[`Ripthulhu/harmony-hub-root`](https://github.com/Ripthulhu/harmony-hub-root) (tested on
firmware 4.15.600) gets persistent root SSH over the LAN. It uses `harmony.log?put` to
write `/etc/tdeenable`, then `harmony.automation?discover` to load an installer. Follow
its README.

## 2. Look at the current provisioning

```sh
ssh root@<hub-ip> cat /data/luaworks/provision
# cloudAuth=svcs.myharmony.com;<accountId>;<globalRemoteId>;<token>
```

Or, without root, from any machine on the LAN:

```sh
curl -s -X POST http://<hub-ip>:8088/ \
  -H 'Origin: http://sl.dhg.myharmony.com' -H 'Content-Type: application/json' \
  -d '{"id":1,"cmd":"setup.account?getProvisionInfo","params":{}}'
```

## 3. Get a provisioning blob from harmony-server

Claim the hub, then ask for its blob. `discovery_host` is the name the hub should use from
now on: your server's LAN name, or `svcs.myharmony.com` if you keep the DNS rewrite.

```sh
TOKEN=$(curl -s https://harmony.lan/api/auth/login -H 'content-type: application/json' \
  -d '{"email":"me@example.com","password":"..."}' | jq -r .token)
curl -s https://harmony.lan/api/hubs/claim -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"hubId":"<globalRemoteId>","ipAddress":"<hub-ip>"}'
curl -s "https://harmony.lan/api/hubs/<globalRemoteId>/provision-blob?discovery_host=harmony.lan" \
  -H "authorization: Bearer $TOKEN"
```

The response holds the same `provisionInfo` in two envelopes:

* `hbus`: send it to the hub over 8088/5222 as
  `vnd.logitech.setup/vnd.logitech.account?provision`. The official MyHarmony client
  did exactly this (decompiled `ProvisionController.SaveProvision`).
* `hlapi`: write it through the USB HID file RPC to `/sys/hlapi`
  (`{"command":"account?provision","params":{"provisionInfo":{...}}}`).

`discoveryServer` is the redirection lever: the hub uses it for all later cloud contact.
Our sources disagree on its form. The decompiled MyHarmony client writes the full URL
(`https://svcs.myharmony.com/Discovery/Discovery.svc`); USB-side notes show a bare host
(`svcs.myharmony.com`). The blob uses the URL form and sets `domain` to the bare host.
If the hub rejects it, try the other form and tell us what happened.

## 4. Point the server at the new name

Make the service directory and the cert match the hostname you chose:

```sh
HARMONY_CERT_EXTRA_NAMES=["harmony.lan"]
HARMONY_SERVICE_URL_REWRITES={"https://svcs.myharmony.com":"https://harmony.lan","https://setup.myharmony.com":"https://harmony.lan"}
HARMONY_VHOSTS={"svcs":["svcs.myharmony.com","harmony.lan"],"setup":["setup.myharmony.com"],"sl":["sl.dhg.myharmony.com"],"content":["content.dhg.myharmony.com"],"sus":["sus.dhg.myharmony.com"],"telemetry":["logging.dhg.myharmony.com","home.myharmony.com"],"logi":["accounts.logi.com"]}
```

Then add `ca.crt` to the hub's trust bundle over SSH. Where that bundle lives depends on
the firmware; check harmony-hub-root's notes.

## 5. Ask the hub to sync

```sh
curl -X PUT "https://harmony.lan/api/hubs/<id>/config?notify_hub=true" ...
```

This sends `setup.sync` through aioharmony. Watch `/data/captures/svcs.jsonl` to see
what the hub requests next. Those requests are the missing config-sync schema.
