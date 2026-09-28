import hmac
import hashlib
from datetime import datetime, timedelta, timezone as py_timezone
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from apps.accounts.models import RateLimitBucket


class RateLimitExceeded(ValidationError):
    def __init__(self, message="Rate limit exceeded. Please try again later.", retry_after_seconds=60):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def hash_network_key(ip_address: str, salt: str = "network_key_v1") -> str:
    """
    Returns a rotating keyed HMAC-SHA256 digest of the IP address.
    Avoids storing raw IP addresses in signals and limit buckets.
    """
    if not ip_address:
        return "unknown_network"
    key = f"{settings.SECRET_KEY}:{salt}".encode("utf-8")
    return hmac.new(key, ip_address.strip().encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def check_rate_limit(
    scope: str,
    subject_key: str,
    limit: int,
    window_seconds: int,
    cost: int = 1,
    now: datetime = None,
) -> None:
    """
    Atomically increments and enforces a rate limit window against PostgreSQL RateLimitBucket.
    Raises RateLimitExceeded if bucket count exceeds the specified limit.
    """
    if now is None:
        now = timezone.now()

    now_epoch = int(now.timestamp())
    window_epoch = (now_epoch // window_seconds) * window_seconds
    window_start = datetime.fromtimestamp(window_epoch, tz=py_timezone.utc)
    expires_at = window_start + timedelta(seconds=window_seconds * 2)

    with transaction.atomic():
        bucket, created = RateLimitBucket.objects.select_for_update().get_or_create(
            scope=scope,
            subject_key=subject_key,
            window_start=window_start,
            defaults={"count": 0, "expires_at": expires_at},
        )

        if bucket.count + cost > limit:
            retry_after = max(1, int(window_seconds - (now_epoch - window_epoch)))
            raise RateLimitExceeded(
                message=f"Rate limit exceeded for {scope}. Try again in {retry_after}s.",
                retry_after_seconds=retry_after,
            )

        bucket.count += cost
        bucket.expires_at = expires_at
        bucket.save(update_fields=["count", "expires_at"])
