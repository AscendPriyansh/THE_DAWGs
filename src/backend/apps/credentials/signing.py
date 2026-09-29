"""
Ed25519 signing and verification for credentials.

Uses the `cryptography` library's Ed25519 operations.
Payload encoding: deterministic JSON with sorted keys, compact separators,
UTF-8, strings/integers/booleans only, no non-finite numbers.
"""

import base64
import hashlib
import json
import os
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from cryptography.exceptions import InvalidSignature


def generate_ed25519_keypair():
    """Generate a new Ed25519 keypair. Returns (private_key, public_key_bytes, key_id)."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    public_bytes = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    key_id = f"dgk_{uuid.uuid4().hex[:16]}"
    return private_key, public_bytes, key_id


def serialize_private_key(private_key: Ed25519PrivateKey) -> bytes:
    """Serialize private key to PEM bytes for secure storage."""
    return private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())


def load_private_key(pem_bytes: bytes) -> Ed25519PrivateKey:
    """Load a private key from PEM bytes."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    return load_pem_private_key(pem_bytes, password=None)


def load_public_key(raw_bytes: bytes) -> Ed25519PublicKey:
    """Load a public key from raw 32 bytes."""
    return Ed25519PublicKey.from_public_bytes(raw_bytes)


def canonical_json_bytes(payload: dict) -> bytes:
    """
    Deterministic JSON encoding: sorted keys, compact separators, UTF-8.
    Only strings/integers/booleans and None. No non-finite numbers.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sign_payload(private_key: Ed25519PrivateKey, payload_bytes: bytes) -> bytes:
    """Sign exact payload bytes with Ed25519. Returns 64-byte signature."""
    return private_key.sign(payload_bytes)


def verify_signature(public_key_bytes: bytes, payload_bytes: bytes, signature_bytes: bytes) -> bool:
    """Verify Ed25519 signature. Returns True if valid, False otherwise."""
    try:
        public_key = load_public_key(public_key_bytes)
        public_key.verify(signature_bytes, payload_bytes)
        return True
    except (InvalidSignature, Exception):
        return False


def build_credential_payload(
    credential_id: str,
    issuer_id: str,
    key_id: str,
    event_id: str,
    event_name: str,
    subject_display_name: str,
    kind: str,
    issued_at: str,
    completed_review_count: int = None,
    publication_id: str = None,
    pdf_sha256: str = None,
) -> dict:
    """Build the canonical credential payload dictionary."""
    payload = {
        "schema_version": "1.0",
        "credential_id": credential_id,
        "issuer_id": issuer_id,
        "key_id": key_id,
        "event_id": event_id,
        "event_name": event_name,
        "subject_display_name": subject_display_name,
        "kind": kind,
        "issued_at": issued_at,
    }
    if completed_review_count is not None:
        payload["completed_review_count"] = completed_review_count
    if publication_id is not None:
        payload["publication_id"] = publication_id
    if pdf_sha256 is not None:
        payload["pdf_sha256"] = pdf_sha256
    return payload


def build_downloadable_envelope(payload_bytes: bytes, signature_bytes: bytes, key_id: str) -> dict:
    """Build the downloadable credential envelope with base64url encoding."""
    return {
        "payload_bytes": base64.urlsafe_b64encode(payload_bytes).decode("ascii"),
        "signature": base64.urlsafe_b64encode(signature_bytes).decode("ascii"),
        "key_id": key_id,
        "algorithm": "ED25519",
    }


def sha256_hex(data: bytes) -> str:
    """Compute SHA-256 hex digest."""
    return hashlib.sha256(data).hexdigest()
