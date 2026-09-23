"""Deterministic and optional local-AI intent parsing."""

import json
from datetime import date

from ordenia.ai.models import AIResponse, ProviderStatus
from ordenia.ai.providers.base import AIProvider, ProviderUnavailable
from ordenia.automation.intent_models import IntentState, RecencyMode
from ordenia.automation.intent_parser import INTENT_JSON_SCHEMA, IntentParser


def _payload(**changes: object) -> str:
    data: dict[str, object] = {
        "search_terms": ["redes empresariales"], "topic": "Redes",
        "extensions": ["pdf"], "categories": ["Documentos"],
        "source_hints": [], "date_reference": "any", "quantity_hint": 3,
        "destination_hints": ["Estudios/Redes"], "grouping_hints": ["Segunda práctica"],
        "user_context": ["Material del curso"], "explicit_exclusions": [],
        "clarification_required": False, "clarification_reason": "",
    }
    data.update(changes)
    return json.dumps(data, ensure_ascii=False)


class IntentProvider(AIProvider):
    name = "ollama"

    def __init__(self, result: str | Exception = _payload()) -> None:
        self.result = result
        self.calls = 0
        self.schema: dict[str, object] | None = None
        self.user_prompt = ""

    def check(self) -> ProviderStatus:
        return ProviderStatus(self.name, True)

    def generate(self, system_prompt: str, user_prompt: str, model: str) -> AIResponse:
        return self.generate_structured(system_prompt, user_prompt, model, {})

    def generate_structured(self, system_prompt: str, user_prompt: str, model: str,
                            schema: dict[str, object]) -> AIResponse:
        self.calls += 1
        self.schema = schema
        self.user_prompt = user_prompt
        if isinstance(self.result, Exception):
            raise self.result
        return AIResponse(self.result)


def test_deterministic_rules_parse_today_quantity_pdf_topic_destination_and_exclusion() -> None:
    parser = IntentParser(today=lambda: date(2026, 9, 21))
    result = parser.parse(
        "Los cinco PDF de hoy son de Base de Datos. Ponlos con Renato. No toques mis proyectos.",
        known_destinations=("Renato/Base de Datos",), vocabulary=("Base de Datos",),
    )
    intent = result.intent
    assert intent.topic == "Base de Datos"
    assert intent.quantity_hint == 5
    assert intent.extensions == ("pdf",)
    assert intent.detected_from == intent.detected_to == date(2026, 9, 21)
    assert intent.recency is RecencyMode.TODAY
    assert intent.destination_hints == ("Renato",)
    assert intent.explicit_exclusions == ("proyectos",)
    assert result.inference_count == 0


def test_yesterday_latest_three_and_grouping_are_structured() -> None:
    parser = IntentParser(today=lambda: date(2026, 9, 21))
    yesterday = parser.parse("Organiza los tres archivos de ayer de Redes.", vocabulary=("Redes",)).intent
    latest = parser.parse("Pon los últimos cinco PDF con Renato.").intent
    practice = parser.parse("Estos tres archivos son de mi segunda práctica de Redes.", vocabulary=("Redes",)).intent
    assert yesterday.detected_from == yesterday.detected_to == date(2026, 9, 20)
    assert yesterday.quantity_hint == 3
    assert latest.recency is RecencyMode.LATEST and latest.quantity_hint == 5
    assert practice.grouping_hints == ("Segunda práctica",)


def test_broad_request_requires_clarification_without_calling_ai() -> None:
    provider = IntentProvider()
    result = IntentParser(provider).parse("Organiza mis cosas.")
    assert result.intent.clarification_required
    assert "Descargas" in result.intent.clarification_reason
    assert provider.calls == 0


def test_semantic_ambiguity_uses_one_strict_schema_inference() -> None:
    provider = IntentProvider()
    result = IntentParser(provider, today=lambda: date(2026, 9, 21)).parse(
        "Clasifica estos documentos del curso que vimos para el examen en el lugar habitual."
    )
    assert result.intent.state is IntentState.RESOLVED
    assert result.intent.topic == "Redes"
    assert result.inference_count == 1 and result.sent_to_ai == 1
    assert provider.calls == 1
    assert provider.schema == INTENT_JSON_SCHEMA
    assert provider.schema["additionalProperties"] is False


def test_offline_or_invalid_model_output_degrades_to_clarification() -> None:
    for value in (ProviderUnavailable("Ollama no disponible"), "no es json", _payload(destination_hints=["C:\\Windows"])):
        provider = IntentProvider(value)
        result = IntentParser(provider).parse("Archiva el curso que vimos en el lugar habitual.")
        if value == _payload(destination_hints=["C:\\Windows"]):
            # The parser can retain a label, but the destination resolver is
            # responsible for rejecting absolute paths before a plan exists.
            assert result.intent.destination_hints == ("C:\\Windows",)
        else:
            assert result.intent.clarification_required
            assert result.provider_error
        assert provider.calls == 1


def test_explicit_exclusion_survives_model_output() -> None:
    provider = IntentProvider(_payload(explicit_exclusions=[]))
    result = IntentParser(provider).parse(
        "Usa el lugar habitual para el curso que vimos. No toques Workspace."
    )
    assert "workspace" in result.intent.explicit_exclusions


def test_known_watched_folder_in_exclusion_is_not_treated_as_positive_source() -> None:
    result = IntentParser().parse(
        "No toques nada de Workspace.", known_sources=("Downloads", "Workspace"),
    )
    assert result.intent.source_hints == ()
    assert result.intent.explicit_exclusions == ("workspace",)
    assert result.intent.clarification_required


def test_model_cannot_reintroduce_an_explicitly_excluded_source() -> None:
    provider = IntentProvider(_payload(source_hints=["Workspace"], explicit_exclusions=[]))
    result = IntentParser(provider).parse(
        "Usa el lugar habitual para el curso que vimos. No toques Workspace.",
        known_sources=("Downloads", "Workspace"),
    )
    assert result.intent.source_hints == ()
    assert result.intent.explicit_exclusions == ("workspace",)


def test_prompt_contains_request_as_untrusted_data_without_document_content() -> None:
    provider = IntentProvider()
    request = "Usa el lugar habitual para el curso que vimos."
    IntentParser(provider).parse(request, vocabulary=("Base de Datos",))
    payload = json.loads(provider.user_prompt)
    assert payload["untrusted_user_request"] == request
    assert "document_content" not in payload
