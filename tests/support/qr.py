"""Synthetic images for exercising the real QR decoder without external requests."""

import cv2
import httpx
import numpy as np
import zxingcpp

from forj_review_service.integrations.qr.decoder import QrImageDecoder


def qr_image_decoder(values: dict[str, str]) -> QrImageDecoder:
    def handler(request: httpx.Request) -> httpx.Response:
        value = values.get(str(request.url))
        image = (
            np.asarray(zxingcpp.write_barcode(zxingcpp.BarcodeFormat.QRCode, value, 300, 300))
            if value is not None
            else np.full((100, 100), 255, dtype=np.uint8)
        )
        ok, encoded = cv2.imencode(".png", image)
        assert ok
        return httpx.Response(200, content=bytes(encoded))

    return QrImageDecoder(transport=httpx.MockTransport(handler))
