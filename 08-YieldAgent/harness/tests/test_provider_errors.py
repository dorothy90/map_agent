
import httpx
from openai import RateLimitError


def test_retry_delay_honors_provider_seconds_and_http_date():
    from harness.executor import retry_delay
    from datetime import datetime, timezone, timedelta
    from email.utils import format_datetime
    response = httpx.Response(429, headers={"retry-after": "12.5"}, request=httpx.Request("POST", "https://fixture.invalid"))
    error = RateLimitError("fixture", response=response, body=None)
    assert 12.5 <= retry_delay(error, 0) < 13.1
    response.headers["retry-after"] = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=15))
    assert 13 < retry_delay(error, 0) < 16
