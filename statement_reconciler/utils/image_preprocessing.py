# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Image preprocessing utilities for OCR.

Designed primarily for flatbed-scanned statements (minimal perspective distortion),
but generally improves OCR robustness on most scans/photos.
"""

from __future__ import annotations

import logging
from typing import Optional

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

logger = logging.getLogger(__name__)


def _require_deps() -> None:
    if cv2 is None or np is None:  # pragma: no cover
        raise ImportError("opencv-python and numpy are required for image preprocessing")


def to_grayscale(image: "np.ndarray") -> "np.ndarray":
    """Convert BGR/RGB image to grayscale (uint8)."""
    _require_deps()

    if image is None:
        raise ValueError("image is None")

    if len(image.shape) == 2:
        return image
    if len(image.shape) == 3 and image.shape[2] == 1:
        return image[:, :, 0]
    if len(image.shape) == 3 and image.shape[2] >= 3:
        # Assume BGR (OpenCV convention)
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    raise ValueError(f"Unsupported image shape: {image.shape}")


def _estimate_skew_angle(binary_inverted: "np.ndarray") -> float:
    """Estimate skew angle in degrees using minAreaRect over foreground pixels."""
    _require_deps()

    coords = np.column_stack(np.where(binary_inverted > 0))
    if coords.size < 2000:
        return 0.0

    rect = cv2.minAreaRect(coords)
    angle = float(rect[-1])

    # OpenCV returns angle in [-90, 0)
    if angle < -45.0:
        angle = -(90.0 + angle)
    else:
        angle = -angle

    return angle


def _rotate_bound(image: "np.ndarray", angle_deg: float, border_value: int = 255) -> "np.ndarray":
    """Rotate image while keeping full content in frame."""
    _require_deps()

    (h, w) = image.shape[:2]
    center = (w / 2.0, h / 2.0)

    m = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
    cos = abs(m[0, 0])
    sin = abs(m[0, 1])

    nW = int((h * sin) + (w * cos))
    nH = int((h * cos) + (w * sin))

    m[0, 2] += (nW / 2.0) - center[0]
    m[1, 2] += (nH / 2.0) - center[1]

    return cv2.warpAffine(
        image,
        m,
        (nW, nH),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=border_value,
    )


def preprocess_for_ocr(
    image_bgr: "np.ndarray",
    *,
    deskew: bool = True,
    denoise: bool = True,
    use_clahe: bool = True,
) -> "np.ndarray":
    """
    Preprocess an image for Tesseract OCR.

    Returns a binarized (black text on white) uint8 image.
    """
    _require_deps()

    gray = to_grayscale(image_bgr)

    # Denoise lightly (flatbed scans usually don't need heavy denoise).
    work = gray
    if denoise:
        try:
            work = cv2.fastNlMeansDenoising(work, None, h=10, templateWindowSize=7, searchWindowSize=21)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Denoise failed: %s", exc)

    # Contrast normalization
    if use_clahe:
        try:
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            work = clahe.apply(work)
        except Exception as exc:  # noqa: BLE001
            logger.debug("CLAHE failed: %s", exc)

    # Deskew (estimate on a downscaled, binarized version for speed/stability)
    if deskew:
        try:
            scale = 1.0
            if work.shape[1] > 1800:
                scale = 1800.0 / float(work.shape[1])
            if scale < 1.0:
                small = cv2.resize(work, (int(work.shape[1] * scale), int(work.shape[0] * scale)))
            else:
                small = work

            _, small_bin = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            small_inv = cv2.bitwise_not(small_bin)
            angle = _estimate_skew_angle(small_inv)

            if abs(angle) >= 0.5:
                work = _rotate_bound(work, angle, border_value=255)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Deskew failed: %s", exc)

    # Final binarization (Otsu)
    blurred = cv2.GaussianBlur(work, (3, 3), 0)
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    return binary
