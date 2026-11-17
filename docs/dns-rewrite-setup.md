# DNS rewrite setup

Goal: every Logitech hostname resolves to the machine running harmony-server, for the
devices that should use it (hub, PC running the legacy app). Everything else on your
network can keep using normal DNS.

## Hostnames

Point all of these at the server (`192.168.1.10` below):

| Hostname | Served by harmony-server as |
|---|---|
| `svcs.myharmony.com` | Discovery.svc, provisioning, WCF json2 services |
| `lb-svcs.myharmony.com` | alias of svcs (MyHarmony uses it for hubs on firmware < 4.14.18) |
| `cf-svcs.myharmony.com` | alias of svcs (CloudFront Discovery entry) |
| `setup.myharmony.com` | provisioning / account |
| `sl.dhg.myharmony.com` | web client (placeholder for now) |
| `content.dhg.myharmony.com`, `files.myharmony.com` | static content, device DB |
| `sus.dhg.myharmony.com` | firmware update check (always "no update") |
| `logging.dhg.myharmony.com`, `home.myharmony.com` | telemetry sink, time |
| `accounts.logi.com`, `id.logi.com` | Logi ID sign-in (step 1 of the auth chain) |

`accounts.logi.com` is shared with other Logitech products. Only override it for the
clients that need it (per-client DNS, or `/etc/hosts` on the legacy-app PC).

Logitech's own service directory lists more hosts (`perrier-svcs`, `ems`, `geo`,
`appcompatibility`, ...). Requests to hosts we do not know are not rewritten, so they
fail. Add a host to `HARMONY_VHOSTS` if a capture shows a client needs it.

## Pi-hole

Pi-hole v6: *Local DNS -> DNS Records* takes one A record per name. Add an A record for
`svcs.myharmony.com`, then CNAME records for the rest:

```
svcs.myharmony.com        A      192.168.1.10
setup.myharmony.com       CNAME  svcs.myharmony.com
sl.dhg.myharmony.com      CNAME  svcs.myharmony.com
content.dhg.myharmony.com CNAME  svcs.myharmony.com
...
```

Or put a wildcard in a dnsmasq drop-in (`/etc/dnsmasq.d/99-harmony.conf`; on v6 set
`misc.etc_dnsmasq_d = true`):

```
address=/myharmony.com/192.168.1.10
```

The wildcard also catches hosts we do not serve. That is usually fine: requests that
would have failed anyway now fail locally, and the unknown-request capture logs them.

## Router (dnsmasq-based: OpenWrt, UniFi, AsusWRT-Merlin)

Same dnsmasq line, in the router's custom dnsmasq config. On UniFi Network, add
*Settings -> Routing -> DNS* "Host (A)" records one by one. UniFi does not take
wildcards there.

## Local hosts file (legacy app PC)

`C:\Windows\System32\drivers\etc\hosts` or `/etc/hosts`:

```
192.168.1.10 svcs.myharmony.com lb-svcs.myharmony.com cf-svcs.myharmony.com
192.168.1.10 setup.myharmony.com sl.dhg.myharmony.com content.dhg.myharmony.com
192.168.1.10 files.myharmony.com sus.dhg.myharmony.com logging.dhg.myharmony.com
192.168.1.10 home.myharmony.com accounts.logi.com
```

A hub cannot use a hosts file. It takes DNS from DHCP, so the override has to be on your
resolver, or on the hub itself if it is rooted ([hub-root-repoint.md](hub-root-repoint.md)).

## Verify

```sh
dig +short svcs.myharmony.com            # -> 192.168.1.10
curl -k https://svcs.myharmony.com/healthz
```

Some devices ignore DHCP-provided DNS or use DoH. If the hub's traffic never arrives, look
at your firewall: block outbound 53/853 from the hub's IP, or redirect its DNS with NAT.

## Proxy mode and the DNS loop

In `SERVER_MODE=proxy` the server forwards to the real Logitech cloud. If the server
host uses the overriding resolver itself, it will resolve those names to itself. Set
`HARMONY_PROXY_UPSTREAM_IPS` (look up the real IPs with `dig @1.1.1.1 svcs.myharmony.com`),
or give the server host an upstream resolver that is not overridden.
