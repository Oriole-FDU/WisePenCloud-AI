from __future__ import annotations

import pytest

from chat.application.tools.utils.url import (
    UrlSecurityError,
    validate_public_http_url,
)


@pytest.mark.parametrize(
    "url",
    (
        "http://127.0.0.1",
        "http://10.0.0.1",
        "http://[::1]",
        "file:///etc/passwd",
        "https://user:password@example.com",
        "https://example.com:8080",
    ),
)
def test_url_security_rejects_non_public_targets(url: str) -> None:
    with pytest.raises(UrlSecurityError):
        validate_public_http_url(url)
