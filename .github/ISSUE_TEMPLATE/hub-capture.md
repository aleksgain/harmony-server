---
name: Hub packet capture
about: Submit a capture of MyHarmony and your hub talking to Logitech's cloud (Discovery.svc)
title: '[CAPTURE] Harmony Hub <model> firmware <version>'
labels: ['protocol-research', 'capture']
---

<!--
Thank you. Logitech's services are still online, and this traffic can only be
recorded while they are. Even a partial capture is useful.

READ "BEFORE YOU POST" FIRST. Raw captures contain your account password and
tokens that let anyone control your hub and your Logitech account.
-->

## Before you post

- **Do not attach a raw `.har`, `.flows`, `.pcap` or `.pcapng` to this public
  issue.** Post a redacted copy (see "Redaction" below). If you have an
  unredacted capture, say so here and a maintainer will arrange a private
  transfer.
- **Do not factory-reset a working hub just to capture first-time setup.** If
  setup fails, or Logitech's servers go away during it, the hub may be left
  unconfigured. Capture a normal sync first (Option A, step 5). Only capture
  first-time setup on a spare hub, or one you were going to set up again anyway.

## What we need

Traffic to these hosts, most important first:

1. `svcs.myharmony.com`, especially **`/Discovery/Discovery.svc`**: every
   request and response, with full headers and bodies (SOAP/XML or JSON)
2. `setup.myharmony.com` (account and provisioning)
3. `files.myharmony.com`, `content.dhg.myharmony.com` (downloads: URLs and
   headers are enough, not the files)

Best: first-time setup of a hub. Also useful: any sync after changing a device
or activity.

## Option A: MyHarmony app through mitmproxy or Charles (HAR)

This records what the **MyHarmony desktop app** sends to the cloud, decrypted.

1. Install [mitmproxy](https://mitmproxy.org/) (or
   [Charles Proxy](https://www.charlesproxy.com/)) on the PC that runs MyHarmony.
2. Start it, writing a HAR file:
   `mitmweb --listen-port 8080 --set hardump=harmony-capture.har`
   (Charles: Proxy → SSL Proxying Settings → add `*.myharmony.com:443`.)
3. Set the system proxy to `127.0.0.1:8080` (Windows: Settings → Network →
   Proxy; macOS: System Settings → Network → Details → Proxies).
4. Install and trust the proxy's CA certificate. With mitmproxy, open
   <http://mitm.it> through the proxy. On Windows, install it into
   **Trusted Root Certification Authorities** for the current user.
5. Start MyHarmony, sign in, then either:
   - **Sync**: change one device or activity, then sync the hub. Or
   - **First-time setup**: add a hub (see "Before you post").
6. Stop the proxy so the HAR is written. (Charles: File → Export Session → HAR.)
7. **Remove the CA certificate and the proxy setting afterwards.**

If MyHarmony refuses to connect while proxied, say so below. That means it pins
certificates, which is useful to know too.

## Option B: Wireshark of the hub itself (pcapng)

The hub talks to the cloud itself, and it will not trust your proxy's
certificate. A plain capture still shows which hosts and ports it uses, whether
any of the traffic is plain HTTP, and the LAN traffic between app and hub (ports
8088 and 5222), which is unencrypted.

1. Capture on a machine that can see the hub's traffic: a mirrored switch port,
   or your router (`tcpdump -i br0 -w hub.pcapng host <hub-ip>`).
2. Filter: `host <hub-ip>`, or
   `host svcs.myharmony.com or host setup.myharmony.com` for cloud traffic only.
3. Trigger a sync as in Option A, step 5.

## Redaction

Replace each value below with a placeholder that shows its type, such as
`<TOKEN>`, `<EMAIL>`, `<ACCOUNT_ID>` or `<REMOTE_ID>`. If one value appears in
several places, use the same placeholder each time so the flow can still be
followed.

- [ ] Your Logitech account **email and password** (the sign-in request body)
- [ ] `Authorization`, `Cookie` and `Set-Cookie` headers, and any `token`,
      `sessionToken`, `cloudAuth` or `authToken` value
- [ ] `accountId`, `globalRemoteId`, `activeRemoteId`, `hubId`, and the hub's
      serial number
- [ ] Wi-Fi SSID and password, if they appear
- [ ] Public IP addresses, and MAC addresses if you prefer

Do **not** change anything else: field names, element order, versions, URLs,
paths and status codes are what we need.

## Hub info

- Hub model (Hub, Elite, Companion, Pro 2400, …):
- Firmware version (shown in MyHarmony):
- MyHarmony version, and OS it ran on:
- What you did (first-time setup, or sync after a change):
- Date and time captured (UTC):
- Capture method (Option A mitmproxy / A Charles / B Wireshark):

## Capture

<!-- Attach the redacted .har / .pcapng (zip it if GitHub rejects the extension),
     or paste the decrypted Discovery.svc requests and responses here. -->

## Anything odd

<!-- Errors, certificate warnings, retries, steps that hung. -->
