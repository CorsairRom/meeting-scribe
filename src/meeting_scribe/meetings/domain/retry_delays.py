"""The one rule for storage retry delays, shared by the settings and the processing config."""


def check_retry_delays(delays: tuple[float, ...]) -> tuple[float, ...]:
    """At least one delay, every delay above zero; returns ``delays`` or raises ``ValueError``."""
    if not delays or any(delay <= 0 for delay in delays):
        raise ValueError("storage_retry_delays_seconds needs at least one delay, all > 0")
    return delays
