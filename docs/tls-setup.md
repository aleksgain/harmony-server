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

**A stock Harmony Hub will almost certainly reject this CA.** Embedded firmware usually
ships its own trust bundle, and nothing indicates Logitech's hub accepts user-added roots.
This has not been tested on hardware yet. Possible outcomes:

1. The hub validates against a bundle we cannot change: DNS rewrite alone does not work
   for the hub. You need root ([hub-root-repoint.md](hub-root-repoint.md)), either to add
   `ca.crt` to the hub's bundle or to repoint `discoveryServer` to a host you control.
2. The hub skips verification, or pins nothing for some hosts: the DNS rewrite works as is.
3. Some calls are plain HTTP (several entries in Logitech's directory are `http://`).
   Port 80 is served too, so those work.

Please report which of these you see, with the hub's firmware version. The server logs
TLS handshake failures, and `/data/captures/*.jsonl` shows what got through.

## Without TLS

`HARMONY_ENABLE_TLS=false` (or `--no-tls`) serves HTTP on :80 only. Fine for the new
client on a trusted LAN. Useless for anything hard-coded to `https://`.
