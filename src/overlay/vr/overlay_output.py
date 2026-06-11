"""Submit the HUD to SteamVR as a single fixed-size overlay quad.

Each frame the visible widgets are grabbed individually (cheap — only the
pixels actually drawn) and composited onto one **constant-size** canvas at
their screen-relative positions; that single canvas is the one frame
submitted per tick, keeping the upload small (~2.6 MB vs ~19 MB for the
whole desktop) and the layout identical to the desktop overlay.

Two submission paths, both verified empirically against SteamVR with
``scripts/vr_probe.py``:

* **GL texture streaming** (primary) — the canvas is uploaded into a
  persistent OpenGL texture on an offscreen Qt context and handed to the
  compositor via ``setOverlayTexture``, which swaps it atomically.
* **Raw upload** (fallback when GL init fails) — ``setOverlayRaw``
  rebuilds the overlay's backing texture on every call (~35–110 ms on the
  UI thread) and the quad can render empty between uploads, which shows
  as *blinking* at streaming rates. Usable, but strictly worse.

Hard-won rule for either path: **an overlay app must drain its VR event
queues.** The runtime delivers ~15–25 events/s; if they are never polled
the queue fills after roughly 10 seconds and the IPC channel wedges —
every subsequent submit fails with ``OverlayError_RequestFailed``,
forever, even on a freshly recreated overlay handle. Only a process with
a fresh ``openvr.init`` recovers, which is why the symptom looks like "the
HUD shows one frame then never updates". Both queues are pumped every
tick (``_pump_events``); a throttled destroy-and-recreate (``_recreate``)
remains as last-resort recovery should a submit streak still fail.

Two placements (see :meth:`set_placement`):

* ``"head"`` — quad parented to the HMD, fixed in front of the eyes.
  Never jitters, always visible; the safe default across every headset.
* ``"dash"`` — quad frozen in the seated play space exactly where it
  floats at the moment the mode is activated: the HMD pose is sampled
  once and composed with the head-locked offset, so the panel locks in
  place in front of wherever the user is currently looking. More
  immersive, but OpenVR world-locking can jitter on some headsets
  (notably Quest over a streamed link), which is why it isn't the
  default. Re-selecting the mode re-anchors at the current gaze.

Everything is guarded: if ``openvr`` can't be imported or SteamVR isn't
running, :meth:`start` logs and returns ``False`` and the caller stays on
the desktop overlay.
"""
from __future__ import annotations

import ctypes
import math
import time

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (QImage, QOffscreenSurface, QOpenGLContext,
                           QPainter)
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
# Tunable once the user can try it on the gaming PC. The panel keeps the
# screen's aspect ratio; only its physical WIDTH is set — height follows
# from the submitted image's aspect.
WIDTH_M = 2.0          # physical width of the quad in metres
HEAD_DISTANCE_M = 1.4  # head-locked: how far in front of the eyes
HEAD_DOWN_M = 0.30     # head-locked: drop it slightly below eye line
# Legacy seated-dashboard spot — only used as the "dash" fallback when the
# HMD pose can't be sampled at activation (tracking not valid yet).
DASH_FORWARD_M = 0.55  # distance forward of the seated origin
DASH_DOWN_M = 0.45     # how far below the seated eye height
DASH_PITCH_RAD = math.radians(35.0)  # tilt top toward driver

# Fixed canvas width (px) the whole screen is composited into before upload.
# Held CONSTANT for the overlay's life so the raw dimensions never change
# (see rule 1 above). Smaller than the desktop to stay well inside the raw
# upload size that the compositor accepts; large enough to keep widget text
# legible on a ~2 m quad read from over a metre away.
CANVAS_WIDTH = 1280

# OpenVR's overlay key must be process-unique; the name is the human label
# shown in SteamVR's overlay list.
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


class VROverlayOutput:
    """Lifecycle wrapper around one OpenVR overlay. Lives on the UI thread;
    every openvr call is guarded so a missing runtime degrades to a no-op."""

    def __init__(self, placement: str = "head") -> None:
        self._placement = placement if placement in ("head", "dash") else "head"
        self._ov = None       # IVROverlay
        self._system = None    # IVRSystem, for the system event queue
        self._handle = None    # VROverlayHandle_t
        self._running = False
        self._canvas_h = 0     # fixed once the screen aspect is known
        self._last_error = ""  # last submit error name, to log only changes
        self._fail_streak = 0  # consecutive submit failures
        self._next_recreate = 0.0  # monotonic deadline for the next rebuild
        # GL texture streaming state (None => raw-upload fallback).
        self._gl_ctx = None        # QOpenGLContext
        self._gl_surface = None    # QOffscreenSurface
        self._gl_textures = []     # ring of live QOpenGLTextures
        self._ovr_texture = None   # reusable openvr.Texture_t

    # --- queries ----------------------------------------------------------
    @staticmethod
    def available() -> bool:
        """Whether the ``openvr`` binding loaded at import time."""
        return openvr is not None

    def is_running(self) -> bool:
        """Whether an overlay is live and accepting frames."""
        return self._running

    @property
    def placement(self) -> str:
        """Current placement mode (``"head"`` / ``"dash"``)."""
        return self._placement

    # --- lifecycle --------------------------------------------------------
    def start(self) -> bool:
        """Init OpenVR as an overlay app and create + show the quad.

        Returns ``True`` on success. On any failure (no binding, SteamVR
        not running, overlay creation rejected) it logs a single line and
        returns ``False`` so the caller can stay on the desktop overlay.
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
            self._handle = self._ov.createOverlay(_OVERLAY_KEY, _OVERLAY_NAME)
            self._ov.setOverlayWidthInMeters(self._handle, WIDTH_M)
            # Non-interactive HUD — no laser/mouse input on the quad.
            self._ov.setOverlayInputMethod(
                self._handle, openvr.VROverlayInputMethod_None
            )
            self._apply_placement()
            self._ov.showOverlay(self._handle)
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[overlay] VR overlay init failed: {exc}")
            self._safe_shutdown()
            return False
        self._init_gl()
        self._apply_texture_bounds()
        self._running = True
        mode = "gl" if self._gl_ctx is not None else "raw"
        print(f"[overlay] VR overlay started "
              f"(placement={self._placement}, submit={mode})")
        return True

    def _init_gl(self) -> None:
        """Set up the offscreen GL context used to stream frames via
        ``setOverlayTexture``. The raw path (``setOverlayRaw``) recreates
        the overlay's backing texture on every call (~35–110 ms) and the
        quad can render empty between uploads — visible blinking at
        streaming rates. A persistent GL texture is swapped atomically by
        the compositor instead. On any failure GL stays off and submits
        fall back to the raw path."""
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

    def _apply_texture_bounds(self) -> None:
        """Flip the overlay's V texture coordinates when streaming GL.

        The composited QImage is top-down but GL samples textures
        bottom-up, so the quad renders upside down on the GL path.
        Swapping vMin/vMax on the overlay corrects it once, with no
        per-frame mirroring cost. The raw path uploads bytes in the
        order the compositor expects, so it keeps default bounds."""
        if self._gl_ctx is None:
            return
        try:
            bounds = openvr.VRTextureBounds_t()
            bounds.uMin, bounds.uMax = 0.0, 1.0
            bounds.vMin, bounds.vMax = 1.0, 0.0
            self._ov.setOverlayTextureBounds(self._handle, bounds)
        except Exception as exc:  # pylint: disable=broad-except
            # Worst case the HUD shows flipped; never block startup on it.
            print(f"[overlay] VR texture bounds update failed: {exc}")

    def stop(self) -> None:
        """Destroy the overlay and shut OpenVR down. Idempotent."""
        if not self._running and self._ov is None:
            return
        self._safe_shutdown()
        self._running = False
        print("[overlay] VR overlay stopped")

    def _safe_shutdown(self) -> None:
        try:
            if self._ov is not None and self._handle is not None:
                self._ov.destroyOverlay(self._handle)
        except Exception:  # pylint: disable=broad-except
            pass
        try:
            if openvr is not None:
                openvr.shutdown()
        except Exception:  # pylint: disable=broad-except
            pass
        try:
            if self._gl_ctx is not None:
                self._gl_ctx.makeCurrent(self._gl_surface)
                for tex in self._gl_textures:
                    tex.destroy()
                self._gl_ctx.doneCurrent()
        except Exception:  # pylint: disable=broad-except
            pass
        self._gl_textures = []
        self._gl_ctx = None
        self._gl_surface = None
        self._ovr_texture = None
        self._ov = None
        self._system = None
        self._handle = None

    # --- placement --------------------------------------------------------
    def set_placement(self, mode: str) -> None:
        """Switch between ``"head"`` and ``"dash"``. Applies live if the
        overlay is already running, so the tray menu updates without a
        restart; otherwise it just records the choice for :meth:`start`."""
        if mode not in ("head", "dash"):
            return
        self._placement = mode
        if self._running and self._ov is not None:
            try:
                self._apply_placement()
            except Exception as exc:  # pylint: disable=broad-except
                print(f"[overlay] VR placement update failed: {exc}")

    def _apply_placement(self) -> None:
        if self._placement == "dash":
            # World-anchored exactly where the panel currently floats:
            # the head-locked offset composed with the HMD pose sampled at
            # activation, so the quad freezes in place in front of wherever
            # the user is looking right now. Falls back to the legacy
            # seated-dashboard spot if no valid pose is available yet.
            offset = _matrix(0.0, 0.0, -HEAD_DOWN_M, -HEAD_DISTANCE_M)
            hmd = self._hmd_pose()
            if hmd is not None:
                m = _mul_matrix(hmd, offset)
            else:
                m = _matrix(DASH_PITCH_RAD, 0.0, -DASH_DOWN_M, -DASH_FORWARD_M)
            self._ov.setOverlayTransformAbsolute(
                self._handle, openvr.TrackingUniverseSeated, m
            )
        else:
            # Parented to the HMD: fixed in front of the eyes, no rotation.
            m = _matrix(0.0, 0.0, -HEAD_DOWN_M, -HEAD_DISTANCE_M)
            self._ov.setOverlayTransformTrackedDeviceRelative(
                self._handle, openvr.k_unTrackedDeviceIndex_Hmd, m
            )

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
        """Composite the visible widgets onto the fixed canvas and push it.

        ``widgets`` is an iterable of ``(key, image, x, y, w, h)`` where
        ``image`` is the grabbed widget and ``x/y/w/h`` is its rect in
        screen-logical pixels. Each widget is drawn at the same relative
        position on a constant ``CANVAS_WIDTH``×``_canvas_h`` canvas, which
        is uploaded as the single raw frame. Holding the canvas size fixed
        is what keeps the compositor accepting frames (see module docstring).
        """
        if not self._running or self._ov is None or self._handle is None:
            return
        if screen_w <= 0 or screen_h <= 0:
            return
        # MUST run every tick: an undrained event queue wedges the IPC
        # channel after ~10 s and every upload fails from then on (see
        # module docstring).
        self._pump_events()
        # Lock the canvas height to the screen aspect on first use; never
        # changes afterwards, so the raw dimensions stay constant.
        if self._canvas_h == 0:
            self._canvas_h = max(1, round(CANVAS_WIDTH * screen_h / screen_w))
        scale = CANVAS_WIDTH / screen_w

        canvas = QImage(CANVAS_WIDTH, self._canvas_h,
                        QImage.Format.Format_RGBA8888)
        canvas.fill(0)  # fully transparent everywhere a widget isn't drawn
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        for _key, image, x, y, w, h in widgets:
            if w <= 0 or h <= 0 or image.isNull():
                continue
            painter.drawImage(
                QRectF(x * scale, y * scale, w * scale, h * scale), image
            )
        painter.end()

        if self._gl_ctx is not None:
            self._submit_gl(canvas)
        else:
            self._submit_raw(canvas)

    def _submit_gl(self, image: QImage) -> None:
        """Push one composited frame as a GL texture via
        ``setOverlayTexture`` — the streaming path. The compositor keeps
        reading the submitted texture between submits, so a small ring of
        textures stays alive and only the oldest is destroyed."""
        try:
            if not self._gl_ctx.makeCurrent(self._gl_surface):
                raise RuntimeError("makeCurrent failed")
            tex = QOpenGLTexture(
                image, QOpenGLTexture.MipMapGeneration.DontGenerateMipMaps)
            self._ovr_texture.handle = int(tex.textureId())
            self._ov.setOverlayTexture(self._handle, self._ovr_texture)
            self._gl_textures.append(tex)
            if len(self._gl_textures) > 3:
                self._gl_textures.pop(0).destroy()
        except Exception as exc:  # pylint: disable=broad-except
            self._on_submit_failure(exc, image.width(), image.height())
            return
        self._on_submit_success(image.width(), image.height())

    def _submit_raw(self, image: QImage) -> None:
        """Fallback when GL init failed: push the frame as raw RGBA bytes.
        ``setOverlayRaw`` rebuilds the overlay texture every call (slow,
        can blink at streaming rates) but needs no GPU plumbing.
        """
        w, h = image.width(), image.height()
        if w == 0 or h == 0:
            return
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
            self._ov.setOverlayRaw(self._handle, buf, w, h, 4)
        except Exception as exc:  # pylint: disable=broad-except
            self._on_submit_failure(exc, w, h)
            return
        self._on_submit_success(w, h)

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
        # wedged, so try a fresh handle as a last resort.
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
        """Drain the system and overlay event queues. The events themselves
        are irrelevant to the HUD; what matters is that they're consumed
        (an overflowing queue permanently wedges raw uploads)."""
        event = openvr.VREvent_t()
        try:
            while self._system is not None and self._system.pollNextEvent(event):
                pass
            while True:
                result, _ = self._ov.pollNextOverlayEvent(self._handle, event)
                if not result:
                    break
        except Exception:  # pylint: disable=broad-except
            # A failed poll must never take down the submit path; worst
            # case the queue stays fuller for one tick.
            pass

    def _recreate(self) -> None:
        """Destroy and rebuild the overlay quad in-place to unwedge the
        compositor's raw-upload path. Throttled by the caller."""
        print("[overlay] VR overlay wedged; recreating")
        try:
            self._ov.destroyOverlay(self._handle)
        except Exception:  # pylint: disable=broad-except
            pass
        try:
            self._handle = self._ov.createOverlay(_OVERLAY_KEY, _OVERLAY_NAME)
            self._ov.setOverlayWidthInMeters(self._handle, WIDTH_M)
            self._ov.setOverlayInputMethod(
                self._handle, openvr.VROverlayInputMethod_None
            )
            self._apply_placement()
            self._apply_texture_bounds()
            self._ov.showOverlay(self._handle)
        except Exception as exc:  # pylint: disable=broad-except
            # Next throttle window retries; the key may be transiently
            # in use right after the destroy.
            print(f"[overlay] VR overlay recreate failed: "
                  f"{type(exc).__name__}: {exc}")


__all__ = ["VROverlayOutput"]
