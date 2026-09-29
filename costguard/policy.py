"""Policy verdict: check cost delta against budget threshold."""
from __future__ import annotations

from decimal import Decimal


def evaluate_policy(
    net_delta: Decimal,
    max_increase: float | None,
) -> tuple[bool, str]:
    """Evaluate the budget policy.

    Args:
        net_delta: Net monthly cost impact.
        max_increase: Budget threshold (--max-increase), or None if not set.

    Returns:
        (passed, message) where passed is True if within budget or no budget set.
    """
    if max_increase is None:
        return True, "No budget threshold set"

    threshold = Decimal(str(max_increase))

    if net_delta <= threshold:
        return True, f"PASSED (Within budget allowance)"
    else:
        overage = net_delta - threshold
        return False, f"FAILED (Exceeds budget allowance by +${overage:,.2f}/mo)"
