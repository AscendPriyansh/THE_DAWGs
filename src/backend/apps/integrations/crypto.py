import hmac
import hashlib
import secrets
import time
from typing import Optional
from django.conf import settings
from cryptography.fernet import Fernet


def get_fernet_cipher() -> Fernet:
    key = getattr(settings, "WEBHOOK_ENCRYPTION_KEY", None)
    if not key:
        # Fallback to key derived from SECRET_KEY
        import base64
        digest = hashlib.sha256(f"webhook-secret-salt:{settings.SECRET_KEY}".encode("utf-8")).digest()
        key = base64.urlsafe_b64encode(digest).decode("ascii")
    if isinstance(key, str):
        key = key.encode("ascii")
    return Fernet(key)


def generate_webhook_secret() -> str:
    """Generate high-entropy shared secret for an endpoint, prefixed with 'whsec_'."""
    return f"whsec_{secrets.token_urlsafe(32)}"


def encrypt_webhook_secret(secret: str) -> str:
    """Encrypt endpoint secret using external deployment key."""
    f = get_fernet_cipher()
    encrypted = f.encrypt(secret.encode("utf-8"))
    return encrypted.decode("ascii")


def decrypt_webhook_secret(encrypted_secret: str) -> str:
    """Decrypt endpoint secret using external deployment key."""
    f = get_fernet_cipher()
    decrypted = f.decrypt(encrypted_secret.encode("ascii"))
    return decrypted.decode("utf-8")


def sign_webhook_payload(secret: str, timestamp_str: str, raw_body_bytes: bytes) -> str:
    """
    Sign ASCII(timestamp) + '.' + raw_body_bytes using HMAC-SHA256.
    Returns signature formatted as 'v1=<hex_hmac>'.
    """
    to_sign = f"{timestamp_str}.".encode("ascii") + raw_body_bytes
    digest = hmac.new(secret.encode("utf-8"), to_sign, hashlib.sha256).hexdigest()
    return f"v1={digest}"


def verify_webhook_signature(
    secret: str,
    timestamp_str: str,
    raw_body_bytes: bytes,
    signature_header: str,
    tolerance_seconds: int = 300,
    current_time: Optional[float] = None,
) -> bool:
    """
    Verify webhook signature in constant time with timestamp tolerance check.
    """
    if current_time is None:
        current_time = time.time()

    # Check timestamp validity
    try:
        ts = float(timestamp_str)
    except (ValueError, TypeError):
        return False

    if abs(current_time - ts) > tolerance_seconds:
        return False

    # Check signature scheme
    expected_prefix = "v1="
    if not signature_header.startswith(expected_prefix):
        return False

    received_digest = signature_header[len(expected_prefix) :].strip()
    to_sign = f"{timestamp_str}.".encode("ascii") + raw_body_bytes
    expected_digest = hmac.new(secret.encode("utf-8"), to_sign, hashlib.sha256).hexdigest()

    return hmac.compare_digest(expected_digest, received_digest)
