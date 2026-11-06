# Remote pitwall validation

## Two-PC simulator test

On October 13, 2026, a teammate used the engineer role over private Tailscale HTTPS to view timing, live telemetry, and the map from a second PC. The driver armed pit control on the racing PC. The engineer then requested **50 L of fuel and left-front and left-rear tires** for a Corvette Z06 GT3.R at Indianapolis Road Course in a Practice session.

The journal recorded a confirmed command after subsequent SDK telemetry showed the requested fuel amount and all four tire flags. The driver also reported seeing the matching settings in the iRacing black box. This test verified the next-stop selections.

On October 14, a parked-car test checked SDK readback for fuel, individual tire flags and pressures, dry and wet compounds, tear-off, and fast repair. The driver restored the original selections afterward and confirmed receipt of a private in-sim confirmation message. The controller blocks commands in the pit stall and during active service.

Race-mode Auto Fuel activation remains unverified in the simulator. The SDK exposes the Auto Fuel checkbox but provides no readback for the margin macro.

## Reproduce the automated checks

| Check | Coverage |
| --- | --- |
| `python -m unittest discover -s tests -v` | Identity and origin checks, driver arming and revocation, session changes, stale data, command timeouts, and partial readback. |
| `python tests/browser_remote.py` | Desktop and mobile flows via a local HTTPS identity proxy and fake SDK. Requires Playwright, Edge, and cryptography. |
| `python tests/validate_remote_endurance.py` | Eight hours of simulated 20 Hz data with eight concurrent remote streams, bounded buffers, and a separate 64-car ingest and snapshot check. |

For a live setup, follow the [private remote pitwall guide](docs/GUIDE.md#private-remote-pitwall-and-pit-selections).
