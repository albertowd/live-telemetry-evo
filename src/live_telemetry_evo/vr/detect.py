"""Detect whether a VR session is live so the overlay can self-enable.

Two cheap signals, combined:

1. **SteamVR runtime running** — ``vrserver.exe`` (the OpenVR runtime
   server) or ``vrmonitor.exe`` (the status window) is in the process
   list. Probed via the same Win32 toolhelp snapshot the game detector
   uses, so it costs nothing extra and needs no ``openvr`` import.
2. **An HMD is actually present** — ``openvr.isHmdPresent()``, a light
   probe that does *not* spin up a full ``openvr.init`` session. Guarded
   so a missing ``openvr`` package (or a transient runtime error) reads
   as "no headset" rather than crashing.

``vr_active()`` ANDs the two: SteamVR is up *and* a headset is connected.
That is the signal ``--vr auto`` polls on to flip the overlay into VR.
"""
from __future__ import annotations

from ..sources.detect import is_process_running


# SteamVR runtime processes. ``vrserver.exe`` is the headless runtime
# server (always up while SteamVR runs); ``vrmonitor.exe`` is its status
# window. Either one being present means a SteamVR session is live.
_STEAMVR_PROCESSES = ("vrserver.exe", "vrmonitor.exe")


def steamvr_running() -> bool:
    """True iff a SteamVR runtime process is currently running."""
    return is_process_running(_STEAMVR_PROCESSES)


def hmd_present() -> bool:
    """True iff the OpenVR runtime reports a connected HMD.

    ``openvr.isHmdPresent()`` is a standalone query that doesn't require
    (and doesn't start) a full ``openvr.init`` session, so it's safe to
    poll cheaply. Any failure — ``openvr`` not installed, runtime not
    ready — is swallowed and read as "no headset".
    """
    try:
        import openvr  # pylint: disable=import-outside-toplevel
    except ImportError:
        return False
    try:
        return bool(openvr.isHmdPresent())
    except Exception:  # pylint: disable=broad-except
        # openvr raises bare Exceptions (e.g. OpenVRError) when the
        # runtime is mid-startup or unreachable; treat as "not yet".
        return False


def vr_active() -> bool:
    """True when a VR session the overlay should target is live: SteamVR
    is running *and* a headset is connected.

    Used by ``--vr auto`` to decide when to enable the VR overlay. The
    process check gates the (slightly heavier) HMD probe so we don't poke
    openvr at all when SteamVR isn't even running.
    """
    return steamvr_running() and hmd_present()


__all__ = ["steamvr_running", "hmd_present", "vr_active"]
