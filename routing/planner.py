"""Cost-minimising fuel purchase planner.

The vehicle drives along a line from mile 0 to ``total_miles``. Stations sit at
known mile markers with a fixed price per gallon. The tank holds
``range_miles / mpg`` gallons and may be filled partially at any station.

The following greedy rule gives the minimum total cost:

* at a station, look ahead for the next station whose price is strictly lower
  than the current one (the destination counts as free fuel);
* if it is within a full tank, buy just enough to reach it;
* otherwise fill the tank, because nothing ahead within range is cheaper.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

EPS = 1e-9


class InfeasibleRoute(Exception):
    """Raised when two consecutive refuel points are farther than the range."""

    def __init__(self, message: str, at_mile: float, gap_miles: float):
        super().__init__(message)
        self.at_mile = at_mile
        self.gap_miles = gap_miles


@dataclass(frozen=True)
class Purchase:
    index: int            # index into the station sequence given to plan_purchases
    mile: float
    gallons: float
    price: float
    tank_on_arrival: float  # gallons in the tank when reaching the station


def plan_purchases(
    miles: Sequence[float],
    prices: Sequence[float],
    total_miles: float,
    range_miles: float,
    mpg: float,
    start_fuel_gallons: float | None = None,
) -> list[Purchase]:
    """Return the cheapest set of purchases, ordered along the route.

    ``miles`` must be sorted ascending and every value must lie in
    ``[0, total_miles]``.
    """
    capacity = range_miles / mpg
    fuel0 = capacity if start_fuel_gallons is None else min(float(start_fuel_gallons), capacity)
    n = len(miles)

    # Reachability: every gap between consecutive points must fit in the tank.
    points = [0.0, *miles, total_miles]
    for k in range(len(points) - 1):
        gap = points[k + 1] - points[k]
        allowed = fuel0 * mpg if k == 0 else range_miles
        if gap > allowed + 1e-7:
            raise InfeasibleRoute(
                f"No fuel station within reach after mile {points[k]:.1f} "
                f"(next one is {gap:.1f} miles away, reach is {allowed:.1f}).",
                at_mile=points[k],
                gap_miles=gap,
            )

    if n == 0:
        return []

    # next_cheaper[i]: first index after i with a strictly lower price; n is the destination.
    # Equal prices are interchangeable, so ties are served by the earlier station,
    # which avoids chains of tiny top-ups.
    ext_prices = [*prices, -1.0]
    ext_miles = [*miles, total_miles]
    next_cheaper = [n] * n
    stack = [n]
    for i in range(n - 1, -1, -1):
        while ext_prices[stack[-1]] >= ext_prices[i]:
            stack.pop()
        next_cheaper[i] = stack[-1]
        stack.append(i)

    purchases: list[Purchase] = []
    fuel = fuel0 - miles[0] / mpg
    for i in range(n):
        fuel = max(fuel, 0.0)
        arrival = fuel
        target = next_cheaper[i]
        distance = ext_miles[target] - ext_miles[i]
        if distance <= range_miles + 1e-7:
            buy = max(0.0, distance / mpg - fuel)
        else:
            buy = capacity - fuel
        buy = min(buy, capacity - fuel)
        if buy > EPS:
            purchases.append(Purchase(i, ext_miles[i], buy, ext_prices[i], arrival))
            fuel += buy
        fuel -= (ext_miles[i + 1] - ext_miles[i]) / mpg
    return purchases
