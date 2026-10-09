class DomainError(Exception):
    """A business rule refused an operation; ``message`` is safe to show to the user."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
