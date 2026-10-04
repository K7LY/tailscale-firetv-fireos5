# Network detection and recovery

Patch `0009-recover-legacy-network-changes.patch` addresses network state handling in
Tailscale Android 1.98.8 and keeps VPN conflict detection compatible with API 22.

## Behaviour

- Below API 26, `onAvailable` reads current capabilities and link properties. Older
  Android may omit the subsequent initial callbacks, leaving upstream without a
  default network even when Wi-Fi is connected.
- API 26+ waits for the ordered capabilities and link-properties callbacks instead
  of publishing an empty route from the initial placeholder state.
- Capabilities, link properties, and network loss update DNS and notify native
  network monitoring even when the DNS text has not changed.
- Losing the final network clears DNS and sends an empty route. Reconnection sends
  the newly selected interface.
- A burst of notifications retains the latest route rather than discarding it when
  the one-element Go channel is full. Backend processing preserves notification
  order instead of starting a goroutine for each route update.
- VPN conflict detection uses `allNetworks` and `getNetworkCapabilities`, both
  available from API 21, instead of calling API-23 `getActiveNetwork` on API 22.

The socket-protection fallback in patch 0004 remains necessary on API 22.

## Verification

Build and run Android unit tests with:

```sh
TS_REF=1.98.8-t1241b225b-gbcbaf1889 MIN_SDK=22 SKIP_TESTS=0 ./scripts/build.sh
```

Robolectric tests cover API 22 initial availability, capability loss and recovery,
unchanged-DNS route changes, final-network loss, and API 28's initial callback order.
Separate tests cover VPN detection without the newer default-network API.

The queue can be tested without Android's C headers by running this in the patched
upstream checkout:

```sh
./tool/go test -race -count=100 libtailscale/network_change_queue.go libtailscale/callbacks_test.go
```

Device checks used SH-01J, Android 5.1.1 / API 22 / ARMv7, build 01.02.06, with this
patch and the separate boot recovery patch installed. Initial Wi-Fi detection,
authentication, bidirectional peer traffic, HTTP over the VPN, and recovery after
Wi-Fi off/on were verified.

Fire OS, other physical devices, multi-uplink operation, and long-duration sleep
remain unverified. The API 28 test covers callback sequencing in Robolectric rather
than a current Android physical device.
