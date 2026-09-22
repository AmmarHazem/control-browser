from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import urlsplit

ALLOWED_SCHEMES = {"http", "https"}
DEFAULT_PORTS = {"http": 80, "https": 443}


def parse_target(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise ValueError(f"scheme {parts.scheme or '(none)'!r} is not allowed; use http or https")
    if not parts.hostname:
        raise ValueError("url has no host")
    port = parts.port or DEFAULT_PORTS[parts.scheme.lower()]
    return parts.hostname.lower(), port


def matches_domain(host: str, pattern: str) -> bool:
    host = host.lower().rstrip(".")
    pattern = pattern.lower().strip().rstrip(".")
    if pattern.startswith("*."):
        return host.endswith("." + pattern[2:])
    return host == pattern or host.endswith("." + pattern)


def is_blocked_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


@dataclass
class DomainPolicy:
    allowed: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    operator_blocked: list[str] = field(default_factory=list)

    def reason_to_block(self, host: str) -> str | None:
        for pattern in self.operator_blocked:
            if matches_domain(host, pattern):
                return f"{host} is on the operator blocked-domain list"
        for pattern in self.blocked:
            if matches_domain(host, pattern):
                return f"{host} is on this run's blocked-domain list"
        if self.allowed and not any(matches_domain(host, p) for p in self.allowed):
            return f"{host} is not on this run's allowed-domain list"
        return None


class NetworkPolicyError(Exception):
    pass


async def resolve_addresses(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return [info[4][0] for info in infos]


async def check_url(url: str, policy: DomainPolicy, *, allow_private: bool = False) -> None:
    try:
        host, port = parse_target(url)
    except ValueError as exc:
        raise NetworkPolicyError(str(exc)) from exc

    reason = policy.reason_to_block(host)
    if reason:
        raise NetworkPolicyError(reason)

    if allow_private:
        return

    try:
        addresses = await resolve_addresses(host, port)
    except OSError as exc:
        raise NetworkPolicyError(f"{host} could not be resolved: {exc}") from exc

    for address in addresses:
        if is_blocked_address(address):
            raise NetworkPolicyError(
                f"{host} resolves to the internal address {address}, which is not reachable"
            )
