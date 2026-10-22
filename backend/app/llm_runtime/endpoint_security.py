import ipaddress
import socket
import ssl
from urllib.parse import urlsplit

import httpcore
import httpx


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


class PinnedNetworkBackend(httpcore.NetworkBackend):
    def __init__(self, resolver=resolve_host, connector=None):
        self.resolver = resolver
        self.connector = connector or httpcore.SyncBackend().connect_tcp

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        answers = self.resolver(host)
        if not answers or any(_is_forbidden(address) for address in answers):
            raise UnsafeEndpointUrl("Endpoint DNS is unsafe at request time")
        return self.connector(host=answers[0], port=port, timeout=timeout,
                              local_address=local_address, socket_options=socket_options)

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise UnsafeEndpointUrl("Unix sockets are not allowed for model endpoints")


class PinnedHTTPTransport(httpx.HTTPTransport):
    def __init__(self, resolver=resolve_host, verify=True):
        ssl_context = httpx.create_ssl_context(verify=verify, trust_env=False)
        self._pool = httpcore.ConnectionPool(
            ssl_context=ssl_context,
            max_connections=20,
            max_keepalive_connections=0,
            keepalive_expiry=0,
            retries=0,
            network_backend=PinnedNetworkBackend(resolver=resolver),
        )


def safe_http_client(resolver=resolve_host) -> httpx.Client:
    return httpx.Client(transport=PinnedHTTPTransport(resolver=resolver), trust_env=False)
