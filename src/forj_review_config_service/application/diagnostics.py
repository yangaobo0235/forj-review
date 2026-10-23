"""Log code locations and identifiers without exception text or business payloads."""

import traceback


def log_failure(logger, exc, **context):
    frames = traceback.extract_tb(exc.__traceback__)
    locations = [
        f"{frame.filename.replace(chr(92), '/').rsplit('/', 1)[-1]}:{frame.lineno}:{frame.name}"
        for frame in frames[-6:]
    ]
    logger.error(
        "context=%s error_type=%s code=%s locations=%s diagnostic=%s",
        context,
        type(exc).__name__,
        getattr(exc, "code", "EXECUTION_FAILED"),
        locations,
        getattr(exc, "diagnostic", {}),
    )
