# TLS setup

On first start the server creates, in `/data/certs` (`$HARMONY_DATA_DIR/certs`):

| File | What it is |
|---|---|
| `ca.crt` / `ca.key` | Private "Harmony Revival Local CA" (RSA-2048, 10 years). Keep `ca.key` secret. |
| `server.crt` / `server.key` | Leaf certificate, valid 397 days. SANs cover every hostname in `HARMONY_VHOSTS`, `localhost`, `127.0.0.1` and `HARMONY_CERT_EXTRA_NAMES`. The file holds the full chain. |

The leaf is reissued automatically when its SANs no longer cover the configured hosts or
it is within 30 days of expiry. To force a new CA and leaf:

```sh
docker compose run --rm harmony-server --gen-certs
```

Get the CA out of the container:

```sh
docker compose cp harmony-server:/data/certs/ca.crt ./harmony-revival-ca.crt
```

## Trusting the CA

| Client | How |
|---|---|
| Windows (legacy MyHarmony app) | `certutil -addstore -f Root harmony-revival-ca.crt` (admin), or double-click -> Install -> Local Machine -> Trusted Root. The app uses the Windows store. |
| macOS | `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain harmony-revival-ca.crt` |
| Linux | copy to `/usr/local/share/ca-certificates/harmony-revival-ca.crt`, then `sudo update-ca-certificates` |
| Android / iOS | install the profile, then enable full trust (iOS: Settings -> General -> About -> Certificate Trust Settings) |
| New client | either trust the CA, or point it at `http://<server>:80` on a trusted LAN |

## The hub: the hard part

**Tested on hardware (Hub firmware 4.15.600): the hub rejects this CA for Discovery.**
The hub has two HTTPS clients. Its native client, used for `svcs.myharmony.com`
(Discovery.svc) and some `home` calls, aborts with `unknown_ca`. Its LuaSocket client,
used for the `sus` update check and the `home` time call, accepts any certificate. Details,
including both ClientHellos, are in [hub-findings.md](hub-findings.md).

So, of the possible outcomes:

1. **Discovery and config sync:** the hub validates against a bundle we cannot change, so a
   DNS rewrite alone does not work. You need root ([hub-root-repoint.md](hub-root-repoint.md)),
   either to add `ca.crt` to the hub's bundle or to repoint `discoveryServer` to a host you
   control.
2. **Update check and time:** no verification, so the DNS rewrite works as is.
3. Plain-HTTP calls: the hub opened one empty connection to port 80 during boot and sent
   no request. Port 80 is served anyway.

If your hub's firmware behaves differently, please report it with the version. The server
does **not** log TLS handshake failures (uvicorn drops them silently). Use
`tcpdump host <hub-ip>` and look for a TLS alert from the hub (`15 03 03 00 02 02 30` is
`unknown_ca`). `/data/captures/*.jsonl` shows what got through.

## Without TLS

`HARMONY_ENABLE_TLS=false` (or `--no-tls`) serves HTTP on :80 only. Fine for the new
client on a trusted LAN. Useless for anything hard-coded to `https://`.
