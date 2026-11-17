# Using the legacy MyHarmony desktop app

**Status: partial. Expect the app to stop somewhere after sign-in.** This page explains
what works, what does not, and how to collect captures that will fix the rest.

## How the app finds the cloud

The launcher downloads the Silverlight app from `setup.myharmony.com/MartiniWeb/...`. The
app gets `discoveryServiceUrl=https://svcs.myharmony.com/Discovery` in its init params,
calls `Discovery.svc` (`GetJson2Uris("HarmonyTouch", "")`) for the URL of every other
service, then talks to roughly 30 WCF services: AccountManager, UserAccountDirector,
CompileManager, ActivityManager, DeviceManager, and others.

## What harmony-server answers

| Piece | Status |
|---|---|
| `Discovery.svc` (all five `IDiscovery` operations, SOAP) | **Real.** Serves the captured Logitech directory, optionally rewritten. |
| Sign-in chain: `accounts.logi.com/identity/signin` -> `Security.svc/json2/signin` -> `GetAuthenticatedUserAuthToken` | Implemented against our user store. Field names are best guesses. |
| `AccountManager GetMyAccount / GetMyHousehold` | Stub backed by our DB. |
| `UserAccountDirector GetDeviceList / GetActivities / GetButtonMaps` | Stub backed by the stored config. |
| `ActivityManager GetActivities / SaveActivities`, `UserButtonMappingManager GetButtonMaps / SaveButtonMaps` | Reads are stubbed. Saves are **captured, not applied**. |
| `CompileManager StartCompile / IsRemoteSyncRequired / GetConnectedRemoteConfig` | Stub. Hands out the stored bundle. |
| `UserAccountDirector ProvisionHub / GetHubCloudToken / GetGlobalRemote`, `RemoteManager *Sync*` | Stub. |
| `DeviceManager` (device search, commands), `ProductManager`, the rest | **Not implemented.** Captured with a 501. |
| The Silverlight `.xap` files themselves | **Not hosted.** They are Logitech's code; we do not redistribute them. |

## Steps

1. Run harmony-server ([README](../README.md#quick-start)).
2. On the PC, add the hosts-file lines from [dns-rewrite-setup.md](dns-rewrite-setup.md).
3. Trust the CA ([tls-setup.md](tls-setup.md#trusting-the-ca)).
4. The app is broken until someone serves the `MartiniWeb` app files. Until then, the
   useful run is **proxy mode**, while Logitech still answers:

   ```sh
   SERVER_MODE=proxy HARMONY_PROXY_UPSTREAM_IPS='{"svcs.myharmony.com":"<real ip>", ...}' \
     docker compose up
   ```

   Use the app normally. Every request/response pair is written to
   `/data/captures/proxy-<vhost>.jsonl`, and those exact schemas are what fill the stubs.
   Captures contain your account data. Auth headers are redacted by default; set
   `HARMONY_CAPTURE_REDACT_HEADERS=[]` only if you need token formats, and scrub before
   sharing.
