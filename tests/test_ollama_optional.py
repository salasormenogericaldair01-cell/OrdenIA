"""Opt-in integration test: ORDENIA_RUN_OLLAMA_TESTS=1 pytest -m ollama."""

import os

import pytest

from ordenia.ai.providers.ollama import OllamaProvider
from ordenia.ai.models import AIContext, DEFAULT_AI_MODEL
from ordenia.ai.parser import parse_suggestion
from ordenia.ai.prompts import SYSTEM_PROMPT, user_prompt


@pytest.mark.ollama
def test_local_ollama_service_opt_in() -> None:
    if os.environ.get("ORDENIA_RUN_OLLAMA_TESTS") != "1":
        pytest.skip("Activa ORDENIA_RUN_OLLAMA_TESTS=1 para probar el Ollama local.")
    status = OllamaProvider().check()
    assert status.available, status.message
    assert DEFAULT_AI_MODEL in status.models, f"Instala con: ollama pull {DEFAULT_AI_MODEL}"
    context = AIContext("curso.pdf", ".pdf", "Documentos", "Curso de bases de datos", {},
                        ("SQL", "normalización"),
                        ("Material académico sobre SQL, normalización y modelo relacional.",),
                        ("Estudios/Base de Datos",), ())
    response = OllamaProvider().generate(SYSTEM_PROMPT, user_prompt(context), DEFAULT_AI_MODEL)
    suggestion = parse_suggestion(response.content)
    assert suggestion.suggested_path
    assert response.eval_count <= OllamaProvider.MAX_PREDICT_TOKENS
