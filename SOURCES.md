# Corvette / Indy 8-hour engineering sources

These sources informed the Corvette endurance-development workflow. The optional
importer stores the reference material and manifest under `data/sources/` and
indexes extracts under `data/library/` on your machine. See the
[driver and engineer guide](docs/GUIDE.md#indy-8-hour-preparation) for import commands.

| Source | Imported content | Provenance |
|---|---|---|
| [Chevrolet Corvette Z06 GT3.R manual V2](https://s100.iracing.com/wp-content/uploads/2024/06/Chevrolet-Corvette-Z06-GT3_manual_V2.pdf) | Text from all 24 PDF pages | June 2024 supplied version; page-number links and original PDF hash |
| [The Ultimate Car Setup Guide for Beginners - RECORDING LIVE!](https://www.youtube.com/watch?v=tQrCTRvPmMs) | English automatically generated captions | Timestamped transcript; about 104,000 characters |
| [The Comprehensive Guide to Adjusting Ride Heights on iRacing](https://www.youtube.com/watch?v=ot0Y8Eruc90) | English automatically generated captions | Timestamped transcript; about 27,000 characters |
| [The only iRacing setup guide you will need [beginner to advanced]](https://www.youtube.com/watch?v=z7vWsRou5cU) | English automatically generated captions | Timestamped transcript; about 37,000 characters |

The library stores source text rather than inventing summaries. Retrieval returns relevant excerpts with page/time locators. The official car manual is preferred for car-specific behavior; general video guidance is contextual. Tagged Corvette material is excluded when the measured run is from another car.

Auto captions can misrecognize engineering vocabulary. PDF diagrams and video demonstrations are not visually interpreted by this import. The 2024 manual is not proof of current garage limits or the event's current BoP. Confirm adjustments against the current garage and simulator build.


Indy corner numbering: Indianapolis Motor Speedway, P150 Road Course Map 2020:
https://www.indianapolismotorspeedway.com/-/media/IMS/pdf/track-maps/2020/P150%20Road%20Course%20Map%202020
The numbered topology was compared with the original track-ID-448 recording's GPS,
steering and travel direction; the 2022 configuration identity comes from the IBT and
https://www.iracing.com/updated-indianapolis-motor-speedway-road-course-available-now/.
The PDF hash is retained in the built-in profile. Percentage anchors/windows are local
calibrations. The profile links to the original reference PDF.

Watkins Glen Boot numbering and topology: [IMSA 2025 Sahlen's Six Hours of The Glen
event maps, page 2](https://imsa.results.alkamelcloud.com/Results_NoticeBoard/25_2025/14_Watkins%20Glen%20International/01_Event%20Maps.pdf#page=2),
cross-checked with [iRacing's circuit description](https://www.iracing.com/tracks/watkins-glen-international/).
The original reference PDF's SHA-256 is in `profiles/434-boot.json`. Recorded track ID 434 / Boot, original
GPS, steering handedness and travel order provide the lap-distance calibration.
T1–T11 retain the circuit's conventional numbering; the Inner Loop (Bus Stop)
receives its own named lap-comparison section. Boundaries are editable estimates,
and are not surveyed apex or pavement coordinates. The profile has been checked
against two Corvette recordings and a Mustang GT4 recording.
