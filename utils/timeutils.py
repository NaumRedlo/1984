"""Time helpers.

The whole codebase stores **naive UTC** datetimes in the database (columns are
plain ``DateTime`` without ``timezone=True``), so all "now" values must be naive
UTC too — mixing naive and aware datetimes raises ``TypeError`` on comparison.

``datetime.utcnow()`` produced exactly this value but is deprecated since 3.12,
so use :func:`utcnow` instead: it is value-identical (naive UTC) without the
deprecation warning.
"""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return the current UTC time as a naive datetime (tzinfo stripped)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
