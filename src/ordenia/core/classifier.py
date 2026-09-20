"""Clasificación intercambiable basada en extensiones."""

from pathlib import Path
from typing import Protocol


class Classifier(Protocol):
    def classify(self, path: Path) -> str: ...


class ExtensionClassifier:
    CATEGORIES: dict[str, frozenset[str]] = {
        "Documentos": frozenset({".pdf", ".doc", ".docx", ".txt", ".md", ".log", ".odt"}),
        "Hojas de cálculo": frozenset({".xls", ".xlsx", ".csv"}),
        "Presentaciones": frozenset({".ppt", ".pptx"}),
        "Imágenes": frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}),
        "Videos": frozenset({".mp4", ".mkv", ".avi", ".mov", ".webm"}),
        "Audio": frozenset({".mp3", ".wav", ".flac", ".m4a"}),
        "Comprimidos": frozenset({".zip", ".rar", ".7z", ".tar", ".gz"}),
        "Código": frozenset({".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".cpp", ".c", ".h", ".hpp", ".ino", ".html", ".css", ".json", ".yaml", ".yml", ".toml", ".sql", ".sh", ".ps1", ".bat", ".xml"}),
        "Instaladores": frozenset({".exe", ".msi"}),
    }

    def classify(self, path: Path) -> str:
        suffix = path.suffix.lower()
        for category, extensions in self.CATEGORIES.items():
            if suffix in extensions:
                return category
        return "Otros"
