"""Typed configuration publication conflicts, shared by maintenance and services."""


class PublicationConflict(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)
