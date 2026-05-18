[SIZE=6][B]App[/B][/SIZE]

A transparent, always-on-top desktop overlay that displays live engine, driver-input, and per-wheel telemetry on top of [B]every Assetto Corsa title[/B] — the original [B]Assetto Corsa[/B], [B]Assetto Corsa Competizione[/B], [B]Assetto Corsa Evo[/B], and [B]Assetto Corsa Rally[/B]. The goal is not to replace the in-game apps, but to give you a single calibrated view of the data that actually matters when you are chasing balance and setup issues.

[MEDIA=youtube]r5xZroHg2QY[/MEDIA]

The overlay reads the game's shared-memory blocks directly ([COLOR=rgb(44, 130, 201)]Local\acevo_pmf_*[/COLOR] on AC Evo, [COLOR=rgb(44, 130, 201)]Local\acpmf_*[/COLOR] on AC1 / ACC / AC Rally), auto-detects which game is running, and attaches as soon as the session publishes telemetry. It ships as a single Windows executable — no Python plugin, no Content Manager install, nothing inside the game folder.

[IMG alt="Overlay running on top of AC Evo"]https://raw.githubusercontent.com/albertowd/live-telemetry-evo/main/resources/previews/ac-evo.webp[/IMG]

[SIZE=5][B]Telemetry Info[/B][/SIZE]

[LIST]
[*]Engine RPM with shift-light colour band.
[*]Engine power (HP) and torque, live or interpolated from the car's power curve.
[*]Boost pressure (bar); slot hidden on naturally-aspirated cars.
[*]Gear and speed (km/h).
[*]Hybrid battery bar with SoC fill; turns blue while deploying; hidden on ICE cars.
[*]Driver-aid chips: PIT, TC, ABS, ESC, LC, DRS, ERS, OT, HEAT, KMAX, CMAX, WW, INV, LAST.
[*]Analog readouts: water/oil temps, oil/fuel pressures, exhaust, battery V & temp, fuel, BBIAS.
[*]Pedal inputs (throttle, brake, clutch, handbrake).
[*]Steering angle (signed degrees) and FFB strength with a clipping warning.
[*]G-meter with lateral / longitudinal rings.
[*]Damage zones (front / rear / left / right / centre) and tyres-out counter.
[*]Performance-mode label (e.g. WET, QUAL).
[*]Tire core, inner, middle, and outer temperatures (ºC).
[*]Tire pressure (psi) and per-compound normalised pressure.
[*]Tire load (N), tire wear, and tire dirt level.
[*]Wheel camber (rad), rendered as live tire rotation.
[*]Contact-patch bars (camber × pressure × load heuristic).
[*]Suspension travel and per-wheel ride height (mm).
[*]Brake disc temperature (ºC) and brake pad / disc wear bars.
[*]Per-wheel lock and ABS blink indicators.
[/LIST]

[SIZE=5][B]Game Support[/B][/SIZE]

The overlay auto-detects which game is running and applies the correct shared-memory layout. AC1, ACC, and AC Rally publish under the same tag names ([COLOR=rgb(44, 130, 201)]Local\acpmf_*[/COLOR]) but use different struct layouts, so a [COLOR=rgb(44, 130, 201)]--source[/COLOR] flag is also available to force a specific reader if you need to.

Each game exposes a different subset of the per-widget signals. The widgets that have no data simply hide themselves, so the HUD always shows live values and never fakes anything.

[LIST]
[*][B]AC Evo[/B] — most complete coverage: live BHP / torque, per-aid in-action flags, brake pad / disc wear, normalised tire temps and pressure, per-axle ride height, contact-patch bars.
[*][B]AC1[/B] — live data plus the car's [B]own torque curve, tyre thermal curves and ideal pressures[/B] read from [COLOR=rgb(44, 130, 201)]data.acd[/COLOR] on connect, so the rev-bar colour band and HP readout reflect the real engine (F1 2004 peak power lands at ~17 000 RPM, not a hardcoded 5 500). Slip-threshold heuristics drive lock / ABS blink. Tire-wear bar self-calibrates to the ~0.06 useful range below the fresh-tyre peak (AC1's [COLOR=rgb(44, 130, 201)]tyreWear[/COLOR] is a grip/health signal, not a linear "% remaining").
[*][B]ACC[/B] — engine, inputs, brake temps and wear, suspension travel. Tire IMO faces and wheel load are not published by the game (the widget falls back to core temperature for the silhouette and hides the load circle).
[*][B]AC Rally[/B] — engine, inputs, tire core temperatures, brake disc temperatures, wheel load circle. Per-face tire temps and ride height are not published. Temperatures arrive in Kelvin and are converted internally.
[/LIST]

[IMG alt="Overlay running on top of AC Rally"]https://raw.githubusercontent.com/albertowd/live-telemetry-evo/main/resources/previews/ac-rally.webp[/IMG]

[SIZE=5][B]Engine Bar[/B][/SIZE]

Top-centred at the bottom of the screen.

The RPM bar uses the power curve to colour itself, so it works as a true shift indicator instead of a fixed percentage threshold:

[LIST]
[*]white: safely below peak power.
[*][COLOR=rgb(44, 130, 201)]blue[/COLOR]: close to peak power on the way up, or a shift-down hint from the game.
[*][COLOR=rgb(97, 189, 109)]green[/COLOR]: at peak power, the optimal shift point.
[*][COLOR=rgb(226, 80, 65)]red[/COLOR]: past peak (you are losing power) or a shift-up hint from the game.
[/LIST]

The HP label shows the current live horsepower when the game publishes it, otherwise the value is interpolated from the bundled power curve and scaled by the current boost ([COLOR=rgb(44, 130, 201)]hp = power * ( 1 + boost )[/COLOR]). On AC1 the curve comes from the car's own [COLOR=rgb(44, 130, 201)]data.acd[/COLOR] so the value matches the car's real engine output. Gear, speed (km/h) and RPM sit on the same row.

The boost bar above the RPM bar:

[LIST]
[*]white: boost below 90 % of the rolling maximum.
[*][COLOR=rgb(97, 189, 109)]green[/COLOR]: boost above 90 %.
[/LIST]

The bar's right edge tracks the highest boost actually achieved this session (rolling max from observed [COLOR=rgb(44, 130, 201)]turbo_boost[/COLOR] — the [COLOR=rgb(44, 130, 201)]static.maxTurboBoost[/COLOR] value in many mod cars is unreliable). On naturally-aspirated cars the slot stays [B]fully transparent[/B] so the widget shows a clean top edge.

Above the boost bar, a [B]hybrid battery bar[/B] appears on cars equipped with a KERS / ERS pack. Fill colour tracks SoC (green > 50 %, yellow > 20 %, else red); the bar turns [COLOR=rgb(44, 130, 201)]blue[/COLOR] whenever energy is actively leaving the pack. Like the boost bar, the slot is transparent on pure-ICE cars.

A strip of driver-aid chips appears only while each condition is true so the bar stays compact: pit limiter, TC (bright when cutting, dim when armed), ABS (same scheme), ESC, launch control, DRS (bright when deployed, dim when only available), ERS charging, OT (overtake / max-deploy mode), HEAT (ERS heat charging), KMAX / CMAX (per-lap deploy / charge cap reached), wrong-way warning, invalid-lap and last-lap markers.

The bottom row shows water / oil temperature, oil and fuel pressure, exhaust temperature, battery voltage, battery temperature, fuel litres, and brake bias percentage. Cells that the running game does not publish are hidden automatically.

[SIZE=5][B]Inputs Widget[/B][/SIZE]

Top-centred at the top of the screen, between the front wheels.

[LIST]
[*][B]Pedal bars[/B] — throttle ([COLOR=rgb(97, 189, 109)]green[/COLOR]), brake ([COLOR=rgb(226, 80, 65)]red[/COLOR]), clutch ([COLOR=rgb(247, 218, 100)]yellow[/COLOR]), handbrake ([COLOR=rgb(226, 226, 226)]white[/COLOR]) as horizontal fill bars.
[*][B]Steering bar[/B] — signed bar from the centre with the angle in degrees.
[*][B]FFB bar[/B] — force-feedback strength; turns [COLOR=rgb(226, 80, 65)]red[/COLOR] at 95 % or above to flag clipping.
[*][B]G-meter[/B] — circular meter with 0.5 g and 1.0 g reference rings, a dot at the live lateral / longitudinal acceleration, and a combined-g readout below.
[LIST]
[*][COLOR=rgb(97, 189, 109)]green[/COLOR]: under 1 g combined.
[*][COLOR=rgb(247, 218, 100)]yellow[/COLOR]: 1 – 1.5 g combined.
[*][COLOR=rgb(226, 80, 65)]red[/COLOR]: above 1.5 g combined.
[/LIST]
[*][B]Damage chips[/B] — five zones (front, rear, left, right, centre). Dim outline below 5 %, [COLOR=rgb(247, 218, 100)]yellow[/COLOR] to 25 %, [COLOR=rgb(226, 80, 65)]red[/COLOR] above. The whole row hides while the car is undamaged.
[*][B]Tyres-out chip[/B] — number of wheels off-track; hides when zero.
[*][B]Mode label[/B] — current performance preset (WET / QUAL / etc.); hides when empty.
[/LIST]

[IMG alt="Overlay running on top of ACC"]https://raw.githubusercontent.com/albertowd/live-telemetry-evo/main/resources/previews/acc.webp[/IMG]

[SIZE=5][B]Wheel Widget[/B][/SIZE]

One widget per corner. The inner face of the tire always points toward the screen centre.

[LIST]
[*][B]Tire silhouette[/B] — tinted by composite tire temperature (75 % core + 25 % average of inner / middle / outer). The colour tracks the per-compound normalised temperature so the band stays correct across compounds.
[LIST]
[*][COLOR=rgb(44, 130, 201)]blue[/COLOR]: below 98 % of the ideal band.
[*][COLOR=rgb(44, 130, 201)]blue[/COLOR] - [COLOR=rgb(97, 189, 109)]green[/COLOR]: between 98 % and 100 %.
[*][COLOR=rgb(97, 189, 109)]green[/COLOR] - [COLOR=rgb(226, 80, 65)]red[/COLOR]: between 100 % and 102 %.
[*][COLOR=rgb(226, 80, 65)]red[/COLOR]: above 102 %.
[/LIST]
[*][B]Inner / middle / outer temp grid[/B] — three columns over the silhouette with the per-face temperature in ºC. Text auto-flips black or white per luminance so it stays legible across the colour sweep. Lets you spot a single-edge overheat (camber too aggressive) or one-corner cooking.
[*][B]Core temperature band[/B] — central band over the silhouette with the core value in ºC, coloured by the same compound-normalised scale.
[*][B]Camber rotation[/B] — the whole tire silhouette rotates around its centre by the live camber value, amplified ×2 so a typical −2.5° setup reads as a ~5° tilt.
[*][B]Contact patch bars[/B] — three bars hanging from the tire's bottom edge (inner / middle / outer faces) whose heights encode a camber × pressure × load heuristic. The bars [B]are[/B] the ground reference; you can see at a glance which face is loaded.
[*][B]Tire load circle[/B] — white ring centred on the silhouette. Diameter scales with vertical wheel load; a typical static corner load fills the inner half, and the ring saturates around ~5 200 N.
[*][B]Tire pressure[/B] (psi) — icon tinted by per-compound normalised pressure (1.0 = ideal cold pressure). The label is the raw psi value.
[LIST]
[*][COLOR=rgb(44, 130, 201)]blue[/COLOR]: below 95 %.
[*][COLOR=rgb(44, 130, 201)]blue[/COLOR] - [COLOR=rgb(97, 189, 109)]green[/COLOR]: 95 % to 100 %.
[*][COLOR=rgb(97, 189, 109)]green[/COLOR] - [COLOR=rgb(226, 80, 65)]red[/COLOR]: 100 % to 105 %.
[*][COLOR=rgb(226, 80, 65)]red[/COLOR]: above 105 %.
[/LIST]
[*][B]Tire wear[/B] — AC1 only. Self-calibrated against the meaningful ~0.06 grip/health window below the fresh-tyre peak (AC1's raw signal isn't a linear "% remaining"), so the bar actually moves across a stint instead of pegging near full.
[LIST]
[*][COLOR=rgb(97, 189, 109)]green[/COLOR]: above 75 %.
[*][COLOR=rgb(247, 218, 100)]yellow[/COLOR]: 40 % – 75 %.
[*][COLOR=rgb(226, 80, 65)]red[/COLOR]: below 40 %.
[/LIST]
[*][B]Tire dirt overlay[/B] — brown rectangle that grows from the bottom of the silhouette as the tire picks up off-track grass and gravel.
[*][B]Suspension bar[/B] — tinted graphic on the outer side of the wheel. The bar fills at full extension and shrinks as the suspension compresses (kept from the original LiveTelemetry plugin for parity). When the game supplies a per-car maximum travel (Kunos AC1 cars, ACC, AC Rally), the bar uses it as the engineered limit and [COLOR=rgb(226, 80, 65)]red[/COLOR] correctly reads as "bottoming out" on stiff F1-style suspension that brushes the limit. Otherwise the bar self-calibrates against the maximum observed (AC EVO always, mod cars without the static value).
[LIST]
[*][COLOR=rgb(226, 226, 226)]white[/COLOR]: mid-travel, engineered limit known.
[*][COLOR=rgb(44, 130, 201)]blue[/COLOR]: mid-travel while using a self-calibrated maximum.
[*][COLOR=rgb(247, 218, 100)]yellow[/COLOR]: outside ±10 % of the calibrated max.
[*][COLOR=rgb(226, 80, 65)]red[/COLOR]: outside ±5 % (on the bump-stops).
[/LIST]
[*][B]Brake disc[/B] — top-inner icon. On AC EVO / ACC / AC Rally it's tinted by disc temperature (peak grip around 400 ºC); on AC1 the game never writes [COLOR=rgb(44, 130, 201)]brakeTemp[/COLOR], so the icon sits on a neutral white base with no °C label.
[LIST]
[*][COLOR=rgb(44, 130, 201)]blue[/COLOR]: cold (below ~150 ºC, reduced bite).
[*][COLOR=rgb(226, 226, 226)]white[/COLOR] / [COLOR=rgb(97, 189, 109)]green[/COLOR]: in the operating window.
[*][COLOR=rgb(226, 80, 65)]red[/COLOR]: overheating (above ~600 ºC, fade risk).
[/LIST]
[*][B]Disk / Pads wear bars[/B] — two horizontal bars in the brake column. Full = fresh, shrinking with wear ([COLOR=rgb(97, 189, 109)]green[/COLOR] > 50 %, [COLOR=rgb(247, 218, 100)]yellow[/COLOR] > 20 %, [COLOR=rgb(226, 80, 65)]red[/COLOR] below).
[*][B]Wheel lock / ABS[/B] — the brake icon blinks to flag pedal events on this corner. ABS-active merges all three documented signals (physics int, intensity float, graphics bool) so the cue fires on every car/build combo. Blink colour is picked to contrast with the icon's base tint:
[LIST]
[*][COLOR=rgb(226, 226, 226)]white[/COLOR] blink: ABS modulating on this wheel ([B]games with brake temp[/B] — contrasts against any temp colour).
[*][COLOR=rgb(44, 130, 201)]blue[/COLOR] blink: ABS modulating on this wheel ([B]AC1[/B] — contrasts against the white base).
[*][COLOR=rgb(247, 218, 100)]yellow[/COLOR] blink: wheel locked up (cars with no ABS, or ABS overwhelmed).
[/LIST]
[*][B]Pressure icon, ride-height icon, compound label[/B] — additional readouts in the side column. The ride-height icon flashes [COLOR=rgb(226, 80, 65)]red[/COLOR] for 0.5 s when the car bottoms out (below 20 mm).
[/LIST]

[IMG alt="Overlay running on top of AC1"]https://raw.githubusercontent.com/albertowd/live-telemetry-evo/main/resources/previews/ac1.webp[/IMG]

[SIZE=5][B]Controls[/B][/SIZE]

Hotkeys are registered globally with Win32 [COLOR=rgb(44, 130, 201)]RegisterHotKey[/COLOR], so they fire even while the game has focus:

[LIST]
[*][B]Ctrl+Alt+L[/B] — toggle click-through (lock / unlock the overlay for repositioning).
[*][B]Ctrl+Alt+R[/B] — reset every widget to its default position and visibility.
[*][B]Ctrl+Alt+S[/B] — cycle widget size (XS → S → M → L → XL → XS …). The chosen size persists.
[*][B]Ctrl+Alt+Q[/B] — quit the overlay.
[/LIST]

A system-tray icon mirrors the same options (reset positions, click-through toggle, size submenu, quit) with the hotkey shown next to each label, plus two new sections:

[LIST]
[*][B]Polling Hz[/B] submenu — 30 / 60 / 100 / 120 / 144 / 250 Hz. Drives the shared-memory poll rate (and CSV row rate when logging is active). UI repaint runs independently at the monitor refresh rate, so faster polling never makes the widgets paint more often than your display can show. The choice is persisted.
[*][B]Start logging[/B] / [B]Stop logging[/B] — toggles CSV capture of every telemetry frame (raw shared-memory values plus calculated ones) to a timestamped file next to the executable. A dedicated writer thread does the disk I/O so polling cadence stays exact. [B]Open logs folder[/B] opens the directory in Explorer.
[/LIST]

When the overlay is unlocked (click-through OFF), drag any widget to move it, or click its [B]×[/B] to hide it. Both states persist across sessions. If you hide everything, [B]Ctrl+Alt+R[/B] brings the layout back.

[B]Click-through is ON by default[/B] so the overlay never steals mouse input from the game.

[SIZE=5][B]Resolutions[/B][/SIZE]

The overlay picks a screen multiplier from the vertical resolution of your main monitor and combines it with your chosen size factor ([B]Ctrl+Alt+S[/B]):

[LIST]
[*]XS — 0.50 ×
[*]S — 0.75 ×
[*]M — 1.00 × (default)
[*]L — 1.25 ×
[*]XL — 1.50 ×
[/LIST]

The baseline (1.0) is calibrated for 1440p; the multiplier automatically scales the HUD up on 4K and down on 1080p so the layout stays usable across screens.

[SIZE=5][B]Persistence[/B][/SIZE]

All overlay state lives in a single JSON file [B]next to the executable[/B] ([COLOR=rgb(44, 130, 201)]positions.json[/COLOR]), and CSV logs go to a sibling [COLOR=rgb(44, 130, 201)]logs\[/COLOR] folder — no [COLOR=rgb(44, 130, 201)]%APPDATA%[/COLOR] hunting required. The JSON stores per-widget positions and visibility, the chosen size index, and the persisted polling Hz. A saved position is honoured on next launch only if the widget would land fully on screen at the current resolution; otherwise it falls back to the layout default.

[SIZE=6][B]App Install[/B][/SIZE]

This release ships as a [B]single Windows executable[/B]. There is no Content Manager step, no Python plugin to drop into [COLOR=rgb(44, 130, 201)]apps/python/[/COLOR], and nothing inside the AC install folder.

[LIST=1]
[*]Download [COLOR=rgb(44, 130, 201)]LiveTelemetryEvo-<version>.exe[/COLOR] from the [URL='https://github.com/albertowd/live-telemetry-evo/releases']GitHub releases page[/URL] (or from the Overtake.gg download button above).
[*]Put it anywhere — Desktop, Documents, a sim-racing tools folder, your choice.
[*]Start any supported Assetto Corsa title (AC1, ACC, AC Evo, or AC Rally) and load a session.
[*]Double-click the executable. A [I]Detecting AC Environment…[/I] screen stays up until the overlay finds the game; once it attaches, a 5-second countdown plays showing the detected game name above the digit, then the widgets reveal.
[*]Use [B]Ctrl+Alt+L[/B] to unlock for repositioning, then drag widgets where you want them.
[/LIST]

To uninstall, just delete the [COLOR=rgb(44, 130, 201)].exe[/COLOR] and the [COLOR=rgb(44, 130, 201)]positions.json[/COLOR] / [COLOR=rgb(44, 130, 201)]logs\[/COLOR] sitting next to it.

The executable is [B]not signed[/B] with a commercial certificate, so SmartScreen may show a "Windows protected your PC" prompt the first time you run it — [I]More info → Run anyway[/I]. The source and the GitHub Actions build that produced the release are public so you can verify both.

[SIZE=6][B]Noted Bugs[/B][/SIZE]

All known issues and feature requests live on the GitHub repository: [URL='https://github.com/albertowd/live-telemetry-evo/issues']Live Telemetry Evo Issues[/URL].

If the overlay reads a suspicious value on a particular car, please open an issue with the game title, the car, and (if you can) a short clip — many struct fields differ between games and a few are still being mapped.

[SIZE=6][B]Big Thanks[/B][/SIZE]

[B]Kunos Simulazioni[/B] for publishing the [URL='https://steamcommunity.com/sharedfiles/filedetails/?id=3707421508']AC Evo Shared Memory documentation[/URL] on Steam. The struct layouts for AC Evo are transcribed straight from that guide.

[B]The original LiveTelemetry AC1 plugin[/B] — the visual language of this overlay (wheel widget layout, suspension bar direction, colour conventions) is ported from that project, kept intentionally familiar for anyone coming from AC1.

[B]The ACC community[/B] for the bundled [COLOR=rgb(44, 130, 201)]ACCSharedMemoryDocumentationV1.8.12.pdf[/COLOR] reference that pinned down the ACC layout (and surfaced the [COLOR=rgb(44, 130, 201)]currentMaxRpm[/COLOR] int/float trap).

[B]Everyone who tested early builds on AC Rally and ACC[/B] — the per-game struct fixes (Kelvin temperatures on AC Rally, missing IMO faces on ACC, etc.) all came from real-session feedback.
