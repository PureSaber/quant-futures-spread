"""framework/incremental.py —— O(1) 增量滚动算子（与 CTA framework.incremental 对齐）。"""
from __future__ import annotations

from collections import deque
from typing import Deque, Optional

import numpy as np
from sortedcontainers import SortedList


class IncrementalRollingMean:
    """O(1) rolling mean，与 pd.Series.rolling(window).mean() 等价。"""

    __slots__ = ("_window", "_min_periods", "_buf", "_sum", "_n_non_nan")

    def __init__(self, window: int, min_periods: Optional[int] = None) -> None:
        self._window = int(window)
        self._min_periods = int(min_periods) if min_periods is not None else int(window)
        self._buf: Deque[float] = deque(maxlen=self._window)
        self._sum: float = 0.0
        self._n_non_nan: int = 0

    def update(self, x: float) -> float:
        if len(self._buf) >= self._window:
            old = self._buf[0]
            if old == old:
                self._sum -= old
                self._n_non_nan -= 1
        self._buf.append(x)
        if x == x:
            self._sum += x
            self._n_non_nan += 1
        if self._n_non_nan < self._min_periods:
            return float("nan")
        return self._sum / self._n_non_nan


class IncrementalRollingStd:
    """O(1) rolling std（ddof=1）。"""

    __slots__ = ("_window", "_min_periods", "_buf", "_sum", "_sum_sq", "_n_non_nan")

    def __init__(self, window: int, min_periods: Optional[int] = None) -> None:
        self._window = int(window)
        self._min_periods = int(min_periods) if min_periods is not None else int(window)
        self._buf: Deque[float] = deque(maxlen=self._window)
        self._sum: float = 0.0
        self._sum_sq: float = 0.0
        self._n_non_nan: int = 0

    def update(self, x: float) -> float:
        if len(self._buf) >= self._window:
            old = self._buf[0]
            if old == old:
                self._sum -= old
                self._sum_sq -= old * old
                self._n_non_nan -= 1
        self._buf.append(x)
        if x == x:
            self._sum += x
            self._sum_sq += x * x
            self._n_non_nan += 1
        if self._n_non_nan < self._min_periods or self._n_non_nan < 2:
            return float("nan")
        n = self._n_non_nan
        mean = self._sum / n
        var = (self._sum_sq - n * mean * mean) / (n - 1)
        if var < 0.0 and var > -1e-12:
            var = 0.0
        return float(np.sqrt(var)) if var >= 0.0 else float("nan")


class IncrementalRollingQuantile:
    """O(log N) rolling quantile（linear），与 pandas rolling.quantile 等价。"""

    __slots__ = ("_window", "_min_periods", "_q", "_buf", "_sorted")

    def __init__(self, window: int, q: float, min_periods: Optional[int] = None) -> None:
        self._window = int(window)
        self._min_periods = int(min_periods) if min_periods is not None else int(window)
        self._q = float(q)
        self._buf: Deque[float] = deque(maxlen=self._window)
        self._sorted: SortedList = SortedList()

    def update(self, x: float) -> float:
        if len(self._buf) >= self._window:
            old = self._buf[0]
            if old == old:
                self._sorted.remove(old)
        self._buf.append(x)
        if x == x:
            self._sorted.add(x)
        n = len(self._sorted)
        if n < self._min_periods:
            return float("nan")
        if n == 1:
            return self._sorted[0]
        idx = (n - 1) * self._q
        lo = int(idx)
        if lo >= n - 1:
            return self._sorted[n - 1]
        frac = idx - lo
        return self._sorted[lo] + frac * (self._sorted[lo + 1] - self._sorted[lo])


__all__ = ["IncrementalRollingMean", "IncrementalRollingStd", "IncrementalRollingQuantile"]
