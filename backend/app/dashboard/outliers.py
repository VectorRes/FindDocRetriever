"""Outlier ratio values in a trend, and what drove them (ID-HU-FE-004).

Deterministic, no model:

- **Detection.** A value is flagged when, against the previous calculated
  period, it moves more than DASHBOARD_OUTLIER_REL_CHANGE of the previous
  value (50% by default) *and* more than a minimum absolute change for its
  unit (5 percentage points for percentages, 0.5x for multiples), so 2% ->
  3.5% isn't an alarm but ROE 12% -> 34% is. With four or more points, a value
  far from the series' median (robust z-score over 3.5, median/MAD) is
  flagged too. Only "calculated" values are compared: ones held for review or
  not calculable are left out of the trend.
- **Drivers.** For ROE the DuPont decomposition, ROE = net margin x asset
  turnover x equity multiplier (equity multiplier = ROE / ROA = total assets
  / equity), says whether profitability, efficiency or leverage moved it; ROA
  = net margin x asset turnover likewise. Any other ratio (or ROE/ROA when a
  component isn't available for both periods) is explained by the change in
  its inputs (numerator and denominator).
"""
import math
from dataclasses import dataclass, field
from statistics import median

from app.config import get_settings

ROBUST_Z_LIMIT = 3.5
MIN_POINTS_FOR_Z = 4


@dataclass
class Driver:
    name: str
    previous: float | None
    current: float | None
    # "percent" (a fraction), "times", or "amount" (a statement line item).
    unit: str
    # Relative change (0.5 = +50%); None when the previous value is 0.
    change: float | None = None
    note: str | None = None


@dataclass
class Outlier:
    previous_period: str | None
    previous_value: float | None
    change: float | None          # relative to the previous value
    absolute_change: float | None
    method: str                   # "period_change" | "robust_z"
    message: str = ""
    drivers: list[Driver] = field(default_factory=list)


def _min_absolute(unit: str) -> float:
    settings = get_settings()
    return settings.dashboard_outlier_min_points if unit == "percent" else settings.dashboard_outlier_min_times


def _relative(previous: float, current: float) -> float | None:
    return None if previous == 0 else (current - previous) / abs(previous)


def detect_outliers(points: list[tuple[str, float]], unit: str) -> dict[str, Outlier]:
    """`points` are (period, value) of calculated values in chronological
    order; returns the flagged ones by period."""
    threshold = get_settings().dashboard_outlier_rel_change
    flagged: dict[str, Outlier] = {}
    for (prev_period, previous), (period, current) in zip(points, points[1:]):
        absolute = current - previous
        relative = _relative(previous, current)
        big_move = relative is None or abs(relative) > threshold
        if big_move and abs(absolute) >= _min_absolute(unit):
            flagged[period] = Outlier(prev_period, previous, relative, absolute, "period_change")

    if len(points) >= MIN_POINTS_FOR_Z:
        values = [value for _, value in points]
        center = median(values)
        spread = median(abs(v - center) for v in values) * 1.4826
        if spread > 0:
            for index, (period, value) in enumerate(points):
                if period in flagged or abs(value - center) < _min_absolute(unit):
                    continue
                if abs(value - center) / spread > ROBUST_Z_LIMIT:
                    prev_period, previous = points[index - 1] if index else (None, None)
                    flagged[period] = Outlier(
                        prev_period, previous,
                        _relative(previous, value) if previous is not None else None,
                        value - previous if previous is not None else None, "robust_z",
                    )
    return flagged


# --- drivers ----------------------------------------------------------------------

_DUPONT = {
    # ratio -> its components: (name, how it's computed from same-period ratios, unit, what it means)
    "roe": (
        ("Net margin", "net_margin", "percent", "profitability"),
        ("Asset turnover", "asset_turnover", "times", "efficiency"),
        ("Equity multiplier", "equity_multiplier", "times", "leverage"),
    ),
    "roa": (
        ("Net margin", "net_margin", "percent", "profitability"),
        ("Asset turnover", "asset_turnover", "times", "efficiency"),
    ),
}


def _components(values: dict[str, float]) -> dict[str, float]:
    """DuPont components from one period's calculated ratios."""
    found = dict(values)
    if "roe" in values and values.get("roa"):
        found["equity_multiplier"] = values["roe"] / values["roa"]
    return found


def _format(value: float | None, unit: str) -> str:
    if value is None:
        return "—"
    if unit == "percent":
        return f"{value * 100:.1f}%"
    if unit == "times":
        return f"{value:.2f}x"
    return f"{value:,.0f}"


def _log_change(driver: Driver) -> float:
    if driver.previous and driver.current and driver.previous > 0 and driver.current > 0:
        return abs(math.log(driver.current / driver.previous))
    return abs(driver.change or 0)


def explain(
    ratio_key: str,
    ratio_name: str,
    outlier: Outlier,
    period: str,
    previous_ratios: dict[str, float],
    current_ratios: dict[str, float],
    previous_inputs: list[dict],
    current_inputs: list[dict],
    unit: str,
) -> Outlier:
    """Fill in the outlier's drivers and the message prompting a review.

    `*_ratios` are the calculated ratio values of each period (by key) and
    `*_inputs` this ratio's inputs as stored (key, name, value)."""
    drivers: list[Driver] = []
    before, after = _components(previous_ratios), _components(current_ratios)
    components = _DUPONT.get(ratio_key, ())
    if components and all(key in before and key in after for _, key, _, _ in components):
        for name, key, component_unit, meaning in components:
            drivers.append(Driver(
                name, before[key], after[key], component_unit, _relative(before[key], after[key]), note=meaning,
            ))
    else:
        previous_by_key = {i.get("key"): i for i in previous_inputs if i.get("found")}
        for item in current_inputs:
            old = previous_by_key.get(item.get("key"))
            if not item.get("found") or old is None or item.get("value") is None or old.get("value") is None:
                continue
            drivers.append(Driver(
                item.get("name") or item.get("key"), old["value"], item["value"], "amount",
                _relative(old["value"], item["value"]),
            ))

    direction = "rose" if (outlier.absolute_change or 0) > 0 else "fell"
    if outlier.previous_period is not None:
        headline = (f"{ratio_name} {direction} from {_format(outlier.previous_value, unit)} in "
                    f"{outlier.previous_period} to {_format(outlier.previous_value + (outlier.absolute_change or 0), unit)} "
                    f"in {period}")
    else:
        headline = f"{ratio_name} in {period} is far from its usual level"
    if drivers:
        main = max(drivers, key=_log_change)
        moved = "rose" if (main.current or 0) > (main.previous or 0) else "fell"
        reason = (f"; the main driver is {main.name.lower()}, which {moved} from "
                  f"{_format(main.previous, main.unit)} to {_format(main.current, main.unit)}")
        if main.note:
            reason += f" (a {main.note} change)"
    else:
        reason = ""
    outlier.drivers = drivers
    outlier.message = f"{headline}{reason}. Review the underlying drivers before presenting this value."
    return outlier
