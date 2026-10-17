"""Shared Python helpers."""


def cents(amount: float) -> int:
    """Convert a currency amount to whole cents."""
    return round(amount * 100)
