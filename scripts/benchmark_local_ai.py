"""Opt-in Ollama benchmark using only synthetic document descriptions."""

import argparse
import json
import statistics
import time
import tracemalloc
from collections.abc import Callable, Sequence

from ordenia.ai.models import AIContext, DEFAULT_AI_MODEL
from ordenia.ai.parser import parse_suggestion
from ordenia.ai.prompts import SYSTEM_PROMPT, user_prompt
from ordenia.ai.providers.base import AIProvider, ProviderTimeout
from ordenia.ai.providers.ollama import OllamaProvider


SAMPLES = (
    ("factura-2026.pdf", "Factura por equipos electrónicos, subtotal, IGV y total."),
    ("curso.pdf", "Material académico de bases de datos: SQL, normalización y modelo relacional."),
    ("manual.pdf", "Manual técnico del sensor ultrasónico HC-SR04 para ESP32."),
    ("plan.md", "Proyecto OrdenIA: tareas, arquitectura, entregables y fechas."),
    ("sensor.py", "Código Python para leer un sensor y almacenar mediciones."),
    ("presentacion.pptx", "Presentación ejecutiva sobre gestión de residuos y recolección."),
)


def benchmark_model(
    provider: AIProvider,
    model: str,
    samples: Sequence[tuple[str, str]] = SAMPLES,
    emit: Callable[[str], None] = print,
) -> dict[str, object]:
    durations: list[float] = []
    timeouts = 0
    failed = 0
    tracemalloc.start()
    for index, (name, document_text) in enumerate(samples):
        context = AIContext(name, "." + name.rsplit(".", 1)[-1], "Otros", "", {}, (),
                            (document_text,), (), ())
        prompt = user_prompt(context)
        started = time.perf_counter()
        result: dict[str, object] = {
            "case": name, "model": model, "context_characters": len(SYSTEM_PROMPT) + len(prompt),
            "first_inference_for_model": index == 0, "success": False, "timeout": False,
            "json_valid": False, "document_type": None, "topic": None,
            "suggested_path": None, "confidence": None,
        }
        try:
            response = provider.generate(SYSTEM_PROMPT, prompt, model)
            elapsed = time.perf_counter() - started
            raw_value = json.loads(response.content)
            if isinstance(raw_value, dict):
                result.update({
                    "json_valid": True,
                    "document_type": raw_value.get("document_type"),
                    "topic": raw_value.get("topic"),
                    "suggested_path": raw_value.get("suggested_path"),
                    "confidence": raw_value.get("confidence"),
                })
            suggestion = parse_suggestion(response.content)
            durations.append(elapsed)
            result.update({
                "seconds": round(elapsed, 3), "success": True, "json_valid": True,
                "document_type": suggestion.document_type, "topic": suggestion.topic,
                "suggested_path": suggestion.suggested_path, "confidence": suggestion.confidence,
                "load_seconds": round(response.load_seconds, 3),
                "prompt_eval_count": response.prompt_eval_count, "eval_count": response.eval_count,
                "eval_seconds": round(response.eval_seconds, 3), "error": None,
            })
        except ProviderTimeout as exc:
            timeouts += 1
            failed += 1
            result.update({"seconds": round(time.perf_counter() - started, 3),
                           "timeout": True, "error": str(exc)})
        except Exception as exc:
            failed += 1
            result.update({"seconds": round(time.perf_counter() - started, 3),
                           "error": f"{type(exc).__name__}: {exc}"})
        emit(json.dumps(result, ensure_ascii=False))
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    summary: dict[str, object] = {
        "summary": True, "model": model, "total": len(samples),
        "successful": len(durations), "failed": failed, "timeouts": timeouts,
        "average_seconds": round(statistics.mean(durations), 3) if durations else None,
        "minimum_seconds": round(min(durations), 3) if durations else None,
        "maximum_seconds": round(max(durations), 3) if durations else None,
        "peak_python_mb": round(peak / 1024 / 1024, 3),
        "first_case_is_cold_start_candidate": True,
    }
    emit(json.dumps(summary, ensure_ascii=False))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", action="append", dest="models",
                        help=f"Modelo local; puede repetirse. Por defecto: {DEFAULT_AI_MODEL}")
    parser.add_argument("--timeout", type=float, default=300.0,
                        help="Timeout por caso en segundos (10-300; por defecto 300).")
    args = parser.parse_args()
    timeout = max(10.0, min(300.0, args.timeout))
    models = tuple(dict.fromkeys(args.models or [DEFAULT_AI_MODEL]))
    provider = OllamaProvider(inference_timeout=timeout)
    status = provider.check()
    if not status.available:
        print(json.dumps({"error": status.message}, ensure_ascii=False))
        return 2
    available = [model for model in models if model in status.models]
    for model in models:
        if model not in status.models:
            print(json.dumps({"model": model, "error": "Modelo no instalado",
                              "command": f"ollama pull {model}"}, ensure_ascii=False))
    if not available:
        return 2
    summaries = [benchmark_model(provider, model) for model in available]
    print(json.dumps({"overall": True, "models": summaries}, ensure_ascii=False))
    return 0 if all(summary["successful"] for summary in summaries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
