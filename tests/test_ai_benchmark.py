"""The development benchmark records failures without aborting the run."""

import json

from ordenia.ai.models import AIResponse, ProviderStatus
from ordenia.ai.providers.base import AIProvider, ProviderTimeout
from scripts.benchmark_local_ai import benchmark_model


class SequenceProvider(AIProvider):
    name = "ollama"

    def __init__(self) -> None:
        self.calls = 0

    def check(self) -> ProviderStatus:
        return ProviderStatus("ollama", True, ("test-model",), "ok")

    def generate(self, _system: str, _user: str, _model: str) -> AIResponse:
        self.calls += 1
        if self.calls == 1:
            raise ProviderTimeout("timeout simulado")
        return AIResponse(json.dumps({
            "document_type": "manual", "topic": "sensores", "tags": ["ESP32"],
            "suggested_path": "Manuales/Electrónica", "confidence": 0.8,
            "reason": "Manual técnico.",
        }), load_seconds=0.2, prompt_eval_count=100, eval_count=40, eval_seconds=1.5)


def test_benchmark_continues_after_timeout_and_summarizes() -> None:
    provider = SequenceProvider()
    output: list[str] = []
    summary = benchmark_model(provider, "test-model", (
        ("first.txt", "primer caso"), ("second.txt", "segundo caso"),
    ), output.append)
    rows = [json.loads(line) for line in output]
    assert provider.calls == 2
    assert rows[0]["timeout"] is True and rows[0]["success"] is False
    assert rows[1]["success"] is True and rows[1]["json_valid"] is True
    assert rows[1]["context_characters"] > 0
    assert summary["total"] == 2
    assert summary["successful"] == 1
    assert summary["failed"] == 1
    assert summary["timeouts"] == 1
    assert summary["average_seconds"] is not None
