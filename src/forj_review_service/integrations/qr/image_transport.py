"""Pin image requests to validated DNS addresses, including every redirect hop."""

import asyncio
import ipaddress
import socket

import httpx


async def resolve_addresses(host: str, port: int) -> list[str]:
    records = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(record[4][0] for record in records))


class ImageTransport(httpx.AsyncBaseTransport):
    def __init__(self, *, private_hosts=(), resolver=resolve_addresses, transport=None):
        self.private_hosts = {host.lower().rstrip(".") for host in private_hosts}
        self.resolver = resolver
        self.transport = transport
        self.transports = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host.lower().rstrip(".")
        if request.url.scheme not in {"http", "https"} or request.url.userinfo:
            raise ValueError("图片地址无效")
        addresses = await self.resolver(host, request.url.port or (443 if request.url.scheme == "https" else 80))
        if not addresses:
            raise ValueError("图片域名未解析到有效地址")
        if host not in self.private_hosts and any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise ValueError("图片地址指向未授权的本机或内部网络")
        # Connect to the checked IP rather than resolving the hostname again. Keep
        # the original Host header and TLS hostname verification for virtual hosts.
        key = (host, request.url.port, request.url.scheme)
        if key not in self.transports:
            # A connection verified for one TLS hostname must not be reused for
            # another hostname which happens to resolve to the same IP.
            self.transports[key] = self.transport or httpx.AsyncHTTPTransport()
        transport = self.transports[key]
        for index, address in enumerate(addresses):
            pinned = httpx.Request(request.method, request.url.copy_with(host=address),
                headers=request.headers, stream=request.stream,
                extensions={**request.extensions, "sni_hostname": request.url.host})
            try:
                return await transport.handle_async_request(pinned)
            except httpx.ConnectError:
                if index == len(addresses) - 1:
                    raise

    async def aclose(self):
        for transport in set(self.transports.values()):
            await transport.aclose()
