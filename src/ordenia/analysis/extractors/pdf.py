"""Page-wise PDF text and document metadata via PyMuPDF."""

from pathlib import Path

import pymupdf

from ordenia.analysis.models import AnalysisLimits, ExtractedContent


class PdfExtractor:
    def extract(self, path: Path, limits: AnalysisLimits) -> ExtractedContent:
        with pymupdf.open(path) as document:
            if document.needs_pass:
                raise ValueError("PDF cifrado: se requiere una contraseña.")
            metadata = document.metadata or {}
            pieces: list[str] = []
            remaining = limits.max_extracted_characters
            for index in range(min(document.page_count, limits.max_pdf_pages)):
                if remaining <= 0:
                    break
                text = document.load_page(index).get_text("text")
                pieces.append(text[:remaining])
                remaining -= len(pieces[-1])
            return ExtractedContent(
                "\n".join(pieces), "PDF", str(metadata.get("title") or ""),
                str(metadata.get("author") or ""), str(metadata.get("subject") or ""),
                page_count=document.page_count,
                truncated=document.page_count > limits.max_pdf_pages or remaining <= 0,
            )
