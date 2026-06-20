"""
text_extract.py (v2)

Shared document-to-text extraction for the CLM pipeline, used by both
process_contract.py and process_amendment.py.

Strategy (primary first, OCR only as a fallback):

    PDF   -> PyMuPDF text layer.  If the text layer is empty/thin (a scanned
             PDF), render each page to an image and OCR it with Tesseract.
    DOCX  -> python-docx.
    Image -> OCR directly (png/jpg/tiff/bmp).
    other -> UTF-8 decode.

Compliance note: Tesseract is Apache-2.0 and runs entirely locally, so scanned
contract images never leave the tenant — keep it that way by running this
module inside your in-tenant Azure Function/container, never a hosted OCR API.

Optional dependencies (import lazily; clear error if missing):
    pip install pymupdf python-docx pytesseract pillow
    plus the Tesseract binary:  apt-get install -y tesseract-ocr
"""
from __future__ import annotations

import io
import logging

log = logging.getLogger(__name__)

# Below this many characters, a PDF "text layer" is treated as effectively empty
# (i.e. a scanned document) and we fall back to OCR.
_MIN_TEXT_CHARS = 20
# Render scanned pages at this DPI before OCR. Higher = more accurate, slower.
_OCR_DPI = 300

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")


def extract_text(filename: str, data: bytes, *, ocr_fallback: bool = True) -> str:
    """Return plain text for a document, using OCR only when the primary path fails."""
    lower = filename.lower()

    if lower.endswith(".pdf"):
        text = _pdf_text_layer(data)
        if ocr_fallback and len(text.strip()) < _MIN_TEXT_CHARS:
            log.info("PDF '%s' has a thin text layer (%d chars); falling back to OCR.",
                     filename, len(text.strip()))
            text = _ocr_pdf(data)
        return text

    if lower.endswith(".docx"):
        return _docx_text(data)

    if lower.endswith(_IMAGE_EXTS):
        return _ocr_image(data)

    return data.decode("utf-8", errors="ignore")


# --------------------------------------------------------------------------- #
# Primary extractors
# --------------------------------------------------------------------------- #
def _pdf_text_layer(data: bytes) -> str:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise RuntimeError("Install PyMuPDF (pip install pymupdf) for PDF support.") from exc
    with fitz.open(stream=data, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


def _docx_text(data: bytes) -> str:
    try:
        import docx  # python-docx
    except ImportError as exc:
        raise RuntimeError("Install python-docx for DOCX support.") from exc
    document = docx.Document(io.BytesIO(data))
    return "\n".join(p.text for p in document.paragraphs)


# --------------------------------------------------------------------------- #
# OCR fallback (local Tesseract)
# --------------------------------------------------------------------------- #
def _ocr_engine():
    """Import the OCR stack lazily and verify the Tesseract binary is present."""
    try:
        import pytesseract
        from PIL import Image  # noqa: F401  (used by callers)
    except ImportError as exc:
        raise RuntimeError(
            "OCR fallback needs pytesseract + Pillow (pip install pytesseract pillow)."
        ) from exc
    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:  # pytesseract.TesseractNotFoundError and friends
        raise RuntimeError(
            "Tesseract binary not found. Install it locally, in-tenant "
            "(e.g. apt-get install -y tesseract-ocr); do not route to a hosted OCR API."
        ) from exc
    return pytesseract


def _ocr_pdf(data: bytes) -> str:
    import fitz  # already validated by _pdf_text_layer in the normal path
    from PIL import Image
    pytesseract = _ocr_engine()

    pages: list[str] = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=_OCR_DPI)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            pages.append(pytesseract.image_to_string(img))
    return "\n".join(pages)


def _ocr_image(data: bytes) -> str:
    from PIL import Image
    pytesseract = _ocr_engine()
    img = Image.open(io.BytesIO(data))
    return pytesseract.image_to_string(img)
