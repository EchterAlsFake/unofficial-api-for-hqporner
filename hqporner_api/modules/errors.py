from base_api.modules.errors import (
    ScraperException,
    VideoUnavailable,
    NotFound,
    NetworkError,
    BotDetection,
    ProxyError,
    UnknownNetworkError,
    DownloadFailed,
)


class InvalidActress(ScraperException):
    def __init__(self, message: str = "Invalid Actress!"):
        self.message = message
        super().__init__(message)


class NotAvailable(VideoUnavailable):
    def __init__(self, message: str = "The video is unavailable, because the CDN network which saves the videos has an issue"):
        self.message = message
        super().__init__(message)


__all__ = [
    "InvalidActress",
    "NotAvailable",
    "NotFound",
    "NetworkError",
    "BotDetection",
    "ProxyError",
    "UnknownNetworkError",
    "DownloadFailed",
]
