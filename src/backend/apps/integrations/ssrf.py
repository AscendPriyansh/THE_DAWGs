import ipaddress
import socket
from typing import Tuple, List, Optional
from urllib.parse import urlsplit
from django.conf import settings


FORBIDDEN_METADATA_IPS = {
    "169.254.169.254",
    "100.100.100.200",  # Alibaba Cloud metadata
    "fd00:ec2::254",
}


def validate_webhook_url(url: str) -> Tuple[bool, str, List[str]]:
    """
    Validates a webhook destination URL against SSRF attacks.
    Returns (is_valid, error_message, resolved_ip_list).
    """
    if not url or not isinstance(url, str):
        return False, "URL must be a non-empty string", []

    try:
        parsed = urlsplit(url)
    except Exception as e:
        return False, f"Malformed URL: {e}", []

    # 1. Reject userinfo
    if parsed.username or parsed.password:
        return False, "URLs with embedded userinfo (username:password) are forbidden.", []

    hostname = parsed.hostname
    if not hostname:
        return False, "URL hostname is required.", []

    port = parsed.port

    # 2. Check deployment allowlist for offline testing or demo receivers
    allowed_hosts = getattr(settings, "DOGFOOD_ALLOWED_WEBHOOK_HOSTS", [])
    if hostname.lower() in [h.lower() for h in allowed_hosts]:
        # Allow allowlisted hosts (e.g. testserver, localhost in tests)
        return True, "", ["127.0.0.1"]

    # 3. Scheme check (must be HTTPS in normal operation)
    if parsed.scheme.lower() != "https":
        return False, "Webhook endpoints must use HTTPS.", []

    # 4. Port check (must be 443 or default)
    if port is not None and port != 443:
        return False, f"Forbidden port {port}. Webhooks only permit default HTTPS port 443.", []

    # 5. Resolve DNS and inspect resolved IPs
    resolved_ips: List[str] = []
    try:
        addr_info = socket.getaddrinfo(hostname, port or 443, type=socket.SOCK_STREAM)
        for family, socktype, proto, canonname, sockaddr in addr_info:
            ip_str = sockaddr[0]
            if ip_str not in resolved_ips:
                resolved_ips.append(ip_str)
    except socket.gaierror as e:
        return False, f"DNS resolution failed for host '{hostname}': {e}", []
    except Exception as e:
        return False, f"Resolution error: {e}", []

    if not resolved_ips:
        return False, f"Could not resolve host '{hostname}'.", []

    # 6. Check each IP against reserved/private/loopback ranges
    for ip_str in resolved_ips:
        if ip_str in FORBIDDEN_METADATA_IPS:
            return False, f"Destination IP {ip_str} is forbidden (cloud metadata endpoint).", []

        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            return False, f"Invalid resolved IP address '{ip_str}'.", []

        if ip_obj.is_loopback:
            return False, f"Destination IP {ip_str} is loopback and forbidden.", []
        if ip_obj.is_private:
            return False, f"Destination IP {ip_str} is in private network space and forbidden.", []
        if ip_obj.is_link_local:
            return False, f"Destination IP {ip_str} is link-local and forbidden.", []
        if ip_obj.is_multicast:
            return False, f"Destination IP {ip_str} is multicast and forbidden.", []
        if ip_obj.is_reserved:
            return False, f"Destination IP {ip_str} is reserved and forbidden.", []
        if not ip_obj.is_global:
            return False, f"Destination IP {ip_str} is not a global routable address.", []

    return True, "", resolved_ips
