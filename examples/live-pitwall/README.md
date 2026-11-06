# Live Pitwall at Summit Point

**Spontaneous Rodents · #333 Ford Escort · 24 Hours of Lemons Capitol Offense · October 2026**

[Open the Live Pitwall demo](https://spontaneous-rodents-333-pitwall.awakegenet68.chatgpt.site) · [Event results](https://24hoursoflemons.com/results-2/?id=449)

At Summit Point, I fueled the car, tracked fuel use, timed stops, and forecast pit windows. The pitwall brings those inputs together in a browser so the crew can check when to prepare for a stop and see which assumptions drove the estimate.

## From lap log to pit window

1. Record completed laps and their conditions. The board can use Race Monitor timing or accept manual lap entries if the feed is unavailable.
2. Log the measured refill and the lap at which it happened. A refill can inform fuel burn when the fill levels are comparable. Lap times inform pace and the time until the projected stop.
3. Use estimated fuel on board, assumed fuel burn, and a reserve to forecast a latest pit lap. Update the estimate when new refill information or a change in race conditions warrants it.

## Worked example with synthetic inputs

At the end of lap **40**, assume **8.0 US gal** on board and a **2.0 US gal** reserve. At **0.20 US gal/lap**, the remaining budget is `floor((8.0 − 2.0) / 0.20) = 30 laps`. That puts the modeled pit target at or before the end of **lap 70** under steady conditions.

| Assumed burn (US gal/lap) | Whole laps above reserve | Projected pit lap |
| ---: | ---: | ---: |
| 0.18 | 33 | 73 |
| 0.20 | 30 | 70 |
| 0.24 | 25 | 65 |

The sample shows the effect of changing the burn assumption while holding the other inputs fixed. At the event, one approximate refill of **8.3 US gal** over a **48-lap interval** gave a mixed-condition estimate of **0.173 US gal/lap**. That interval included interruptions. The green-flag burn rate and usable tank capacity were unverified, so the board treats those as planning inputs.

This case study covers the browser tool for the real-world Lemons race. The [Indianapolis 8 Hour case study](../indy-8-hour/README.md) covers the separate Stint workstation and its iRacing telemetry analysis.
