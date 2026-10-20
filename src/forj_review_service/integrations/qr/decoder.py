"""读取二维码图片内容的受控本地链路。

二维码是官网核验的输入，不是普通材料识别的替代品。该模块只在需要官网
核验的候选材料上工作：图片 URL 在内存中读取，ZXing-C++ 优先，OpenCV
QRCodeDetector 兜底，既不写盘也不把图片上传给其他服务。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import urlparse

import httpx

from forj_review_service.integrations.qr.image_transport import ImageTransport


@dataclass(frozen=True)
class QrDecodeResult:
    """一次二维码读取的可审计结果。"""

    values: tuple[str, ...] = ()
    decoder: str = "none"
    error_code: str | None = None
    elapsed_ms: int = 0


class QrImageDecoder:
    """从单个图片 URL 读取二维码，且每张图片最多执行一次。"""

    def __init__(
        self,
        *,
        timeout_seconds: float = 4.0,
        max_bytes: int = 8_000_000,
        max_redirects: int = 2,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
        private_hosts: tuple[str, ...] = (),
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.transport = transport
        self.private_hosts = private_hosts

    async def decode(self, image_url: str | None) -> QrDecodeResult:
        started = time.monotonic()
        if not self._is_http_url(image_url):
            return self._result(started, error_code="invalid_url")
        try:
            content = await self._read_image(image_url or "")
        except TimeoutError:
            return self._result(started, error_code="download_timeout")
        except httpx.HTTPStatusError:
            return self._result(started, error_code="http_status")
        except httpx.HTTPError:
            return self._result(started, error_code="download_error")
        except ValueError:
            return self._result(started, error_code="image_rejected")
        except (OSError, RuntimeError, TypeError):  # pragma: no cover - transport boundary
            return self._result(started, error_code="download_error")

        try:
            values = await asyncio.to_thread(self._decode_bytes, content)
        except (ImportError, OSError, RuntimeError, TypeError, ValueError):  # pragma: no cover - native boundary
            return self._result(started, error_code="decoder_error")
        if values:
            return self._result(started, values=values[0], decoder=values[1])
        return self._result(started, error_code="decode_empty")

    async def _read_image(self, url: str) -> bytes:
        current = url
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(self.timeout_seconds),
            transport=self.transport or ImageTransport(private_hosts=self.private_hosts),
            trust_env=False,
        ) as client:
            for _ in range(self.max_redirects + 1):
                if not self._is_http_url(current):
                    raise ValueError("图片重定向到非 HTTP(S) 地址")
                async with asyncio.timeout(self.timeout_seconds):
                    async with client.stream("GET", current) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise ValueError("图片重定向缺少 location")
                            current = str(response.url.join(location))
                            continue
                        response.raise_for_status()
                        content_length = response.headers.get("content-length")
                        if content_length and int(content_length) > self.max_bytes:
                            raise ValueError("二维码图片超过大小限制")
                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            content.extend(chunk)
                            if len(content) > self.max_bytes:
                                raise ValueError("二维码图片超过大小限制")
                        if not content:
                            raise ValueError("二维码图片为空")
                        return bytes(content)
        raise ValueError("二维码图片重定向次数过多")

    @staticmethod
    def _is_http_url(url: str | None) -> bool:
        if not url:
            return False
        try:
            parsed = urlparse(url)
            _ = parsed.port
        except ValueError:
            return False
        return (parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                and parsed.username is None and parsed.password is None)

    @staticmethod
    def _decode_bytes(content: bytes) -> tuple[tuple[str, ...], str] | None:
        """ZXing 优先；OpenCV 仅在 ZXing 没有结果时兜底。"""
        import cv2
        import numpy as np
        from PIL import Image, UnidentifiedImageError

        # 压缩字节数不能限制解压后的内存；必须在原生解码分配像素前检查尺寸。
        try:
            with Image.open(BytesIO(content)) as header:
                if header.width * header.height > 20_000_000:
                    raise ValueError("二维码图片像素超过限制")
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
            raise ValueError("二维码图片格式或尺寸无效") from exc
        try:
            image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
        except cv2.error as exc:
            raise ValueError("二维码图片无法解码") from exc
        if image is None:
            return None

        try:
            import zxingcpp

            zxing_values = tuple(
                value
                for value in (
                    str(item.text).strip()
                    for item in zxingcpp.read_barcodes(image)
                    if getattr(item, "text", None)
                )
                if value
            )
        except (ImportError, OSError, RuntimeError, TypeError, ValueError):
            # ZXing 原生库不可用或单张图片触发其异常时，仍必须进入
            # OpenCV 兜底，不能把“主解码器异常”直接变成整项失败。
            zxing_values = ()
        if zxing_values:
            return tuple(dict.fromkeys(zxing_values)), "zxing"

        detector = cv2.QRCodeDetector()
        values: list[str] = []
        try:
            detected, decoded_info, _points, _straight = detector.detectAndDecodeMulti(image)
            if detected:
                values.extend(str(value).strip() for value in decoded_info if str(value).strip())
        except (cv2.error, ValueError):
            pass
        if not values:
            try:
                value, _points, _straight = detector.detectAndDecode(image)
                if value and value.strip():
                    values.append(value.strip())
            except (cv2.error, ValueError):
                pass
        return (tuple(dict.fromkeys(values)), "opencv") if values else None

    def _result(
        self,
        started: float,
        *,
        values: tuple[str, ...] = (),
        decoder: str = "none",
        error_code: str | None = None,
    ) -> QrDecodeResult:
        return QrDecodeResult(
            values=values,
            decoder=decoder,
            error_code=error_code,
            elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
        )
