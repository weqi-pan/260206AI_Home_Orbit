import ipaddress
import socket
from urllib.parse import urlsplit


class UnsafeEndpointUrl(ValueError):
    pass


def resolve_host(host: str) -> list[str]:
    return list({item[4][0] for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)})


def _is_forbidden(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return bool(ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_multicast or ip.is_unspecified)


def validate_endpoint_url(url: str, resolver=resolve_host, allow_private: bool = False) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise UnsafeEndpointUrl("Endpoint URL must be an HTTP(S) origin without user info")
    try:
        answers = resolver(parsed.hostname)
    except OSError as exc:
        raise UnsafeEndpointUrl("Endpoint hostname could not be resolved") from exc
    if not answers:
        raise UnsafeEndpointUrl("Endpoint hostname has no addresses")
    if not allow_private and any(_is_forbidden(address) for address in answers):
        raise UnsafeEndpointUrl("Endpoint URL resolves to a forbidden network")
    return url.rstrip("/")
