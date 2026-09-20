"""Bounded format dispatch, errors and local keyword extraction."""

import logging
import zipfile
from pathlib import Path

from .models import AnalysisLimits, AnalysisOutcome
from .registry import LEGACY_EXTENSIONS, ExtractorRegistry
from .text_utils import keywords

logger = logging.getLogger(__name__)


class ContentAnalyzer:
    def __init__(self, limits: AnalysisLimits | None = None, registry: ExtractorRegistry | None = None) -> None:
        self.limits = limits or AnalysisLimits()
        self.registry = registry or ExtractorRegistry()

    def analyze(self, path: Path) -> AnalysisOutcome:
        extractor = self.registry.get(path)
        if extractor is None:
            reason = ("No compatible con análisis de contenido en V0.3" if path.suffix.casefold() in LEGACY_EXTENSIONS
                      else "Formato no compatible con análisis de contenido en V0.3")
            return AnalysisOutcome("unsupported", error=reason)
        try:
            if path.is_symlink() or not path.is_file():
                raise FileNotFoundError("El archivo ya no está disponible para analizar.")
            if path.stat().st_size > self.limits.max_file_bytes:
                return AnalysisOutcome("skipped", error="Archivo demasiado grande para análisis de contenido.")
            if path.suffix.casefold() in {".docx", ".xlsx", ".pptx"}:
                if path.stat().st_size > self.limits.max_office_file_bytes:
                    return AnalysisOutcome("skipped", error="Documento Office demasiado grande para análisis de contenido.")
                with zipfile.ZipFile(path) as archive:
                    members = archive.infolist()
                    if len(members) > self.limits.max_zip_members or sum(info.file_size for info in members) > self.limits.max_zip_uncompressed_bytes:
                        return AnalysisOutcome("skipped", error="Documento comprimido demasiado grande para análisis de contenido.")
            extracted = extractor.extract(path, self.limits)
            text = extracted.text.strip()
            return AnalysisOutcome(
                "indexed" if text else "no_text", extracted.extractor, text,
                extracted.title, extracted.author, extracted.subject,
                extracted.page_count, extracted.slide_count,
                keywords(text, self.limits.max_keywords) if text else (),
                "PDF sin texto extraíble" if not text and path.suffix.casefold() == ".pdf" else "",
                extracted.truncated,
            )
        except Exception as exc:
            logger.exception("No se pudo extraer contenido de %s", path)
            return AnalysisOutcome("failed", error=f"No se pudo analizar el archivo: {exc}")
