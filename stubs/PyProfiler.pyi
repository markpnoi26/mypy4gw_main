# PyProfiler stub — Py4GW performance counters (2026-07-18)
# Matches src/profiler/profiler_bindings.cpp in Py4GW_Reforged_Native
# (PYBIND11_EMBEDDED_MODULE(PyProfiler)). Legacy Console.get_profiler_* parity.

from typing import List, Tuple

# A single metric's aggregated report row, as returned by get_reports():
#   (name, min, avg, p50, p95, p99, max)  — all times in milliseconds.
MetricReport = Tuple[str, float, float, float, float, float, float]

# ═══════════════ REPORTING ═══════════════════════════════════════

def get_metric_names() -> List[str]:
    """Return the names of every metric the profiler is currently tracking."""
    ...

def get_reports() -> List[MetricReport]:
    """Return one aggregated report per metric.

    Each row is (name, min, avg, p50, p95, p99, max), all in milliseconds,
    computed over the metric's retained sample history.
    """
    ...

def get_history(metric_name: str) -> List[float]:
    """Return the retained per-sample history (milliseconds) for one metric.

    Returns an empty list if the metric has no samples / does not exist.
    """
    ...

def reset() -> None:
    """Clear all retained profiler history for every metric."""
    ...

# ═══════════════ MANUAL TIMING ═══════════════════════════════════
# Time your own sections independently of the callback scheduler. `end` stamps
# the current frame internally, matching the scheduler's timing.

def start(name: str) -> None:
    """Begin timing a named metric."""
    ...

def end(name: str) -> None:
    """End timing a named metric (stamps the current frame internally)."""
    ...
