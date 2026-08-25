class AppError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class PermissionDenied(AppError):
    def __init__(self, message: str = "You do not have permission to use this command.") -> None:
        super().__init__("permission_denied", message)


class ValidationFailed(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("validation_failed", message)
