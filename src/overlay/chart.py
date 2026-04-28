from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor


pg.setConfigOptions(antialias=True, useOpenGL=False, background=None, foreground="w")


class LiveChart(pg.PlotWidget):
    """Ring-buffer plot tuned for 60+ Hz telemetry streams.

    Holds the last `window_size` samples per series and pushes new data via
    `append`. Drawing uses a fixed numpy buffer so we never allocate inside
    the hot path.
    """

    SERIES_COLORS = {
        "throttle": (0, 220, 120),
        "brake": (235, 70, 70),
        "speed": (90, 170, 255),
    }

    def __init__(self, window_size: int = 600) -> None:
        super().__init__()
        self.window_size = window_size

        self.setBackground(None)  # transparent canvas
        self.showGrid(x=False, y=True, alpha=0.15)
        self.setMouseEnabled(x=False, y=False)
        self.setMenuEnabled(False)
        self.hideButtons()
        self.getPlotItem().getViewBox().setBackgroundColor(QColor(0, 0, 0, 90))

        # Y axis fixed for normalized 0..1 inputs; speed will be scaled.
        self.setYRange(0.0, 1.0, padding=0.0)
        self.setXRange(0, window_size, padding=0.0)

        self._x = np.arange(window_size, dtype=np.float32)
        self._buffers: dict[str, np.ndarray] = {}
        self._curves: dict[str, pg.PlotDataItem] = {}
        for name, color in self.SERIES_COLORS.items():
            buf = np.zeros(window_size, dtype=np.float32)
            pen = pg.mkPen(color=color, width=2)
            curve = self.plot(self._x, buf, pen=pen, name=name)
            self._buffers[name] = buf
            self._curves[name] = curve

        legend = self.addLegend(offset=(8, 8))
        for name, curve in self._curves.items():
            legend.addItem(curve, name)

    def append(self, sample: dict[str, float]) -> None:
        for name, buf in self._buffers.items():
            value = float(sample.get(name, 0.0))
            buf[:-1] = buf[1:]
            buf[-1] = value
            self._curves[name].setData(self._x, buf)
