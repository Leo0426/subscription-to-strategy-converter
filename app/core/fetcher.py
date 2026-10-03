from __future__ import annotations

import ipaddress
import asyncio
import os
import socket
from urllib.parse import urlparse

import dns.resolver
import dns.asyncresolver
import json
import httpx

from app.core.network import fetch_timeout, max_subscription_bytes, outbound_client
from app.core.address_binding import ADDRESS_EXTENSION


class FetchError(ValueError):
    pass


class FetchInvalidError(FetchError):
    """The source URL or response violates an input or safety constraint."""


BLOCKED_HOSTS = {"localhost"}
FAKE_IP_NETWORKS = (
    ipaddress.ip_network("198.18.0.0/15"),
)
NAT64_WELL_KNOWN_NETWORK = ipaddress.ip_network("64:ff9b::/96")
# Universal subscription endpoints negotiate their output from this header.
# Include both names: older panels recognize "meta", newer ones "mihomo".
# This is the input format capability, independent of the requested output target.
DEFAULT_SUBSCRIPTION_USER_AGENT = "clash.meta/1.19.30 mihomo/1.19.30 subflow/0.1"


def _validate_url(url: str) -> None:
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
    except ValueError as exc:
        raise FetchInvalidError("subscription_url is invalid") from exc
    if parsed.scheme not in {"http", "https"}:
        raise FetchInvalidError("subscription_url must use http or https")
    if not hostname:
        raise FetchInvalidError("subscription_url must include a hostname")

    hostname = hostname.strip().lower()
    if hostname in BLOCKED_HOSTS or hostname.endswith(".localhost"):
        raise FetchInvalidError("local hostnames are not allowed")

    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None

    if ip is not None and _is_blocked_ip(ip):
        raise FetchInvalidError("private or local IP URLs are not allowed")


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # IPv4-mapped IPv6 must obey the underlying IPv4 address restrictions.
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip in NAT64_WELL_KNOWN_NETWORK:
            # RFC 6052 permits the well-known NAT64 prefix only for global
            # IPv4 addresses. Check the translated endpoint, not the prefix.
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        elif ip.is_site_local:
            return True
    # Shared carrier space is neither private nor global. Multicast can be
    # classified as global, but is never a public HTTP subscription endpoint.
    return not ip.is_global or ip.is_multicast or ip.is_reserved


def _blocked_ip_message(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    if any(ip in network for network in FAKE_IP_NETWORKS):
        return "subscription host resolves to a private or local IP; 198.18.x.x is often a proxy fake-ip, so run the app with a reachable proxy/DNS"
    return "subscription host resolves to a private or local IP"


def _is_fake_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(ip in network for network in FAKE_IP_NETWORKS)


async def _resolve_via_udp_dns(hostname: str) -> list:
    resolver = dns.asyncresolver.Resolver(configure=False)
    resolver.nameservers = ["8.8.8.8"]
    resolver.timeout = resolver.lifetime = 5
    async def query(rdtype):
        try:
            answer = await resolver.resolve(hostname, rdtype)
            return [ipaddress.ip_address(str(record)) for record in answer]
        except (dns.exception.DNSException, ValueError):
            return []
    answers = await asyncio.gather(query('A'), query('AAAA'))
    return [ip for answer in answers for ip in answer]


#: JSON DoH endpoints queried by IP so fake-ip DNS cannot intercept them.
#: Cloudflare is unreachable from some networks (where the fake-ip proxy runs);
#: AliDNS covers those, so at least one side of the firewall always answers.
_DOH_ENDPOINTS = (
    "https://1.1.1.1/dns-query",
    "https://223.5.5.5/resolve",
)


async def _resolve_via_doh(hostname: str) -> list:
    async with outbound_client() as client:
        async def query(endpoint, kind):
            try:
                async with client.stream('GET', endpoint, params={'name': hostname, 'type': kind},
                                         headers={'Accept': 'application/dns-json'}, timeout=5) as response:
                    if response.status_code != 200:
                        return []
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > 65536:
                            return []
                        content.extend(chunk)
                    payload = json.loads(content)
                    if not isinstance(payload, dict) or not isinstance(payload.get('Answer', []), list):
                        return []
                    return [ipaddress.ip_address(record['data']) for record in payload.get('Answer', [])
                            if isinstance(record, dict) and record.get('type') == kind]
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                return []
        answers = await asyncio.gather(*(query(endpoint, kind) for endpoint in _DOH_ENDPOINTS for kind in (1, 28)))
        return [ip for answer in answers for ip in answer]


async def _ensure_resolved_host_is_public(hostname: str) -> tuple[str, ...]:
    try:
        results = await asyncio.to_thread(socket.getaddrinfo, hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError(f"could not resolve subscription host: {hostname}") from exc

    fake_ip_hits: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    public_addresses: list[str] = []
    for result in results:
        try:
            ip = ipaddress.ip_address(result[4][0])
        except ValueError:
            continue
        if _is_fake_ip(ip):
            fake_ip_hits.append(ip)
        elif _is_blocked_ip(ip):
            raise FetchInvalidError(_blocked_ip_message(ip))
        else:
            public_addresses.append(str(ip))

    if not fake_ip_hits:
        if not public_addresses:
            raise FetchError(f"could not resolve subscription host: {hostname}")
        return tuple(dict.fromkeys(public_addresses))

    tasks = [asyncio.create_task(resolver(hostname)) for resolver in (_resolve_via_udp_dns, _resolve_via_doh)]
    try:
        for completed in asyncio.as_completed(tasks):
            candidates = [ip for ip in await completed if not _is_fake_ip(ip)]
            if not candidates:
                continue
            for ip in candidates:
                if _is_blocked_ip(ip):
                    raise FetchInvalidError(_blocked_ip_message(ip))
            return tuple(dict.fromkeys(str(ip) for ip in candidates))
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    raise FetchInvalidError(_blocked_ip_message(fake_ip_hits[0]))


async def fetch_subscription(url: str, *, target: str = "mihomo") -> str:
    user_agent = (
        (os.environ.get("SUBFLOW_SHADOWROCKET_USER_AGENT", "").strip() if target == "shadowrocket" else "")
        or os.environ.get("SUBFLOW_SUBSCRIPTION_USER_AGENT", "").strip()
        or ("Shadowrocket/2.2.70" if target == "shadowrocket" else "")
        or DEFAULT_SUBSCRIPTION_USER_AGENT
    )
    return await request_text(url, headers={"User-Agent": user_agent})


async def request_text(url: str, *, headers: dict | None = None, params: dict | None = None,
                       public: bool = True, redirects: bool = True) -> str:
    """Bound the whole request, including validation, redirects and one retry.

    Only operator-configured compatibility endpoints may use public=False;
    the original subscription still passes public validation before forwarding.
    """
    try:
        async with asyncio.timeout(fetch_timeout()), outbound_client() as client:
            current_url = url
            # Keep provider redirect tickets within this fetch; the pooled
            # client's own jar rejects cookies from every subscription.
            cookies = httpx.Cookies()
            for hop in range(6):
                addresses = ()
                if public:
                    _validate_url(current_url)
                    addresses = await _ensure_resolved_host_is_public(urlparse(current_url).hostname)
                for attempt in range(2):
                    if attempt and public:
                        addresses = await _ensure_resolved_host_is_public(urlparse(current_url).hostname)
                    try:
                        async with client.stream('GET', current_url, headers=headers, params=params, cookies=cookies,
                                                 extensions={ADDRESS_EXTENSION: addresses}) as response:
                            cookies.extract_cookies(response)
                            if response.is_redirect and redirects:
                                location = response.headers.get('location')
                                if not location:
                                    raise FetchInvalidError('subscription redirect response is missing Location')
                                current_url = str(response.url.join(location))
                                params = None
                                break
                            if response.status_code in {502, 503, 504} and attempt == 0:
                                await asyncio.sleep(0.1)
                                continue
                            if response.status_code in {401, 403, 404, 410}:
                                # Denied or missing sources cannot authorize a stale
                                # publication. Keep outages/rate limits recoverable.
                                raise FetchInvalidError(f'subscription source rejected or unavailable: HTTP {response.status_code}')
                            if not 200 <= response.status_code < 300:
                                raise FetchError(f'subscription fetch failed with HTTP {response.status_code}')
                            content = bytearray()
                            limit = max_subscription_bytes()
                            async for chunk in response.aiter_bytes():
                                if len(content) + len(chunk) > limit:
                                    raise FetchInvalidError('subscription exceeds decoded size limit')
                                content.extend(chunk)
                            return content.decode(response.encoding or 'utf-8', errors='replace')
                    except httpx.TransportError:
                        if attempt:
                            raise
                        await asyncio.sleep(0.1)
            raise FetchInvalidError('subscription fetch exceeded redirect limit')
    except TimeoutError as exc:
        raise FetchError('subscription refresh deadline exceeded') from exc
    except httpx.InvalidURL as exc:
        raise FetchInvalidError('subscription_url is invalid') from exc
    except httpx.HTTPError as exc:
        raise FetchError(f'subscription network failure ({type(exc).__name__})') from exc
