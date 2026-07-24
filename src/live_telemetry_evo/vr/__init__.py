"""VR output for the overlay.

Submits the composited HUD pixels to the SteamVR / OpenVR compositor as a
single overlay quad so the telemetry shows up *inside* the headset — a
plain Win32 always-on-top window is invisible in VR.

One OpenVR-overlay backend covers every PCVR headset that runs through
SteamVR: a Pimax 4k (PiTool -> SteamVR) and a Meta Quest 3 (Link / Air
Link / Steam Link / Virtual Desktop -> SteamVR) look identical from here.

Everything is import-guarded: on a machine without ``openvr`` or a running
SteamVR runtime, :func:`overlay.vr.detect.vr_active` returns ``False`` and
:class:`overlay.vr.overlay_output.VROverlayOutput` reports unavailable, so
the desktop overlay keeps working untouched.
"""
