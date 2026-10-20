"""报废证明二维码核验。

主要职责：核验专用解码器取得的二维码地址、校验官方域名并提取官网字段。
修改日期：2026-08-26
"""

import asyncio
import json
import logging
import re
import ssl
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from html import unescape
from typing import Any
from urllib.parse import urlparse

import httpx

from forj_review_service.contracts.recognition import RetryAttempt

logger = logging.getLogger(__name__)


@dataclass
class QrCodeResult:
    image_index: int | None
    raw_value: str
    url: str | None = None
    domain_valid: bool | None = None
    page_fields: dict[str, Any] = field(default_factory=dict)
    status: str = "REVIEW_REQUIRED"
    accessible: bool | None = None
    retryable: bool = False
    error_code: str | None = None
    http_status: int | None = None
    message: str = ""


def validate_qr_url(url: str, allowed_hosts: list[str]) -> bool:
    """只允许 HTTPS 且命中白名单主机，避免二维码触发任意外部跳转。"""
    try:
        parsed = urlparse(url)
        return parsed.scheme == "https" and canonicalize_official_qr_url(url, allowed_hosts) is not None
    except ValueError:
        return False


def canonicalize_official_qr_url(url: str, allowed_hosts: list[str]) -> str | None:
    """Accept an official legacy HTTP QR only by upgrading it to HTTPS before access."""
    try:
        parsed = urlparse(url)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in set(allowed_hosts)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 80, 443}
        ):
            return None
        port = parsed.port
    except ValueError:
        return None
    hostname = parsed.hostname or ""
    netloc = hostname if port in {None, 80} else f"{hostname}:443"
    return parsed._replace(scheme="https", netloc=netloc).geturl()


def create_official_ssl_context(*, tls12_only: bool = False) -> ssl.SSLContext:
    """Keep verified modern TLS, with TLS 1.2 compatibility for connection retries."""
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if tls12_only:
        context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers("DEFAULT@SECLEVEL=1")
    return context


def extract_page_fields(html: str) -> dict[str, str]:
    """Extract certificate number/VIN from common official HTML or embedded JSON."""
    raw = unescape(html)
    result: dict[str, str] = {}

    # Some versions of the official page render values in JSON before hydration.
    # Parse JSON-looking script/text fragments first so labels need not be adjacent.
    json_keys = {
        "certificate_no": ("certificate_no", "certificateNo", "recycleCertificateNo", "回收证明编号", "证明编号"),
        "vin": ("vin", "vehicleVin", "vehicleIdentificationNo", "车架号", "车辆识别代号"),
    }
    for candidate in re.findall(r"(?:\{|\[)[^<>]{0,20000}(?:\}|\])", raw, flags=re.DOTALL):
        try:
            values = json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        objects = values if isinstance(values, list) else [values]
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            for field_name, keys in json_keys.items():
                if field_name in result:
                    continue
                for key in keys:
                    value = obj.get(key)
                    if isinstance(value, (str, int, float)) and str(value).strip():
                        result[field_name] = str(value).strip()
                        break

    text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"\s+", " ", text).strip()
    patterns = {
        "certificate_no": r"(?:回收证明编号|报废证明编号|证明编号|回收证明号|certificate[_\s-]*no|certificateNo)\s*[:：=]?\s*([A-Za-z0-9一-龥][A-Za-z0-9一-龥\-/]*)",
        "vin": r"(?:车架号|车辆识别代号|车辆识别码|VIN|vehicle[_\s-]*vin)\s*[:：=]?\s*([A-Za-z0-9]{6,32})",
    }
    for field_name, pattern in patterns.items():
        if field_name not in result:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                result[field_name] = match.group(1).strip()
    return result


class QrWebVerifier:
    """Safely fetch and parse an official QR page."""

    def __init__(
        self,
        *,
        allowed_hosts: list[str],
        timeout_seconds: float = 8.0,
        max_bytes: int = 2_000_000,
        max_redirects: int = 2,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
    ) -> None:
        self.allowed_hosts = [host.lower().rstrip(".") for host in allowed_hosts]
        self.timeout = httpx.Timeout(timeout_seconds)
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.transport = transport

    async def verify(self, url: str, image_index: int | None = None) -> QrCodeResult:
        if canonicalize_official_qr_url(url, self.allowed_hosts) is None:
            return QrCodeResult(
                image_index=image_index,
                raw_value=url,
                domain_valid=False,
                page_fields={},
                error_code="invalid_url",
                message="二维码网址不在允许的官网范围，请人工核验",
            )
        async with self._client() as client:
            return await self._verify_once(client, url, image_index)

    def _client(self, *, tls12_only: bool = False) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            follow_redirects=False,
            timeout=self.timeout,
            transport=self.transport,
            verify=create_official_ssl_context(tls12_only=tls12_only),
        )

    async def _verify_once(
        self,
        client: httpx.AsyncClient,
        url: str,
        image_index: int | None,
    ) -> QrCodeResult:
        canonical_url = canonicalize_official_qr_url(url, self.allowed_hosts)
        result = QrCodeResult(
            image_index=image_index,
            raw_value=url,
            url=canonical_url,
            domain_valid=canonical_url is not None,
        )
        if not result.domain_valid:
            result.page_fields = {}
            result.error_code = "invalid_url"
            result.message = "二维码网址不在允许的官网范围，请人工核验"
            return result
        try:
            async with asyncio.timeout(self.timeout_seconds):
                response = await self._fetch(client, canonical_url, result)
            result.accessible = True
            result.page_fields = extract_page_fields(response)
            result.status = "MATCH" if {"certificate_no", "vin"}.issubset(result.page_fields) else "REVIEW_REQUIRED"
            if result.status == "MATCH":
                result.message = "二维码网页字段已提取"
            else:
                result.error_code = "fields_missing"
                result.message = "官网已访问，但未提取到完整的回收证明编号和车架号，请人工核验"
        except httpx.HTTPStatusError as exc:
            result.http_status = exc.response.status_code
            result.error_code = "http_status"
            result.message = f"官网返回 HTTP {result.http_status}，未完成核验，请稍后重试或人工核验"
            result.retryable = exc.response.status_code in {429, 500, 502, 503, 504}
            result.accessible = False
        except (httpx.TimeoutException, TimeoutError):
            result.error_code = "timeout"
            result.message = "访问官网超时，未完成核验，请稍后重试或人工核验"
            result.retryable = True
            result.accessible = False
        except httpx.TransportError as exc:
            # Certificate failures are deterministic and must never be retried
            # with certificate verification disabled. Do not expose exception
            # text: HTTP libraries may include the encrypted QR token in it.
            current: BaseException | None = exc
            certificate_error = False
            for _ in range(8):
                if current is None:
                    break
                if isinstance(current, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(current):
                    certificate_error = True
                    break
                current = current.__cause__ or current.__context__
            result.error_code = "tls_certificate_error" if certificate_error else "connection_error"
            result.message = (
                "官网 TLS 证书校验失败，未完成核验，请人工核验"
                if certificate_error else "连接官网失败，未完成核验，请稍后重试或人工核验"
            )
            result.retryable = not certificate_error
            result.accessible = False
        except ValueError as exc:
            result.error_code = {
                "redirected outside allowlist": "redirect_rejected",
                "redirect without location": "redirect_rejected",
                "too many redirects": "redirect_rejected",
                "response too large": "response_too_large",
            }.get(str(exc), "response_rejected")
            result.message = "官网响应或跳转不符合核验要求，已停止访问，请人工核验"
            result.accessible = False
        except (httpx.HTTPError, RuntimeError):
            result.error_code = "request_error"
            result.message = "官网请求未完成，请人工核验"
            result.accessible = False
        return result

    async def verify_with_retry(self, url: str, image_index: int | None = None, *, retry_count: int = 1) -> tuple[QrCodeResult, list[RetryAttempt]]:
        attempts: list[RetryAttempt] = []
        if canonicalize_official_qr_url(url, self.allowed_hosts) is None:
            result = await self.verify(url, image_index)
            logger.info("review qr web completed image_index=%s result=%s error_code=invalid_url attempt=1 duration_ms=0", image_index, result.status)
            attempts.append(RetryAttempt(
                target_id=str(image_index if image_index is not None else "qr"),
                stage="qr_web", attempt_number=1, reason_code="invalid_url",
                strategy="official_page", result="failed", duration_ms=0,
            ))
            return result, attempts
        async with AsyncExitStack() as stack:
            # Each review is independent: try normal TLS first and only fall
            # back to TLS 1.2 within this review.
            tls12_only = False
            client = await stack.enter_async_context(self._client())
            for attempt_number in range(1, max(1, min(retry_count + 1, 2)) + 1):
                started = time.monotonic()
                result = await self._verify_once(client, url, image_index)
                duration_ms = max(0, int((time.monotonic() - started) * 1000))
                attempts.append(RetryAttempt(
                    target_id=str(image_index if image_index is not None else "qr"),
                    stage="qr_web", attempt_number=attempt_number,
                    reason_code=result.error_code or "completed",
                    strategy="official_page_tls12" if tls12_only else "official_page",
                    result="succeeded" if result.status == "MATCH" else "failed",
                    duration_ms=duration_ms,
                ))
                logger.info(
                    "review qr web completed image_index=%s host=%s tls=%s attempt=%d "
                    "accessible=%s result=%s error_code=%s http_status=%s retryable=%s duration_ms=%d",
                    image_index, urlparse(result.url or "").hostname, "1.2" if tls12_only else "auto", attempt_number,
                    result.accessible, result.status, result.error_code or "-",
                    result.http_status or "-", result.retryable, duration_ms,
                )
                if result.status == "MATCH" or not result.retryable:
                    return result, attempts
                if attempt_number <= min(retry_count, 1):
                    await asyncio.sleep(0.25)
                    if result.error_code == "connection_error":
                        tls12_only = True
                        client = await stack.enter_async_context(self._client(tls12_only=True))
        return result, attempts

    async def _fetch(self, client: httpx.AsyncClient, url: str, result: QrCodeResult | None = None) -> str:
        current = url
        for _ in range(self.max_redirects + 1):
            if not validate_qr_url(current, self.allowed_hosts):
                raise ValueError("redirected outside allowlist")
            async with client.stream("GET", current) as response:
                if result is not None:
                    result.http_status = response.status_code
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("redirect without location")
                    current = str(response.url.join(location))
                    continue
                response.raise_for_status()
                length = response.headers.get("content-length")
                if length and int(length) > self.max_bytes:
                    raise ValueError("response too large")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self.max_bytes:
                        raise ValueError("response too large")
                return content.decode(response.encoding or "utf-8", errors="replace")
        raise ValueError("too many redirects")
