from pathlib import Path

import pytest

from ordenia.core.classifier import ExtensionClassifier


@pytest.mark.parametrize(("name", "expected"), [
    ("reporte.PDF", "Documentos"),
    ("inventario.xlsx", "Hojas de cálculo"),
    ("demo.pptx", "Presentaciones"),
    ("foto.JPEG", "Imágenes"),
    ("clip.mp4", "Videos"),
    ("audio.flac", "Audio"),
    ("datos.tar", "Comprimidos"),
    ("control.ino", "Código"),
    ("setup.msi", "Instaladores"),
    ("desconocido.xyz", "Otros"),
])
def test_classification(name: str, expected: str) -> None:
    assert ExtensionClassifier().classify(Path(name)) == expected
