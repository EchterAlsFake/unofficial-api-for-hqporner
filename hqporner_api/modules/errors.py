from base_api.modules.errors import (
    NotFound,
    NetworkError,
    BotDetection,
    ProxyError,
    UnknownNetworkError,
    DownloadFailed,
)


class InvalidActress(Exception):
    def __init__(self):
        self.message = "Invalid Actress!"


class NotAvailable(Exception):
    def __init__(self):
        self.message = "The video is unavailable, because the CDN network which saves the videos has an issue"


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