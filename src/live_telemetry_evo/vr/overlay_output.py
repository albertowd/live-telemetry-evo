"""Submit the HUD to SteamVR as one small overlay quad **per widget**.

Each visible widget is its own OpenVR overlay: a quad sized and placed on a
shared virtual plane so the assembly reproduces the desktop layout exactly,
and its grabbed image is uploaded straight to that quad's texture — no
compositing, no full-screen canvas. Two wins over the old single
full-screen quad:

* **Upload is widget-sized, not screen-sized.** The old design composited
  every widget onto one screen-resolution canvas and uploaded the whole
  thing each tick — ~14.7 MB at 1440p, most of it transparent. Six small
  quads upload only the pixels actually drawn (~3.3 MB total at 1440p,
  ~4.5x less), which is the dominant cost in holding headset rate.
* **A hidden widget costs nothing.** Closed/hidden widgets simply have
  their overlay hidden; no transparent pixels are uploaded for them.

Each widget's image is uploaded at its native resolution (the quad's
physical width is its pixel fraction of the screen times ``WIDTH_M``), so
text is as crisp on the quad as on the desktop overlay.

Two submission paths, both verified empirically against SteamVR with
``scripts/vr_probe.py``:

* **GL texture streaming** (primary) — each overlay owns a small ring of
  persistent OpenGL textures on a shared offscreen Qt context; the grabbed
  image is uploaded into the next texture in the ring and handed to the
  compositor via ``setOverlayTexture``, which swaps it atomically.
* **Raw upload** (fallback when GL init fails) — ``setOverlayRaw``
  rebuilds the overlay's backing texture on every call and the quad can
  render empty between uploads, which shows as *blinking* at streaming
  rates. Usable, but strictly worse.

Hard-won rule for either path: **an overlay app must drain its VR event
queues.** The runtime delivers ~15-25 events/s; if they are never polled
the queue fills after roughly 10 seconds and the IPC channel wedges —
every subsequent submit fails with ``OverlayError_RequestFailed``,
forever. The system queue is per-app; the overlay queue is **per handle**,
so every live overlay is pumped each tick (``_pump_events``). A throttled
destroy-and-recreate of all overlays (``_recreate``) remains as last-resort
recovery should a submit streak still fail.

Two placements (see :meth:`set_placement`):

* ``"head"`` — quads parented to the HMD, fixed in front of the eyes.
  Never jitters, always visible; the safe default across every headset.
* ``"dash"`` — quads frozen in the seated play space where the assembly
  floats at the moment the mode is activated: the HMD pose is sampled once
  and shared by every widget, so the whole panel locks in place in front
  of wherever the user is currently looking. More immersive, but OpenVR
  world-locking can jitter on some headsets (notably Quest over a streamed
  link), which is why it isn't the default. Re-selecting the mode
  re-anchors at the current gaze.

Everything is guarded: if ``openvr`` can't be imported or SteamVR isn't
running, :meth:`start` logs and returns ``False`` and the caller stays on
the desktop overlay.
"""
from __future__ import annotations

import ctypes
import math
import time

from PySide6.QtGui import QImage, QOffscreenSurface, QOpenGLContext
from PySide6.QtOpenGL import QOpenGLTexture

try:
    import openvr  # type: ignore
    _IMPORT_ERROR: str | None = None
except Exception as exc:  # pylint: disable=broad-except
    # ImportError when the package is absent; other exceptions if the
    # bundled native lib fails to load. Either way VR is unavailable.
    openvr = None  # type: ignore
    _IMPORT_ERROR = str(exc)


# --- Placement constants (metres / radians) -------------------------------
# Tunable once the user can try it on the gaming PC. ``WIDTH_M`` is the
# physical width of the WHOLE virtual screen plane; each widget quad takes
# the fraction of it that the widget occupies on screen, so the assembly
# keeps the screen's aspect and relative layout.
WIDTH_M = 2.4          # physical width of the whole screen plane in metres
HEAD_DISTANCE_M = 1.4  # head-locked: how far in front of the eyes
HEAD_DOWN_M = 0.30     # head-locked: drop it slightly below eye line
# Pull widgets horizontally toward the centre of view. The exact
# flat-screen mapping puts the corner wheels out near the edge of the FOV,
# which feels too wide in a headset; <1.0 compresses only the sideways
# offset (vertical positions and widget sizes are untouched, and the
# centred engine/inputs don't move). 1.0 == exact desktop layout. This is
# only the DEFAULT — the user tunes it live via the tray "VR spread"
# submenu (see :meth:`VROverlayOutput.set_spread`).
HORIZONTAL_SPREAD = 0.8
# Widgets are placed on a vertical CYLINDER centred on the head rather than
# a flat plane, so the panel wraps gently around the viewer and the corner
# widgets turn to face inward instead of skewing away at the edge of
# vision. The radius matches the head-locked viewing distance, so the
# centre widget sits exactly where the flat plane used to. This is only the
# DEFAULT distance — the user tunes how far the panel floats live via the
# tray "VR distance" submenu (see :meth:`VROverlayOutput.set_distance`).
CYLINDER_RADIUS_M = HEAD_DISTANCE_M
# Legacy seated-dashboard spot — only used as the "dash" fallback when the
# HMD pose can't be sampled at activation (tracking not valid yet).
DASH_FORWARD_M = 0.55  # distance forward of the seated origin
DASH_DOWN_M = 0.45     # how far below the seated eye height
DASH_PITCH_RAD = math.radians(35.0)  # tilt top toward driver

# Each overlay keeps a small ring of textures so the compositor never reads
# the one texture currently being overwritten (single-buffering tears /
# stalls at streaming rates).
_TEXTURE_RING = 3

# OpenVR overlay keys must be process-unique; per-widget keys hang off this
# base. The name is the human label shown in SteamVR's overlay list.
_OVERLAY_KEY = "dev.albertowd.live-telemetry-evo"
_OVERLAY_NAME = "Live Telemetry Evo"


def _identity_matrix():
    """Return an OpenVR ``HmdMatrix34_t`` set to identity (no rotation,
    no translation)."""
    m = openvr.HmdMatrix34_t()
    for r in range(3):
        for c in range(4):
            m.m[r][c] = 1.0 if r == c else 0.0
    return m


def _matrix(pitch_rad: float, tx: float, ty: float, tz: float):
    """Build a ``HmdMatrix34_t`` rotating ``pitch_rad`` about the X axis
    (positive tilts the top of the panel toward the viewer) with the given
    translation. Column 3 is the translation; columns 0..2 are rotation."""
    m = _identity_matrix()
    cos, sin = math.cos(pitch_rad), math.sin(pitch_rad)
    # Rotation about X.
    m.m[1][1] = cos
    m.m[1][2] = -sin
    m.m[2][1] = sin
    m.m[2][2] = cos
    # Translation.
    m.m[0][3] = tx
    m.m[1][3] = ty
    m.m[2][3] = tz
    return m


def _mul_matrix(a, b):
    """Compose two ``HmdMatrix34_t`` affine transforms (``a`` applied after
    ``b``). Both carry an implicit ``[0 0 0 1]`` bottom row, so the result's
    translation column picks up ``a``'s translation."""
    m = openvr.HmdMatrix34_t()
    for r in range(3):
        for c in range(4):
            v = sum(a.m[r][k] * b.m[k][c] for k in range(3))
            if c == 3:
                v += a.m[r][3]
            m.m[r][c] = v
    return m


def _cylinder_matrix(theta: float, ty: float, radius: float):
    """Build a ``HmdMatrix34_t`` placing a quad on a vertical cylinder of
    the given ``radius`` at horizontal angle ``theta`` (radians, +right),
    yawed so its face turns back toward the cylinder's axis (the head).

    ``ty`` is the vertical offset; height is independent of the angle. At
    ``theta == 0`` this reduces to the old flat placement (x=0, z=-radius,
    no rotation), so the centre widget is unchanged."""
    m = _identity_matrix()
    cos, sin = math.cos(theta), math.sin(theta)
    # Yaw about Y so the quad's +Z face points back at the axis. Columns
    # 0/2 are the rotated right/normal axes; up (column 1) stays vertical.
    m.m[0][0] = cos
    m.m[0][2] = -sin
    m.m[2][0] = sin
    m.m[2][2] = cos
    # Position around the cylinder: straight ahead at theta=0 (x=0,
    # z=-radius), wrapping toward the viewer as |theta| grows.
    m.m[0][3] = radius * sin
    m.m[1][3] = ty
    m.m[2][3] = -radius * cos
    return m


class _WidgetOverlay:
    """One OpenVR overlay quad backing a single widget. Created lazily the
    first time the widget is submitted; reused for its lifetime."""

    __slots__ = ("key", "handle", "textures", "next_tex", "rect",
                 "tex_size", "shown")

    def __init__(self, key: str) -> None:
        self.key = key
        self.handle = None           # VROverlayHandle_t
        self.textures: list = []     # GL ring (empty on the raw path)
        self.next_tex = 0            # round-robin index into the ring
        self.rect: tuple | None = None       # (x, y, w, h) last applied
        self.tex_size: tuple | None = None   # (w, h) the ring is sized for
        self.shown = False           # whether the quad is currently visible


class VROverlayOutput:
    """Lifecycle wrapper around a set of OpenVR overlays (one per widget).
    Lives on the UI thread; every openvr call is guarded so a missing
    runtime degrades to a no-op."""

    def __init__(self, placement: str = "head",
                 spread: float = HORIZONTAL_SPREAD,
                 distance: float = CYLINDER_RADIUS_M) -> None:
        self._placement = placement if placement in ("head", "dash") else "head"
        # Horizontal fan-out factor and cylinder radius (metres). Both are
        # user-tunable live from the tray; changing either re-applies every
        # overlay transform on the next submit via ``_placement_dirty``.
        self._spread = float(spread)
        self._distance = max(0.1, float(distance))
        self._ov = None        # IVROverlay
        self._system = None     # IVRSystem, for the system event queue
        self._running = False
        self._overlays: dict[str, _WidgetOverlay] = {}
        # Re-apply every overlay's transform on the next submit (set on
        # start and whenever the placement mode changes).
        self._placement_dirty = True
        # HMD pose shared by all quads in "dash" mode, sampled once when the
        # placement is (re)applied so the whole panel anchors together.
        self._dash_anchor = None
        self._last_error = ""  # last submit error name, to log only changes
        self._fail_streak = 0  # consecutive submit failures
        self._next_recreate = 0.0  # monotonic deadline for the next rebuild
        # GL texture streaming state (None => raw-upload fallback).
        self._gl_ctx = None        # QOpenGLContext
        self._gl_surface = None    # QOffscreenSurface
        self._ovr_texture = None   # reusable openvr.Texture_t (handle set per upload)
        # Last submit's phase costs in ms — (pump, prep, upload). Surfaced
        # so the app's step-down log can say WHERE a slow tick spends its
        # time instead of just how long it took.
        self.phase_ms: tuple[float, float, float] = (0.0, 0.0, 0.0)

    # --- queries ----------------------------------------------------------
    @staticmethod
    def available() -> bool:
        """Whether the ``openvr`` binding loaded at import time."""
        return openvr is not None

    def is_running(self) -> bool:
        """Whether the overlays are live and accepting frames."""
        return self._running

    @property
    def placement(self) -> str:
        """Current placement mode (``"head"`` / ``"dash"``)."""
        return self._placement

    @property
    def spread(self) -> float:
        """Current horizontal fan-out factor (see ``HORIZONTAL_SPREAD``)."""
        return self._spread

    @property
    def distance(self) -> float:
        """Current distance the panel floats from the viewer, in metres
        (the cylinder radius; see ``CYLINDER_RADIUS_M``)."""
        return self._distance

    def display_hz(self) -> float:
        """The HMD's display refresh rate in Hz (e.g. 90.0 / 120.0), or
        ``0.0`` when not running or the runtime doesn't report it — the
        caller picks its own fallback."""
        if self._system is None:
            return 0.0
        try:
            hz = self._system.getFloatTrackedDeviceProperty(
                openvr.k_unTrackedDeviceIndex_Hmd,
                openvr.Prop_DisplayFrequency_Float,
            )
            return float(hz) if hz and hz > 0 else 0.0
        except Exception:  # pylint: disable=broad-except
            return 0.0

    # --- lifecycle --------------------------------------------------------
    def start(self) -> bool:
        """Init OpenVR as an overlay app. Individual widget quads are
        created lazily on first submit.

        Returns ``True`` on success. On any failure (no binding, SteamVR
        not running) it logs a single line and returns ``False`` so the
        caller can stay on the desktop overlay.
        """
        if self._running:
            return True
        if openvr is None:
            print(f"[overlay] VR unavailable: openvr import failed ({_IMPORT_ERROR})")
            return False
        try:
            openvr.init(openvr.VRApplication_Overlay)
            self._system = openvr.VRSystem()
            self._ov = openvr.VROverlay()
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[overlay] VR overlay init failed: {exc}")
            self._safe_shutdown()
            return False
        self._init_gl()
        self._placement_dirty = True
        self._running = True
        mode = "gl" if self._gl_ctx is not None else "raw"
        print(f"[overlay] VR overlay started "
              f"(placement={self._placement}, submit={mode})")
        return True

    def _init_gl(self) -> None:
        """Set up the offscreen GL context shared by every overlay's texture
        ring. The raw path (``setOverlayRaw``) recreates each overlay's
        backing texture on every call and the quad can render empty between
        uploads — visible blinking at streaming rates. Persistent GL
        textures are swapped atomically by the compositor instead. On any
        failure GL stays off and submits fall back to the raw path."""
        try:
            ctx = QOpenGLContext()
            if not ctx.create():
                raise RuntimeError("QOpenGLContext.create() failed")
            surface = QOffscreenSurface()
            surface.setFormat(ctx.format())
            surface.create()
            if not surface.isValid():
                raise RuntimeError("QOffscreenSurface invalid")
            if not ctx.makeCurrent(surface):
                raise RuntimeError("makeCurrent failed")
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[overlay] VR GL init failed ({exc}); "
                  f"falling back to raw uploads")
            self._gl_ctx = None
            self._gl_surface = None
            return
        self._gl_ctx = ctx
        self._gl_surface = surface
        self._ovr_texture = openvr.Texture_t()
        self._ovr_texture.eType = openvr.TextureType_OpenGL
        self._ovr_texture.eColorSpace = openvr.ColorSpace_Gamma

    def _ensure_overlay(self, key: str) -> _WidgetOverlay | None:
        """Return the overlay for ``key``, creating its quad on first use.
        Returns ``None`` if creation fails (the widget is skipped this
        frame; the next frame retries)."""
        ov = self._overlays.get(key)
        if ov is not None and ov.handle is not None:
            return ov
        if ov is None:
            ov = _WidgetOverlay(key)
            self._overlays[key] = ov
        try:
            ov.handle = self._ov.createOverlay(
                f"{_OVERLAY_KEY}.{key}", f"{_OVERLAY_NAME} {key}")
            # Non-interactive HUD — no laser/mouse input on the quad.
            self._ov.setOverlayInputMethod(
                ov.handle, openvr.VROverlayInputMethod_None)
            self._apply_texture_bounds(ov)
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[overlay] VR overlay create failed ({key}): "
                  f"{type(exc).__name__}: {exc}")
            ov.handle = None
            return None
        # Force a transform apply on the next use of this fresh handle.
        ov.rect = None
        ov.shown = False
        return ov

    def _apply_texture_bounds(self, ov: _WidgetOverlay) -> None:
        """Flip the overlay's V texture coordinates when streaming GL.

        The grabbed QImage is top-down but GL samples textures bottom-up,
        so the quad renders upside down on the GL path. Swapping vMin/vMax
        on the overlay corrects it once, with no per-frame mirroring cost.
        The raw path uploads bytes in the order the compositor expects, so
        it keeps default bounds."""
        if self._gl_ctx is None:
            return
        try:
            bounds = openvr.VRTextureBounds_t()
            bounds.uMin, bounds.uMax = 0.0, 1.0
            bounds.vMin, bounds.vMax = 1.0, 0.0
            self._ov.setOverlayTextureBounds(ov.handle, bounds)
        except Exception as exc:  # pylint: disable=broad-except
            # Worst case the HUD shows flipped; never block startup on it.
            print(f"[overlay] VR texture bounds update failed: {exc}")

    def stop(self) -> None:
        """Destroy all overlays and shut OpenVR down. Idempotent."""
        if not self._running and self._ov is None:
            return
        self._safe_shutdown()
        self._running = False
        print("[overlay] VR overlay stopped")

    def _safe_shutdown(self) -> None:
        # Destroy GL textures first while the context is still current.
        try:
            if self._gl_ctx is not None:
                self._gl_ctx.makeCurrent(self._gl_surface)
                for ov in self._overlays.values():
                    for tex in ov.textures:
                        tex.destroy()
                self._gl_ctx.doneCurrent()
        except Exception:  # pylint: disable=broad-except
            pass
        try:
            if self._ov is not None:
                for ov in self._overlays.values():
                    if ov.handle is not None:
                        try:
                            self._ov.destroyOverlay(ov.handle)
                        except Exception:  # pylint: disable=broad-except
                            pass
        finally:
            self._overlays = {}
        try:
            if openvr is not None:
                openvr.shutdown()
        except Exception:  # pylint: disable=broad-except
            pass
        self._gl_ctx = None
        self._gl_surface = None
        self._ovr_texture = None
        self._ov = None
        self._system = None
        self._dash_anchor = None

    # --- placement --------------------------------------------------------
    def set_placement(self, mode: str) -> None:
        """Switch between ``"head"`` and ``"dash"``. Applies on the next
        submit (re-anchoring the dash pose), so the tray menu updates
        without a restart; otherwise it just records the choice."""
        if mode not in ("head", "dash"):
            return
        self._placement = mode
        self._placement_dirty = True

    def set_spread(self, factor: float) -> None:
        """Set the horizontal fan-out factor: how wide the widgets spread
        sideways around the cylinder (1.0 == exact desktop layout, <1.0
        pulls the corners toward centre). Re-applies every overlay
        transform on the next submit, so the tray menu updates live."""
        self._spread = float(factor)
        self._placement_dirty = True

    def set_distance(self, meters: float) -> None:
        """Set how far the panel floats from the viewer — the cylinder
        radius in metres. Larger pushes the whole HUD further away (and
        makes it subtend a smaller angle). Re-applies on the next submit."""
        self._distance = max(0.1, float(meters))
        self._placement_dirty = True

    def _apply_transform(self, ov: _WidgetOverlay,
                         x: int, y: int, w: int, h: int,
                         screen_w: int, screen_h: int) -> None:
        """Size and place ``ov``'s quad on the curved (cylindrical) screen
        so it occupies the same region the widget occupies on screen.

        The whole screen maps to a ``WIDTH_M``-wide arc, so the quad's
        physical width is the widget's pixel fraction of it; the widget's
        horizontal distance from screen centre becomes an arc length, hence
        an angle around the cylinder, and the quad is yawed to face inward.
        Vertical offset stays a straight translation (screen-Y is down,
        VR-Y is up)."""
        mpp = WIDTH_M / screen_w           # metres per screen pixel
        width_m = max(0.001, w * mpp)
        cx = x + w / 2.0
        cy = y + h / 2.0
        radius = self._distance
        # Horizontal offset -> arc length -> angle on the cylinder.
        arc = (cx - screen_w / 2.0) * mpp * self._spread
        theta = arc / radius
        dy = -(cy - screen_h / 2.0) * mpp  # +up
        try:
            self._ov.setOverlayWidthInMeters(ov.handle, width_m)
            if self._placement == "dash":
                offset = _cylinder_matrix(theta, -HEAD_DOWN_M + dy, radius)
                if self._dash_anchor is not None:
                    m = _mul_matrix(self._dash_anchor, offset)
                else:
                    # No valid pose at activation: fall back to the legacy
                    # flat seated spot (curve needs a head anchor to wrap
                    # around). Rare — tracking invalid mid-startup.
                    m = _matrix(DASH_PITCH_RAD, arc,
                                -DASH_DOWN_M + dy, -DASH_FORWARD_M)
                self._ov.setOverlayTransformAbsolute(
                    ov.handle, openvr.TrackingUniverseSeated, m)
            else:
                # Parented to the HMD: the cylinder wraps around the eyes.
                m = _cylinder_matrix(theta, -HEAD_DOWN_M + dy, radius)
                self._ov.setOverlayTransformTrackedDeviceRelative(
                    ov.handle, openvr.k_unTrackedDeviceIndex_Hmd, m)
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[overlay] VR placement update failed ({ov.key}): {exc}")
            return
        # Diagnostic (fires only on a size/placement change, not per frame):
        # surfaces the pixel rect and the derived quad geometry so a "content
        # clipped at the panel edge" report can be traced to whichever number
        # is off — texture size, quad metres, or angle.
        height_m = h * mpp
        print(f"[overlay] VR place {ov.key}: rect={w}x{h}@({x},{y}) "
              f"screen={screen_w}x{screen_h} -> quad {width_m:.3f}x{height_m:.3f} m "
              f"theta={math.degrees(theta):.1f} dy={dy:.3f}")
        ov.rect = (x, y, w, h)

    def _hmd_pose(self):
        """Sample the HMD's current pose in the seated universe as an
        ``HmdMatrix34_t``, or ``None`` when tracking isn't valid yet (e.g.
        headset asleep, runtime mid-startup)."""
        if self._system is None:
            return None
        try:
            poses = self._system.getDeviceToAbsoluteTrackingPose(
                openvr.TrackingUniverseSeated, 0.0,
                (openvr.TrackedDevicePose_t
                 * (openvr.k_unTrackedDeviceIndex_Hmd + 1))(),
            )
            pose = poses[openvr.k_unTrackedDeviceIndex_Hmd]
            if not pose.bPoseIsValid:
                return None
            return pose.mDeviceToAbsoluteTracking
        except Exception:  # pylint: disable=broad-except
            return None

    # --- frame submit -----------------------------------------------------
    def submit_widgets(self, widgets, screen_w: int, screen_h: int) -> None:
        """Push each visible widget to its own overlay quad.

        ``widgets`` is an iterable of ``(key, image, x, y, w, h)`` where
        ``image`` is the grabbed widget and ``x/y/w/h`` is its rect in
        screen-logical pixels. Only the listed widgets are visible; any
        overlay whose widget isn't listed this frame is hidden (a closed
        widget therefore uploads nothing). Each widget's image is uploaded
        at native resolution and placed on the shared plane, so the layout
        — and text sharpness — matches the desktop overlay.
        """
        if not self._running or self._ov is None:
            return
        if screen_w <= 0 or screen_h <= 0:
            return
        t0 = time.perf_counter()
        # MUST run every tick: an undrained event queue wedges the IPC
        # channel after ~10 s and every upload fails from then on (see
        # module docstring).
        self._pump_events()
        t1 = time.perf_counter()

        # Re-anchor / re-apply transforms once when the placement changed.
        reapply = self._placement_dirty
        if reapply:
            self._dash_anchor = (self._hmd_pose()
                                 if self._placement == "dash" else None)
            self._placement_dirty = False

        upload_ms = 0.0
        seen: set[str] = set()
        for key, image, x, y, w, h in widgets:
            if w <= 0 or h <= 0 or image.isNull():
                continue
            seen.add(key)
            ov = self._ensure_overlay(key)
            if ov is None:
                continue
            if reapply or ov.rect != (x, y, w, h):
                # The quad is sized from the geometry (w, h) but textured
                # from the grabbed image. If those disagree, the compositor
                # maps a full-texture quad onto the wrong aspect and content
                # reads as clipped/stretched — flag it so the mismatch is
                # visible instead of silently wrong.
                if (image.width(), image.height()) != (w, h):
                    print(f"[overlay] VR size mismatch {key}: "
                          f"geometry {w}x{h} vs image "
                          f"{image.width()}x{image.height()}")
                self._apply_transform(ov, x, y, w, h, screen_w, screen_h)
            # The texture upload wants tightly-packed RGBA8888 (stride ==
            # w*4); convert the grab once if it came back in another format.
            if image.format() != QImage.Format.Format_RGBA8888:
                image = image.convertToFormat(QImage.Format.Format_RGBA8888)
            u0 = time.perf_counter()
            if self._gl_ctx is not None:
                ok = self._upload_gl(ov, image)
            else:
                ok = self._upload_raw(ov, image)
            upload_ms += (time.perf_counter() - u0) * 1000.0
            if ok and not ov.shown:
                try:
                    self._ov.showOverlay(ov.handle)
                    ov.shown = True
                except Exception:  # pylint: disable=broad-except
                    pass

        self._hide_unused(seen)
        t2 = time.perf_counter()
        self.phase_ms = ((t1 - t0) * 1000.0,
                         (t2 - t1) * 1000.0 - upload_ms,
                         upload_ms)

    def _upload_gl(self, ov: _WidgetOverlay, image: QImage) -> bool:
        """Push one widget frame as a GL texture via ``setOverlayTexture``.
        The ring is allocated once per size and updated in place
        round-robin: creating a texture per frame doubles the driver work
        and stalls at headset rates, while the ring keeps the compositor
        reading a texture that isn't the one being overwritten."""
        w, h = image.width(), image.height()
        try:
            if not self._gl_ctx.makeCurrent(self._gl_surface):
                raise RuntimeError("makeCurrent failed")
            # (Re)allocate the ring when the widget size changes (e.g. the
            # in-overlay size-cycle button).
            if ov.tex_size != (w, h):
                for tex in ov.textures:
                    tex.destroy()
                ov.textures = []
                ov.next_tex = 0
                for _ in range(_TEXTURE_RING):
                    tex = QOpenGLTexture(QOpenGLTexture.Target.Target2D)
                    tex.setFormat(QOpenGLTexture.TextureFormat.RGBA8_UNorm)
                    tex.setSize(w, h)
                    tex.setMipLevels(1)
                    tex.setMinMagFilters(QOpenGLTexture.Filter.Linear,
                                         QOpenGLTexture.Filter.Linear)
                    tex.allocateStorage(QOpenGLTexture.PixelFormat.RGBA,
                                        QOpenGLTexture.PixelType.UInt8)
                    ov.textures.append(tex)
                ov.tex_size = (w, h)
            tex = ov.textures[ov.next_tex]
            ov.next_tex = (ov.next_tex + 1) % len(ov.textures)
            tex.setData(QOpenGLTexture.PixelFormat.RGBA,
                        QOpenGLTexture.PixelType.UInt8,
                        image.constBits())
            self._ovr_texture.handle = int(tex.textureId())
            self._ov.setOverlayTexture(ov.handle, self._ovr_texture)
        except Exception as exc:  # pylint: disable=broad-except
            self._on_submit_failure(exc, w, h)
            return False
        self._on_submit_success(w, h)
        return True

    def _upload_raw(self, ov: _WidgetOverlay, image: QImage) -> bool:
        """Fallback when GL init failed: push the frame as raw RGBA bytes.
        ``setOverlayRaw`` rebuilds the overlay texture every call (slow,
        can blink at streaming rates) but needs no GPU plumbing."""
        w, h = image.width(), image.height()
        if w == 0 or h == 0:
            return False
        # RGBA8888 has no row padding (stride == w*4), so the raw buffer is
        # exactly w*h*4 contiguous bytes.
        data = bytes(image.constBits())
        buf = (ctypes.c_char * len(data)).from_buffer_copy(data)
        try:
            # pyopenvr wraps the buffer in ``byref()`` internally, so hand it
            # the ctypes array directly. Casting to ``c_void_p`` first would
            # make ``byref`` take the address *of the pointer variable* rather
            # than of the pixel data, and the native read walks off into
            # unmapped memory (access violation, then a wedged overlay).
            self._ov.setOverlayRaw(ov.handle, buf, w, h, 4)
        except Exception as exc:  # pylint: disable=broad-except
            self._on_submit_failure(exc, w, h)
            return False
        self._on_submit_success(w, h)
        return True

    def _hide_unused(self, seen: set[str]) -> None:
        """Hide overlays whose widget wasn't submitted this frame (closed /
        hidden widgets). Hidden quads upload nothing and cost nothing; the
        handle is kept so re-showing is instant."""
        for key, ov in self._overlays.items():
            if key in seen or not ov.shown or ov.handle is None:
                continue
            try:
                self._ov.hideOverlay(ov.handle)
            except Exception:  # pylint: disable=broad-except
                pass
            ov.shown = False

    def _on_submit_failure(self, exc: Exception, w: int, h: int) -> None:
        """Shared bookkeeping for a failed submit: change-only logging and
        last-resort overlay recreation after a sustained streak. pyopenvr
        error classes carry the code in the CLASS NAME with an empty
        message, so the type is logged."""
        name = type(exc).__name__
        detail = f"{name}: {exc}" if str(exc) else name
        if detail != self._last_error:
            self._last_error = detail
            print(f"[overlay] VR submit failed ({w}x{h}): {detail}")
        # With the event queues pumped this should essentially never
        # happen; a sustained streak means something is genuinely
        # wedged, so try fresh handles as a last resort.
        self._fail_streak += 1
        now = time.monotonic()
        if self._fail_streak >= 10 and now >= self._next_recreate:
            self._next_recreate = now + 2.0
            self._recreate()

    def _on_submit_success(self, w: int, h: int) -> None:
        self._fail_streak = 0
        if self._last_error != "ok":
            self._last_error = "ok"
            print(f"[overlay] VR submit ok ({w}x{h})")

    # --- event pump / wedge recovery ---------------------------------------
    def _pump_events(self) -> None:
        """Drain the system queue and every overlay's queue. The events
        themselves are irrelevant to the HUD; what matters is that they're
        consumed (an overflowing queue permanently wedges raw uploads).
        The overlay queue is per handle, so every live overlay is pumped."""
        event = openvr.VREvent_t()
        try:
            while self._system is not None and self._system.pollNextEvent(event):
                pass
            for ov in self._overlays.values():
                if ov.handle is None:
                    continue
                while True:
                    result, _ = self._ov.pollNextOverlayEvent(ov.handle, event)
                    if not result:
                        break
        except Exception:  # pylint: disable=broad-except
            # A failed poll must never take down the submit path; worst
            # case the queue stays fuller for one tick.
            pass

    def _recreate(self) -> None:
        """Destroy every overlay quad to unwedge the compositor's
        raw-upload path; the next submit rebuilds them lazily. Throttled by
        the caller."""
        print("[overlay] VR overlays wedged; recreating")
        try:
            if self._gl_ctx is not None:
                self._gl_ctx.makeCurrent(self._gl_surface)
        except Exception:  # pylint: disable=broad-except
            pass
        for ov in self._overlays.values():
            for tex in ov.textures:
                try:
                    tex.destroy()
                except Exception:  # pylint: disable=broad-except
                    pass
            if ov.handle is not None:
                try:
                    self._ov.destroyOverlay(ov.handle)
                except Exception:  # pylint: disable=broad-except
                    pass
        # Drop all per-widget state; submit_widgets recreates on demand.
        self._overlays = {}
        self._placement_dirty = True


__all__ = ["VROverlayOutput"]
