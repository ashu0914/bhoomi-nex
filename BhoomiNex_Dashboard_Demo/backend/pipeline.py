"""BHOOMI-AI document pipeline.

Mirrors the 7-stage flow from the pitch deck:

    1. INPUT              -> the uploaded file
    2. PREPROCESSING       -> preprocess_image()
    3. AI UNDERSTANDING    -> ocr_document()  (real Tesseract OCR)
    4. VALIDATION ENGINE   -> validate_fields()
    5. CONFIDENCE ENGINE   -> field + overall confidence scoring
    6. HUMAN VERIFICATION  -> decide_status() flags low-confidence / conflicting
                               records as "Needs Review"; officers finalize
                               them via PUT /api/records/{id}/verify
    7. OUTPUT              -> a structured dict ready to persist as a Record

This is a genuine (not faked) extraction pipeline built on open tooling
(Tesseract OCR + regex/keyword field extraction), sized for a hackathon
prototype. It is intentionally rule-based rather than a trained NLP model
so it runs with no GPU and no external API calls — swap `extract_fields`
for a trained NER model later without touching the rest of the pipeline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pytesseract
from PIL import Image, ImageFilter, ImageOps
from pypdf import PdfReader
from sqlalchemy.orm import Session

from config import settings
import models

if settings.TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}
PDF_EXTENSIONS = {".pdf"}

REQUIRED_FIELDS = ["owner", "khasra", "village", "district"]

FIELD_LABELS = {
    "owner": ["owner name", "owner's name", "name of owner", "owner", "मालिक", "स्वामी का नाम"],
    "khasra": ["khasra no", "khasra number", "khasra", "survey no", "survey number", "सर्वे नंबर", "खसरा नंबर", "खसरा"],
    "khata": ["khata no", "khata number", "khata", "खाता नंबर", "खाता"],
    "village": ["village", "gram", "गांव", "ग्राम"],
    "tehsil": ["tehsil", "taluka", "taluk", "तहसील"],
    "district": ["district", "zila", "जिला"],
    "area": ["area", "land area", "क्षेत्रफल", "रकबा"],
    "mutation_no": ["mutation no", "mutation number", "mutation", "म्यूटेशन नंबर", "म्यूटेशन"],
}

# Value patterns per field, applied to the text captured after a matched label.
VALUE_PATTERNS = {
    "khasra": re.compile(r"[0-9]+(?:[\/\-][0-9]+)*"),
    "khata": re.compile(r"[0-9]+(?:[\/\-][0-9]+)*"),
    "mutation_no": re.compile(r"[0-9]+(?:[\/\-][0-9]+)*"),
    "area": re.compile(r"[0-9]+(?:[.,][0-9]+)?\s*(?:acres?|hectares?|ha|sq\.?\s?m|sqm|marla|बीघा|कनाल)?", re.IGNORECASE),
    "owner": re.compile(r"[A-Za-z\u0900-\u097F][A-Za-z\u0900-\u097F.\s]{2,59}"),
    "village": re.compile(r"[A-Za-z\u0900-\u097F][A-Za-z\u0900-\u097F.\s]{1,49}"),
    "tehsil": re.compile(r"[A-Za-z\u0900-\u097F][A-Za-z\u0900-\u097F.\s]{1,49}"),
    "district": re.compile(r"[A-Za-z\u0900-\u097F][A-Za-z\u0900-\u097F.\s]{1,49}"),
}

STOPWORDS_AFTER_VALUE = re.compile(
    r"\b(khasra|khata|village|tehsil|district|area|owner|mutation|survey)\b", re.IGNORECASE
)


@dataclass
class ExtractedField:
    value: Optional[str] = None
    matched_label: bool = False
    confidence: int = 0


@dataclass
class PipelineResult:
    fields: dict[str, ExtractedField] = field(default_factory=dict)
    overall_confidence: int = 0
    status: str = "Needs Review"
    validation_notes: list[str] = field(default_factory=list)
    raw_text: str = ""
    ocr_confidence: int = 0
    quality_score: int = 0


# ---------------------------------------------------------------------------
# 2. PREPROCESSING
# ---------------------------------------------------------------------------

def _sharpness_score(image: Image.Image) -> int:
    """Cheap blur/quality estimate without OpenCV: variance of an edge map."""
    edges = image.convert("L").filter(ImageFilter.FIND_EDGES)
    arr = np.asarray(edges, dtype=np.float32)
    variance = float(arr.var())
    # Empirically map variance to a 0-100 "document quality" score.
    return int(max(0, min(100, variance / 12)))


def preprocess_image(image: Image.Image) -> tuple[Image.Image, int]:
    """Deskew is skipped (no OpenCV dependency) but we denoise/normalize and
    score document quality so low-quality scans get lower confidence."""
    quality = _sharpness_score(image)
    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    if gray.width < 1000:  # upscale small scans, Tesseract likes >=300dpi-ish
        ratio = 1000 / gray.width
        gray = gray.resize((int(gray.width * ratio), int(gray.height * ratio)), Image.LANCZOS)
    return gray, quality


def _load_images(path: Path) -> list[Image.Image]:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return [Image.open(path)]
    if suffix in PDF_EXTENSIONS:
        try:
            from pdf2image import convert_from_path
            kwargs = {"poppler_path": settings.POPPLER_PATH} if settings.POPPLER_PATH else {}
            return convert_from_path(str(path), dpi=300, **kwargs)
        except Exception:
            # poppler not installed — caller falls back to the embedded text layer.
            return []
    raise ValueError(f"Unsupported file type: {suffix}")


# ---------------------------------------------------------------------------
# 3. AI UNDERSTANDING (OCR)
# ---------------------------------------------------------------------------

def _pdf_text_layer(path: Path) -> str:
    try:
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        return ""


def ocr_document(path: Path) -> tuple[str, int, int]:
    """Returns (raw_text, ocr_confidence 0-100, quality_score 0-100)."""
    suffix = path.suffix.lower()

    # Text-based PDFs: trust the embedded text layer over OCR when it's substantial.
    if suffix in PDF_EXTENSIONS:
        embedded = _pdf_text_layer(path)
        if len(embedded.strip()) > 40:
            return embedded, 97, 100  # a real text layer is effectively ground truth

    images = _load_images(path)
    if not images:
        return "", 0, 0

    texts: list[str] = []
    confidences: list[float] = []
    quality_scores: list[int] = []

    for img in images:
        processed, quality = preprocess_image(img)
        quality_scores.append(quality)
        try:
            data = pytesseract.image_to_data(
                processed, lang=settings.OCR_LANGUAGES, output_type=pytesseract.Output.DICT,
            )
        except pytesseract.TesseractError:
            # Requested language pack (e.g. "hin") isn't installed on this
            # machine — fall back to English-only rather than failing the
            # whole upload. See README_BACKEND.md for installing language data.
            data = pytesseract.image_to_data(processed, lang="eng", output_type=pytesseract.Output.DICT)
        words = []
        for word, conf in zip(data["text"], data["conf"]):
            conf = float(conf)
            if word.strip() and conf >= 0:
                words.append(word)
                confidences.append(conf)
        texts.append(" ".join(words))

    raw_text = "\n".join(texts)
    ocr_confidence = int(sum(confidences) / len(confidences)) if confidences else 0
    quality_score = int(sum(quality_scores) / len(quality_scores)) if quality_scores else 0
    return raw_text, ocr_confidence, quality_score


# ---------------------------------------------------------------------------
# EXTRACTION
# ---------------------------------------------------------------------------

def _clean_value(field_name: str, raw: str) -> str:
    raw = raw.strip(" \t:-–—.")
    raw = STOPWORDS_AFTER_VALUE.split(raw)[0].strip(" \t:-–—.,")
    if field_name in ("owner", "village", "tehsil", "district"):
        raw = " ".join(w.capitalize() if w.isascii() else w for w in raw.split())
    return raw[:60].strip()


def extract_fields(text: str) -> dict[str, ExtractedField]:
    """Rule-based field extraction: find a known label, then pull the value
    pattern that follows it on the same line. Falls back to 'not found'."""
    results: dict[str, ExtractedField] = {}
    lines = [l for l in re.split(r"[\r\n]+", text) if l.strip()]

    for field_name, labels in FIELD_LABELS.items():
        found = ExtractedField()
        pattern = VALUE_PATTERNS.get(field_name)
        for line in lines:
            lower = line.lower()
            for label in labels:
                idx = lower.find(label.lower())
                if idx == -1:
                    continue
                after = line[idx + len(label):]
                after = after.lstrip(" \t:-–—")
                match = pattern.search(after) if pattern else None
                value = match.group(0) if match else after.strip()[:60]
                value = _clean_value(field_name, value)
                if value:
                    found = ExtractedField(value=value, matched_label=True)
                    break
            if found.value:
                break
        results[field_name] = found
    return results


# ---------------------------------------------------------------------------
# 5. CONFIDENCE ENGINE
# ---------------------------------------------------------------------------

def score_confidence(fields: dict[str, ExtractedField], ocr_confidence: int, quality_score: int) -> int:
    base = int(0.7 * ocr_confidence + 0.3 * quality_score)
    weighted_total = 0
    weight_sum = 0
    for name, extracted in fields.items():
        weight = 2 if name in REQUIRED_FIELDS else 1
        if extracted.value and extracted.matched_label:
            extracted.confidence = max(0, min(99, base + 6))
        elif extracted.value:
            extracted.confidence = max(0, min(90, base - 15))
        else:
            extracted.confidence = 0
        weighted_total += extracted.confidence * weight
        weight_sum += weight
    return int(weighted_total / weight_sum) if weight_sum else 0


# ---------------------------------------------------------------------------
# 4. VALIDATION ENGINE
# ---------------------------------------------------------------------------

def validate_fields(fields: dict[str, ExtractedField], db: Session, exclude_id: str | None = None) -> tuple[list[str], bool]:
    notes: list[str] = []
    blocking = False

    for req in REQUIRED_FIELDS:
        if not fields.get(req) or not fields[req].value:
            notes.append(f"Required field '{req.replace('_', ' ').title()}' could not be extracted — please enter it manually.")
            blocking = True

    khasra = fields.get("khasra")
    if khasra and khasra.value and not re.fullmatch(r"[0-9]+(?:[\/\-][0-9]+)*", khasra.value):
        notes.append("Khasra number format looks unusual — please double-check it.")

    area = fields.get("area")
    if area and area.value and not re.search(r"[0-9]", area.value):
        notes.append("Area value has no numeric quantity — please verify.")

    khasra_val = khasra.value if khasra else None
    village = fields.get("village")
    village_val = village.value if village else None
    if khasra_val and village_val:
        query = db.query(models.Record).filter(
            models.Record.khasra == khasra_val,
            models.Record.village == village_val,
        )
        if exclude_id:
            query = query.filter(models.Record.id != exclude_id)
        existing = query.first()
        if existing:
            owner = fields.get("owner")
            if owner and owner.value and existing.owner and existing.owner.lower() != owner.value.lower():
                notes.append(
                    f"Possible conflicting record: khasra {khasra_val} in {village_val} already exists "
                    f"as {existing.id} under a different owner ({existing.owner}). Needs officer review."
                )
                blocking = True
            else:
                notes.append(f"A similar record already exists ({existing.id}) — check for duplicate upload.")

    return notes, blocking


# ---------------------------------------------------------------------------
# 6. HUMAN VERIFICATION DECISION
# ---------------------------------------------------------------------------

def decide_status(overall_confidence: int, has_blocking_issue: bool) -> str:
    if has_blocking_issue:
        return "Needs Review"
    if overall_confidence >= 90:
        return "Verified"
    if overall_confidence >= 70:
        return "Processed"
    return "Needs Review"


# ---------------------------------------------------------------------------
# 1 + 7. ENTRY POINT
# ---------------------------------------------------------------------------

def run_pipeline(path: Path, db: Session) -> PipelineResult:
    raw_text, ocr_confidence, quality_score = ocr_document(path)
    fields = extract_fields(raw_text)
    overall_confidence = score_confidence(fields, ocr_confidence, quality_score)
    notes, blocking = validate_fields(fields, db)
    status = decide_status(overall_confidence, blocking)

    return PipelineResult(
        fields=fields,
        overall_confidence=overall_confidence,
        status=status,
        validation_notes=notes,
        raw_text=raw_text,
        ocr_confidence=ocr_confidence,
        quality_score=quality_score,
    )
