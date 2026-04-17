"""
OCR Text Capture — Pro+ feature.
Extracts text from images and PDFs using pytesseract and pdfplumber.
Output is normalized through TypingEngine.normalize_special_chars()
so the result is always safe to type or save as a text block.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class FeatureNotAvailableError(Exception):
    """Raised when a Pro+ feature is accessed on a lower tier."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _require_pro_plus(license_info) -> None:
    """Gate: raise FeatureNotAvailableError if tier is not Pro+/Team."""
    if license_info is None:
        return  # No license object passed — caller's responsibility to gate
    tier = getattr(license_info, "tier", "solo")
    if tier not in ("pro_plus", "team"):
        raise FeatureNotAvailableError(
            "OCR Text Capture requires a Pro+ or Team license. "
            "Upgrade at typestra.com to unlock this feature."
        )


def _extract_text_from_image(path: str) -> str:
    """Run pytesseract OCR on a single image file."""
    try:
        from PIL import Image
        import pytesseract
    except ImportError as e:
        raise ImportError(
            "pytesseract and Pillow are required for image OCR. "
            "Install them with: pip install pytesseract Pillow"
        ) from e

    img = Image.open(path)
    # Use LSTM engine (OEM 3) with automatic page segmentation (PSM 3)
    custom_config = r"--oem 3 --psm 3"
    text = pytesseract.image_to_string(img, config=custom_config)
    logger.debug("OCR image result: %d chars from %r", len(text), path)
    return text


def _extract_text_from_pdf_embedded(path: str) -> Optional[str]:
    """
    Try to extract embedded (digital) text from a PDF using pdfplumber.
    Returns None if the PDF appears to be scanned (no embedded text found).
    """
    try:
        import pdfplumber
    except ImportError as e:
        raise ImportError(
            "pdfplumber is required for PDF text extraction. "
            "Install it with: pip install pdfplumber"
        ) from e

    pages_text = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                pages_text.append(page_text.strip())

    combined = "\n\n".join(pages_text).strip()
    if not combined:
        logger.debug("pdfplumber found no embedded text in %r — trying OCR fallback", path)
        return None

    logger.debug("pdfplumber extracted %d chars from %r", len(combined), path)
    return combined


def _extract_text_from_pdf_ocr(path: str) -> str:
    """
    Fallback: render each PDF page as an image and OCR it.
    Used when the PDF is scanned or image-only (no embedded text).
    """
    try:
        from pdf2image import convert_from_path
        import pytesseract
    except ImportError as e:
        raise ImportError(
            "pdf2image and pytesseract are required for scanned PDF OCR. "
            "Install them with: pip install pdf2image pytesseract"
        ) from e

    images = convert_from_path(path, dpi=300)
    pages_text = []
    for i, img in enumerate(images):
        custom_config = r"--oem 3 --psm 3"
        text = pytesseract.image_to_string(img, config=custom_config)
        pages_text.append(text.strip())
        logger.debug("OCR PDF page %d: %d chars", i + 1, len(text))

    return "\n\n".join(pages_text).strip()


# ---------------------------------------------------------------------------
# Image extensions recognized as direct OCR targets
# ---------------------------------------------------------------------------
_IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp",
    ".tiff", ".tif", ".gif",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class OCRCapture:
    """
    Extract text from images and PDFs.

    Usage:
        ocr = OCRCapture(license_info=my_license)
        text = ocr.extract("scan.pdf")
        # text is normalized and ready to type or save as a block
    """

    def __init__(self, license_info=None) -> None:
        """
        license_info: a LicenseInfo object (or None to skip license check).
        Pass the object returned by LicenseManager.get_license_info().
        """
        self._license_info = license_info

    def extract(self, file_path: str) -> str:
        """
        Extract and return clean text from an image or PDF file.

        Args:
            file_path: Absolute or relative path to a .pdf, .png, .jpg, etc.

        Returns:
            Normalized text string safe for typing or saving as a block.

        Raises:
            FeatureNotAvailableError: if license tier is not Pro+/Team.
            FileNotFoundError: if the file does not exist.
            ValueError: if the file type is unsupported.
        """
        _require_pro_plus(self._license_info)

        path = os.path.expanduser(file_path)
        if not os.path.exists(path):
            raise FileNotFoundError(f"File not found: {path!r}")

        ext = os.path.splitext(path)[1].lower()

        if ext == ".pdf":
            raw = self._extract_pdf(path)
        elif ext in _IMAGE_EXTENSIONS:
            raw = _extract_text_from_image(path)
        else:
            raise ValueError(
                f"Unsupported file type: {ext!r}. "
                f"Supported: PDF, {', '.join(sorted(_IMAGE_EXTENSIONS))}"
            )

        return self._normalize(raw)

    def _extract_pdf(self, path: str) -> str:
        """Try embedded text first; fall back to page-image OCR."""
        embedded = _extract_text_from_pdf_embedded(path)
        if embedded:
            return embedded
        logger.info("No embedded text in %r — running OCR on rendered pages", path)
        return _extract_text_from_pdf_ocr(path)

    @staticmethod
    def _normalize(text: str) -> str:
        """
        Run the extracted text through TypingEngine's normalizer so the
        output is free of smart quotes, em-dashes, zero-width chars, etc.
        Falls back to a lightweight inline version if the engine is unavailable.
        """
        try:
            from autoflow_engine.typing_engine import TypingEngine
            return TypingEngine.normalize_special_chars(text)
        except ImportError:
            pass

        # Inline fallback normalization (subset of TypingEngine's version)
        replacements = {
            "\u201c": '"', "\u201d": '"',
            "\u2018": "'", "\u2019": "'",
            "\u2014": "-", "\u2013": "-",
            "\u2026": "...",
            "\u00a0": " ",
            "\u200b": "", "\u200c": "", "\u200d": "", "\ufeff": "",
        }
        for src, dst in replacements.items():
            text = text.replace(src, dst)
        return text.strip()
