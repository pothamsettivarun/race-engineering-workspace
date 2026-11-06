# Stint driver and engineer guide

Double-click **Start Stint.cmd**. It opens **http://127.0.0.1:8765** in your browser.
Keep the server window running while using the app. Close it or press Ctrl+C to stop.

## Live pitwall (v0.5)

Open **Live** while iRacing is running. Stint connects automatically through the local,
iRacing SDK. Monitoring works without IBT import, disk logging, an AI connection, or internet access. Collection continues while other worksheets are open. A fresh sample
is collected at 20 Hz and browser updates arrive at 10 Hz. Data older than two
seconds is marked stale and predictions pause; replay also pauses predictions.

**Pitwall** combines overall/class timing, selected-car lap and pit history, a live
car map, player telemetry, fuel, pit-service selections, incidents, repairs, conditions,
and alerts. Select a timing row or map marker to highlight the same car in both
views. Race classification gaps come from the SDK; track-relative gaps and pit rejoin
are estimates. Observed pit counts do not include stops before collection started.
The map uses exact track/configuration geometry: Indy Road Course and Watkins Glen
Boot are included, and valid GPS
outlines are cached from existing and newly imported recordings. Unknown circuits
show a labeled lap-progress diagram until a matching recording is imported.

**Fuel and next stop** shows **Last lap**, **Session average**, and **Last 5 median**
consumption, laps to empty, and estimated pits to end. Last lap and session average
start after one clean complete lap. Session average includes all eligible laps
observed by Stint, including across a collector restart; last 5 median requires at
least three eligible laps. An excluded last lap stays unavailable rather than
silently using an older lap. **Edit race plan → Fuel calculation basis** selects
which row drives fuel reserve, range, finish requirements, and strategy; last lap
is the default. Explicit pace/burn inputs override measured values. Laps to empty
uses all remaining fuel; the range below it retains the selected reserve. Pits to
end expresses additional fuel in tank loads above reserve, with a fuel-only minimum
stop count. Actual feasible stop laps are checked in Strategy. Fuel range works in
testing without a race target; finish fuel and pits to end need a target.

**Strategy** compares suggested fuel plans and editable scenarios. Pit, incident, off-track, refueling,
caution, partial, and discontinuous laps are excluded. Practice/testing needs an
explicit total race-lap or time target. Reserve defaults to one lap, and timed-race
estimates include one additional finish lap. Enter pit-lane loss versus a normal
lap, refueling rate, tire time, driver-change time, and service concurrency where
applicable. Missing inputs leave timing and ranking unavailable. Suggested fuel
plans retain the current driver; copy a suggestion to edit stop laps, fuel, tires,
and driver assignments. Add the current driver and teammates to enforce availability,
maximum continuous stint, and minimum total observed driving-time constraints.
Choose **Save & use for alerts** to track the next stop and driver change.

These are constant-pace/consumption projections, not a globally optimized race model.
They do not predict weather, tire degradation, rival fuel, or rival stops. Driver
time and stint history cover observed running; driving before connection and coverage
gaps are not credited. Tire temperatures and wear are explicitly pit-updated readings,
and cold pressures are garage settings. Real-time tire surface temperatures, pressures,
and rival pedal/fuel data are not fabricated. Sounds start muted; enable **Sound on**
for warning tones. Stint timers start when collection observes the car or driver.

**History** stores lap/fuel summaries, observed field laps, pit visits, stints,
driver changes, condition summaries, alerts, coverage gaps, and plan revisions in
`data/live-sessions`. Atomic checkpoints occur on significant events and every 15
seconds; the race journal retains older entries beyond bounded live-memory windows.
The same unfinished session can resume after restart with its uncovered interval
marked. Browser charts retain 60 seconds and are not saved as full telemetry; continue
using Alt+L and IBT imports for detailed post-session engineering.

The workspace remains localhost-only. Optional private remote Live access and explicit
engineer pit selections are described below. Scheduled stops never execute automatically.
Live AI and external map services are not included. Windows requires `pyirsdk==1.3.5`
(installed by the launcher). Other platforms can still use recorded-session analysis.

Additional checks: `python tests/browser_live.py`, `python tests/browser_fuel.py`, and
`python tests/validate_live_endurance.py`. Both use synthetic feeds and temporary data;
the endurance check simulates eight hours at 20 Hz and checks a 64-car load.

## Private remote pitwall and pit selections

Remote access starts disabled. Open **Live → Remote pitwall** on the racing PC to
configure allowed Tailscale logins and designate one engineer. Viewers can monitor
Live, strategy, and saved race history; the engineer can also save strategy revisions.
Local and remote edits use revision checks: after a conflict, reload the saved plan
and review it before saving again. Your imported recordings, engineering library,
AI connection, and workspace administration are available only on the racing PC.

1. [Install Tailscale for Windows](https://tailscale.com/download/windows) on both PCs
   and sign in. Each person should use their own Tailscale account.
2. In the [Tailscale machines console](https://login.tailscale.com/admin/machines),
   share the racing PC with the engineer and have them accept the invitation. Add
   viewers in the same way. Keep the racing PC owned by a user: tagged clients do
   not provide the user identity this app requires. See
   [Tailscale sharing](https://tailscale.com/docs/features/sharing) and
   [Serve identity headers](https://tailscale.com/docs/features/tailscale-serve).
3. Restrict the invited users to TCP port **8443** in your tailnet access policy,
   using the example below. Device sharing and Stint's allowlist are separate:
   a person needs both network access and an allowed login.
4. With Stint running, open PowerShell in this project folder and run:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File ".\Setup Remote Pitwall.ps1"
   ```

   Follow Tailscale's HTTPS consent prompt if shown, then rerun the helper. It
   inspects existing Serve configuration, refuses a conflicting 8443 service or
   public Funnel, and adds only `https://your-racing-pc.your-tailnet.ts.net:8443`
   forwarding to `http://127.0.0.1:8766`. It preserves other Serve services.
   [Serve CLI reference](https://tailscale.com/docs/reference/tailscale-cli/serve).
5. In **Live → Remote pitwall**, refresh status, verify the HTTPS address and
   logins, select **Enable private remote access**, and save. Give the engineer
   the displayed link; they open it while connected to Tailscale. Status lists
   recently connected users and their roles. Up to eight remote tabs can stream
   simultaneously; close unused tabs when all streams are occupied.
6. Enter the car and choose **Enable pit control** locally. The engineer edits
   **Fuel**, **Begin fueling**, **Auto Fuel / margin**, individual tires and pressures,
   **compound**, **tear-off**, and **fast repair**, then chooses **Apply to car**.
   **Load next planned stop** only fills the draft and rounds fuel upward to whole
   liters; it does not send a command. Review the amount before applying.

Only the dedicated loopback listener trusts Tailscale's authenticated identity
headers. Remote writes require the configured HTTPS origin. Serve supplies the
identity; there is no additional Stint password. Public Funnel, port forwarding,
and exposing the main workspace listener are outside this configuration.

An example network grant for invited users is below. Replace `100.101.102.103`
with this racing PC's Tailscale IPv4 address and merge the entries into your
existing policy. Preserve unrelated rules. Review any other matching grants or
ACLs: access rules are additive, so an existing broad allow can still grant other
ports. If the engineer is a member of your own tailnet, use their login as the
source and review their existing grants as well. Validate the policy in the
Tailscale console. [Grants syntax and shared-user selector](https://tailscale.com/docs/reference/syntax/grants).

```json
{
  "hosts": { "stint-racing-pc": "100.101.102.103" },
  "grants": [
    {
      "src": ["autogroup:shared"],
      "dst": ["stint-racing-pc"],
      "ip": ["tcp:8443"]
    }
  ]
}
```

Pit control resets after server restart, session change, or detected driver change.
**Disable pit control** revokes it immediately; disabling remote access also disarms
it. Disconnection suspends commands and cancels pending requests. After recovery,
the engineer must explicitly apply a new request. Replay, stale data, an absent
driver, missing required channels, and active pit service block commands.

Fuel requests use whole liters within the SDK's permitted capacity. **Begin fueling**
controls the refueling checkbox independently of the saved amount; zero clears
the saved amount and disables refueling using a bounded relative fuel macro. Pressure inputs use
whole kPa; iRacing enforces car limits, and telemetry readback exposes clamping.
Changed pressures appear in blue with **Was … kPa** below, compared with the
current cold-pressure setting. The note survives Apply and clears when the values
match. If cold-pressure telemetry is unavailable, the initial next-stop selection
is used as the comparison for that session.
The compound list comes from the active car; changing compound selects all four
tires. Tear-off and fast-repair selections have separate controls, and remaining
fast repairs are shown when available. Actual selections are shown beside the draft.
Auto Fuel uses iRacing's documented macro and is available only when the SDK
reports fuel data ready in a Race session. Its margin has no SDK readback, so it
is explicitly labeled as sent and requiring a black-box check. A margin is sent
only when edited or when enabling Auto Fuel. Pit-stall and active-service states
block Apply to car. A request
is **Pending** until subsequent telemetry confirms it within two seconds. Results
can be **Confirmed**, **Partially applied**, or **Unconfirmed/failed**. Delivery to
the SDK alone is not confirmation. Stint never retries or rolls back a partial
batch and does not reassert selections after manual changes. Check the actual
values before applying a new request.

Confirmed selections queue a private iRacing message to the current driver's own
SDK car number: `(Stint) Pit selections applied by Race Engineer: ...`. The name comes
from the allowed Tailscale user, never from the command body. Local applications
are attributed to Local driver. Pending, failed, or partially confirmed selections
do not send a success message. Chat delivery is tracked separately from pit
readback, and failure does not repeat or roll back a pit command.

Settings and allowed users persist in `data/remote-settings.json`; control
authorization does not. Race history records local authorization/revocation,
engineer identity, requested selections, observed results, and command failures.
This release supports one racing PC. Team handovers between racing PCs and automatic scheduled pit commands remain
future work. Car-specific pit-stop adjustments are outside the current control scope.

To shut down just Stint's Serve endpoint while preserving other services:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\Setup Remote Pitwall.ps1" -Disable
```

Remote verification: `python tests/browser_remote.py` checks engineer/viewer
workflows, concurrent edits, confirmation, reconnect/revocation, and desktop/mobile
layouts using a local HTTPS identity proxy and fake SDK. It requires Playwright,
Microsoft Edge, and `cryptography`. `python tests/validate_remote_endurance.py`
simulates eight hours at 20 Hz with eight remote streams and checks bounded memory
buffers and a 64-car load. Unit tests include command delivery/readback failures,
security boundaries, and lifecycle changes. Before race use, verify pit commands
and black-box readback in an offline session, then test the complete connection
from the engineer's PC over Tailscale; synthetic tests do not establish those checks.

**Real remote acceptance completed on October 13, 2026:** A teammate accessed Live
telemetry, timing, and the map from his PC over Tailscale and applied fuel/tire
selections. The driver verified the changes in iRacing's black box. The race journal
independently records a confirmed 50-liter request with LF/LR tires selected and
RF/RR unselected in the Corvette at Indianapolis Road Course. See
[simulator acceptance](../REMOTE_ACCEPTANCE.md) for the evidence and tested scope.

## Compare laps from different recordings

Open **Lap comparison** for a separate workspace with independent **Selected** and
**Reference** IBT and lap selectors. Import into either slot or choose an existing
recording. Each defaults to its fastest clean lap; other complete laps remain
selectable with their incident, pit, or exclusion warnings. Both recordings must
use the same track ID and configuration. Different cars are allowed with a warning.
The existing Telemetry lap overlay and setup-focused Compare Stints worksheet remain available.

The segment table starts with the largest time losses. **Selected minus reference**
is the sign convention: positive means time lost, negative means time gained.
Sorting also supports largest gains, absolute difference, and track order. Select
a segment to highlight its map range, zoom the traces, and inspect both laps’
entry/exit and minimum/maximum speeds, maximum brake percentage, and pedal durations.
Measurements use retained original samples; displayed charts are reduced traces.
Comparison charts use the same order and colors as Telemetry: speed blue, throttle
green, brake red, steering cyan, with white reference traces. Each header shows
**▼ minimum / ▲ maximum / ◆ arithmetic average** for the visible range, separately
for each recording and lap. Delta statistics align original selected timestamps
with the original reference recording by lap distance; they exclude extrapolation,
reference gaps, and synthetic finish samples. Map labels stay at a compact screen
size when zooming.
Segment timing uses linearly interpolated original timestamps at shared distance
boundaries, with the imported lap’s first/next-lap timestamps anchored at 0/100%.
Segments cover the lap once and their deltas add to the total. Telemetry gaps or
backward progress prevent alignment. Summary-only sessions need **Attach original IBT**.

Confirmed corner profiles seed a shared, non-overlapping corner/straight map.
Otherwise steering/braking estimates seed editable corner zones; these can include
corner complexes and are not official corner numbers. **Edit segments** lets you
rename, add/delete, or adjust corners numerically or by dragging map handles.
Straights fill the gaps automatically. A start greater than the end crosses the
finish. **Apply draft** recalculates; **Save for track** reuses the boundaries for
that exact configuration across recording pairs. **Reset map** restores the
confirmed profile or automatic detection. Comparison boundaries and their revisions
are stored separately in `data/comparison-segments`; numbered analysis profiles
are unchanged. Recording/lap selections are remembered in this browser.

The map overlays the selected laps’ recorded GPS trajectories at a shared geographic
scale when usable Lat/Lon samples exist. Toggle paths, pan, or zoom to inspect them.
Without lap GPS, a matching saved circuit outline is used when available; otherwise
a labeled lap-progress diagram is shown. The **Driving line close-up** follows corner selection and chart hover, using a shared
meter scale and showing the distance between the two paths at the same lap distance.
Selected is solid blue; reference is solid white. The paths continue through the
close-up frame rather than ending at the selected distance window. Its +/− controls zoom the local
view, and **Whole segment** fits the entire selected segment. **Aerial track** places
both paths over georeferenced photographs of the actual pavement and curbs in the
overview and close-up. The current source is USGS/USDA NAIP imagery for the continental
US. The first load needs internet access; the image is cached by track configuration
in `data/track-imagery` and reused offline. Only the track's geographic bounding box
is requested from the public imagery service; lap samples are not uploaded. The source
and attribution are shown beneath the controls. Unsupported locations or failed
requests retain the GPS view and offer a retry. Real-world imagery can differ from
the simulator's layout or GPS alignment. GPS is a display aid, not a surveyed line
measurement. Review fuel, tire state, traffic, and conditions before interpreting
pace differences. Missing channels display unavailable values.

Checks: `python tests/test_lap_comparison.py` and
`python tests/browser_lap_comparison.py` use isolated synthetic recordings.
`python tests/test_track_imagery.py` checks image georeferencing, caching, offline
fallback, retry, and local image endpoints without external downloads.

## First track test

1. Use one car and track. Record the setup name and actual garage settings. An open-setup test session is required for chassis changes.
2. Press **Alt+L** in iRacing to enable disk telemetry. Drive an outlap and **4–6 clean timed laps**.
3. Press **Alt+L** to stop logging. Wait a few seconds for the file to finish writing.
4. In Stint, click **Import telemetry**, then select the completed `.ibt` from the detected iRacing folder or file picker.
5. Review the **Setup** worksheet, confirm that it matches the run, then open **Engineer** and fill in the engineering brief: objective, vision, corner/phase feedback, and relevant settings. Click **Save brief & update findings**.
6. Inspect laps, pedal and chassis traces, heuristic driving zones, pace variation, and condition timelines. Exclude eligible laps affected by traffic or other issues using the lap table in **Stint**.
7. Apply one human-reviewed change in the iRacing garage. Record the old/new values and repeat a comparable run.
8. Import the new `.ibt`, record the change, then use **Compare** to select the baseline. Review pace, fuel/condition differences, and setup differences; record keep/revert/inconclusive.

## AI and your engineering library

The **local analysis engine is deterministic**, not a pretrained proprietary race engineer. It produces measured findings, cautious driver-feedback hypotheses, and controlled test suggestions without internet access.

**ChatGPT subscription is the default connection.** Open **Connect AI**, choose **ChatGPT subscription · local Codex**, and use the existing signed-in account or click **Sign in with ChatGPT**. Finish the official browser flow and click **Use this connection**. This uses your plan's Codex access and limits, not API-key billing. The bridge uses the [official local Codex app-server](https://learn.chatgpt.com/docs/app-server); it is not a commercial/hosted authentication service. Codex owns token storage and refresh; Stint never reads or copies login tokens. An API-authenticated Codex account is not silently accepted as subscription access.

The official Codex CLI or desktop app must be installed. Stint prefers the current desktop app binary to an older npm CLI shim. When signed in, the model selector lists available Codex models; **Use Codex default model** preserves your configured default. The app-server protocol has beta/experimental parts and may need compatibility updates after Codex upgrades.

The engineering process uses an ephemeral Codex thread with an explicit read-only permission profile. Shell execution, connectors, plugins, delegation, memory lookup and web search are disabled, and any executable-tool approval is declined. The model works from supplied measurements and source excerpts. Stint stores its engineering response with the run; it does not create a separate user-visible Codex chat.

For an alternative connection, choose **OpenAI API · separate billing** and enter your API key and a [Responses API](https://developers.openai.com/api/reference/python/resources/responses/methods/create) compatible model (default `gpt-4.1-mini`). The key stays in server memory and is never saved or returned by the app. Alternatively set `OPENAI_API_KEY` and optionally `OPENAI_MODEL` before starting. Switching to the API is explicit; a subscription failure never triggers API billing.

When you ask a question, selected summaries, setup, feedback, condition summaries, recent conversation, and up to five matching private-library excerpts are sent to OpenAI. The complete `.ibt` stays local; requested consecutive sections of original numerical samples can be sent through the read-only evidence tools. ChatGPT-plan usage follows that account's applicable data controls. API requests use `store: false`; this is not a promise of zero retention by the API provider. The UI makes this data flow explicit.

Add engineering notes, manual excerpts, or video transcripts under **Library**. Text and Markdown are supported in the UI. Use `ingest_sources.py` to import the Corvette manual and three video transcripts; see [source provenance](../SOURCES.md). Retrieval preserves PDF page numbers and video timestamps, skips unavailable sources, filters tagged car-specific material when the measured car differs, and uses transparent keyword scoring. It is not a fine-tuned model or an exhaustive source search. Responses and retrieved excerpts are retained with the run.

## Indy 8-hour preparation

The [Indianapolis 8 Hour case study](../examples/indy-8-hour/README.md) documents the Corvette development sessions. Use **Engineer** to record the objective and driver feedback for a new run. An optional local `data/project.json` can seed the objective and vision for matching imports. Weather, stint duration, fuel targets, event BoP, and driver preferences come from your testing or event information.

The manual dates from June 2024. Confirm current garage adjustment limits and simulator behavior. YouTube sources use English auto captions with timestamps; transcription errors are possible, and visual demonstrations are not analyzed. **Inspect imported text** in the library shows the actual material available to the engineer.

To refresh those exact sources, install the optional source dependencies into the project, then run the importer:

```powershell
python -m pip install --target data/source-tools -r requirements-sources.txt
python ingest_sources.py
```

## Data and accuracy

- Files are read without modifying the originals. Runs, metadata, notes, and AI responses are stored in `data/` in this project. Imported dense chart traces are resampled; metrics are calculated from the original selected scalar samples. Back up `data/` to preserve your work.
- Session YAML provides a single setup snapshot that may reflect the end of the recording. It does not prove each lap used those settings. Garage tire readings, aero calculator outputs, and measured static ride heights are clearly separate from continuously logged channels.
- Complete-lap timing uses consecutive measured start-line sample timestamps and is subject to the original sampling resolution. Partial/outlaps, pit lane, off-track, incident increments, sample gaps, and discontinuities are flagged. Traffic and tire age are not automatically known. Flagged laps cannot be promoted as clean.
- Charts use lap-distance percentage and validated numbered corners for Indy track 448 / Road Course and Watkins Glen track 434 / Boot. Unknown configurations retain heuristic zones. Steering demand or coasting is not a diagnosis of understeer by itself.
- TrackTemp and TrackTempCrew are preserved separately. Missing measurements are shown as unavailable. Condition mismatch thresholds flag comparisons; no invented weather correction or tire degradation model is applied.
- Setup advice stays conditional when settings, allowed ranges, adjustment direction, or causal evidence are unknown. No `.sto` writer, automatic garage control, or definitive optimal setup generation is implemented.
- Direct `.ibt` is supported. `.ld` support is on the future backlog. The parser follows the published iRacing SDK disk structure, cross-checked against the [pyirsdk reference](https://github.com/kutu/pyirsdk/blob/master/irsdk.py), and validates file bounds before reading.

## Developer commands

```powershell
python -m pip install -r requirements.txt
python server.py --open
python -m unittest discover -s tests -v
```

Dependencies: Python 3.11+, NumPy, PyYAML. ChatGPT subscription integration additionally requires Codex. No frontend installation or internet-hosted chart assets are required. A custom telemetry folder can be set with `IRACING_TELEMETRY_DIR`. The server binds only to loopback and rejects cross-origin browser requests. Run the launcher under your normal Windows account to access that account's Codex sign-in.

See the [development backlog](../FUTURE.md).


## Original telemetry and numbered corners (v0.3)
Imports retain a content-addressed, checksum-verified original IBT in `data/recordings`.
Existing runs have an **Attach original recording** action; attachment checks session,
setup, channel metadata, lap boundaries and saved display traces, preserving briefs and history.
The current Corvette Indy recording has been attached and verified.

Indy track ID 448 / Road Course uses the versioned T1–T14 profile in `profiles`.
Numbering was matched against the Speedway P150 numbered map, recorded GPS path,
travel direction and steering traces. Reference distances are approximate editable anchors,
not measured apexes. Entry/exit windows can overlap for linked turns. Click a turn to focus
charts; choose **Edit map** to drag handles or edit percentages. Saving keeps a new profile
revision. Geometry mismatches reject numbering; other configurations remain unnumbered
until a sourced profile is confirmed. Map and chart geometry is reduced for display only.

Watkins Glen track ID 434 / Boot uses `profiles/434-boot.json`, with T1–T11
calibrated against original GPS and steering samples and the IMSA event track map.
Lap comparison also includes the **Bus Stop** (Inner Loop) as a separate named
section between T4 and T5. Corners and automatically filled straights cover the
full lap once, and their time differences reconcile to the lap difference. These
boundaries are shared across recordings of the same configuration, including
different cars. Classic Boot, Cup, and legacy configurations retain their separate
identities. **Reset to built-in track map** restores the current circuit's profile;
**Lap comparison → Reset map** restores its comparison sections.

Project briefs now match both the car and track configuration. The existing Indy
brief stays with Indy; matching Corvette Boot imports receive the Watkins Glen
brief. Other cars keep their own editable engineering notes.

Real-recording checks: `python tests/validate_watkins.py` validates imported Boot
recordings, corner direction/GPS anchors, source checksum and segment totals.
`python tests/browser_watkins.py` uses isolated copies of the prepared comparison
pair to check the numbered map, Bus Stop selection, GPS close-up, save/reset and
desktop/mobile layouts.

AI questions calculate corner measurements using original samples across eligible laps.
The AI can request native-unit original samples with original indices and timestamps;
returned values are not rounded, interpolated or downsampled. Named corner requests
include up to two seconds of approach/exit context. Pages are consecutive, at most 256
rows / 16 KiB; investigations allow 24 calls / 128 KiB of returned evidence. Exhausted
budgets are disclosed. The whole file stays local, while requested numerical sections
and source excerpts are sent to the explicitly selected OpenAI provider.

Use Whole run, Selected corner(s), or Selected chart region. Corners explicitly named in
a question take priority; conflicting selections are rejected with an explanation.
Progress and cancellation run through background jobs; refreshing resumes status polling.
Cancelled/failed jobs retain partial coverage, and server restart marks unfinished jobs interrupted.
API cancellation stops subsequent requests; an already-dispatched network request may finish.
The installed Codex dynamic-tool round trip was live-tested with ChatGPT Plus.

Every new answer has **Inspection coverage** listing measurements and original sample
pages supplied to the AI, with links to supporting lap/ranges. Calculating measurements
across a run does not mean the model read every original sample. Old answers remain
unchanged and are labeled as predating detailed evidence tracking. Budget-limited or
summary-only answers cannot establish detailed corner behavior.

Additional verification: `python -m unittest discover -s tests -p "test_*.py"` and
`python tests/browser_evidence.py`. The browser test uses temporary recordings and a fake
model; the live Corvette/Plus acceptance test is performed separately.

## Workstation navigation

**Telemetry** fits speed, throttle, brake, an additional channel, and time delta into the desktop window. Select laps in the top toolbar or the **Sessions** browser. Use **Channels** to choose the additional trace. The **Browser** and **Map** buttons toggle panes; drag their dividers or focus a divider and use arrow keys to resize. Pane widths and the active worksheet are remembered locally. On mobile, the browser and map open as toggleable panels.

**Stint** contains lap progression and exclusions. **Engineer** contains the brief, findings, and AI conversation in independently scrolling columns. Supporting evidence links return to **Telemetry** with the cited lap and distance range. **Run info** opens metadata, the development objective, summary measurements, and display-resolution guidance.

To remove an imported session, choose **×** beside its recording in **Telemetry → Sessions** and confirm the filename and import time. Stint removes it from the list and keeps its saved data in `data/removed-runs/<run id>` for recovery. Original telemetry files and recordings shared by another import are preserved. Finish or cancel any investigation using the session before removing it.

Repeated SDK timestamps are preserved as original samples and do not split laps. A backwards clock, session change, lap-counter reset, or interrupted circuit still marks a discontinuity. Complete laps containing pit-lane activity remain visible and are excluded from pace analysis; this does not imply an incident.

Optional browser checks: `python tests/browser_smoke.py`, `python tests/browser_evidence.py`, and `python tests/browser_workstation.py` (the workstation screenshot check uses existing local recordings read-only).

Plot headers show **▼ minimum / ▲ maximum / ◆ arithmetic average** for each displayed lap or channel. Telemetry statistics follow the visible distance range and use finite original samples in displayed units, without synthetic finish-line samples. Cursor values stay separate. Hover a statistics row for sample counts and provenance. Time delta is a derived comparison using original selected timestamps and linear reference-distance interpolation; it excludes extrapolation and reference gaps. Conditions use original session samples; categorical wetness has no average. Stint statistics use eligible lap durations. Missing original channel evidence shows unavailable. Tahoma and true-black surfaces are used throughout.
