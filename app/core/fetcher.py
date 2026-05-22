from __future__ import annotations

import ipaddress
import asyncio
import socket
from urllib.parse import urlparse

import dns.resolver
import httpx


class FetchError(ValueError):
    pass


BLOCKED_HOSTS = {"localhost"}
BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "::1/128",
    )
)
FAKE_IP_NETWORKS = (
    ipaddress.ip_network("198.18.0.0/15"),
)


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise FetchError("subscription_url must use http or https")
    if not parsed.hostname:
        raise FetchError("subscription_url must include a hostname")

    hostname = parsed.hostname.strip().lower()
    if hostname in BLOCKED_HOSTS or hostname.endswith(".localhost"):
        raise FetchError("local hostnames are not allowed")

    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None

    if ip is not None and _is_blocked_ip(ip):
        raise FetchError("private or local IP URLs are not allowed")


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(ip in network for network in BLOCKED_NETWORKS) or ip.is_private or ip.is_loopback


def _blocked_ip_message(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    if any(ip in network for network in FAKE_IP_NETWORKS):
        return "subscription host resolves to a private or local IP; 198.18.x.x is often a proxy fake-ip, so run the app with a reachable proxy/DNS"
    return "subscription host resolves to a private or local IP"


def _is_fake_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(ip in network for network in FAKE_IP_NETWORKS)


def _resolve_via_udp_dns(hostname: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Query 8.8.8.8:53 directly over UDP, bypassing the system resolver."""
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = ["8.8.8.8"]
    resolver.timeout = 5
    resolver.lifetime = 5
    ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for rdtype in ("A", "AAAA"):
        try:
            for rdata in resolver.resolve(hostname, rdtype):
                try:
                    ips.append(ipaddress.ip_address(str(rdata)))
                except ValueError:
                    pass
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers, dns.exception.Timeout):
            pass
    return ips


async def _resolve_via_doh(hostname: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """DNS-over-HTTPS to Cloudflare 1.1.1.1 by IP, so fake-ip DNS cannot intercept it."""
    ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    async with httpx.AsyncClient(timeout=5.0) as client:
        for rdtype, rtype_id in (("A", 1), ("AAAA", 28)):
            try:
                resp = await client.get(
                    "https://1.1.1.1/dns-query",
                    params={"name": hostname, "type": rdtype},
                    headers={"Accept": "application/dns-json"},
                )
                if resp.status_code == 200:
                    for answer in resp.json().get("Answer", []):
                        if answer.get("type") == rtype_id:
                            try:
                                ips.append(ipaddress.ip_address(answer["data"]))
                            except (ValueError, KeyError):
                                pass
            except (httpx.HTTPError, Exception):
                pass
    return ips


async def _ensure_resolved_host_is_public(hostname: str) -> None:
    try:
        results = await asyncio.to_thread(socket.getaddrinfo, hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError(f"could not resolve subscription host: {hostname}") from exc

    fake_ip_hits: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for result in results:
        try:
            ip = ipaddress.ip_address(result[4][0])
        except ValueError:
            continue
        if _is_fake_ip(ip):
            fake_ip_hits.append(ip)
        elif _is_blocked_ip(ip):
            raise FetchError(_blocked_ip_message(ip))

    if not fake_ip_hits:
        return

    # System DNS returned fake-ips (Clash fake-ip mode).
    # Try UDP DNS to 8.8.8.8, then DoH to 1.1.1.1 (IP-based, immune to fake-ip).
    for resolver_fn in (
        lambda h: asyncio.to_thread(_resolve_via_udp_dns, h),
        _resolve_via_doh,
    ):
        all_ips = await resolver_fn(hostname)
        # Drop fake-ips — the resolver may also have been intercepted by Clash
        public_candidates = [ip for ip in all_ips if not _is_fake_ip(ip)]
        if not public_candidates:
            continue
        for ip in public_candidates:
            if _is_blocked_ip(ip):
                raise FetchError(_blocked_ip_message(ip))
        return  # At least one public non-blocked IP confirmed

    raise FetchError(_blocked_ip_message(fake_ip_hits[0]))


async def fetch_subscription(url: str) -> str:
    current_url = url

    headers = {"User-Agent": "subflow/0.1 (+https://github.com/local/subflow)"}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False, headers=headers) as client:
            for _ in range(6):
                _validate_url(current_url)
                parsed = urlparse(current_url)
                await _ensure_resolved_host_is_public(parsed.hostname)  # type: ignore[arg-type]

                response = await client.get(current_url)
                if not response.is_redirect:
                    if response.status_code < 200 or response.status_code >= 300:
                        raise FetchError(f"subscription fetch failed with HTTP {response.status_code}")
                    return response.text

                redirect_url = response.headers.get("location")
                if not redirect_url:
                    raise FetchError("subscription redirect response is missing Location")
                current_url = str(response.url.join(redirect_url))
            else:
                raise FetchError("subscription fetch exceeded redirect limit")
    except httpx.HTTPError as exc:
        raise FetchError(f"failed to fetch subscription: {exc}") from exc
