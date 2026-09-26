"""Telegram Ads API failures with retry and ambiguity metadata."""


class TelegramAdsError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool = False,
        ambiguous: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.ambiguous = ambiguous
