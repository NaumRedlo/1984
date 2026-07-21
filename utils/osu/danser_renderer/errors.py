"""danser render exceptions, shared across the subsystem."""


class DanserError(Exception):
    """Raised when danser-cli fails."""
    pass


class DanserNotFoundError(DanserError):
    """Raised when danser-cli binary is not found."""
    pass


class RenderQueueFullError(DanserError):
    """Raised when too many renders are queued."""
    pass
