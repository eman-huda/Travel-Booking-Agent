"""Exceptions raised inside tool handlers. They look like ordinary service failures."""


class ToolTimeoutError(Exception):
    def __init__(self, service: str, seconds: float):
        super().__init__(f"{service} did not respond within {seconds:.1f}s")


class UpstreamAPIError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}")
