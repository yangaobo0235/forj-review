"""Errors whose messages are intentionally safe for API consumers."""


class ReviewInputError(ValueError):
    """Invalid review input, including requests validated outside HTTP."""


class ReviewJobNotFound(KeyError):
    """The requested in-memory job no longer exists."""


class VehicleModelCatalogUnavailable(Exception):
    """Catalog availability diagnostics containing no raw driver errors."""


class ReviewResourceError(Exception):
    def __init__(self, code: str, message: str, status: int):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


class ConfigurationInputError(ValueError):
    """An explicit declaration/configuration constraint safe for API consumers."""
