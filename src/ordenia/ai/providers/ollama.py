"""Minimal localhost-only Ollama client using the Python standard library."""

import json
import ipaddress
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from ordenia.ai.models import AIResponse, ProviderStatus, SUGGESTION_JSON_SCHEMA

from .base import AIProvider, ModelNotInstalled, ProviderTimeout, ProviderUnavailable


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise ProviderUnavailable("Ollama intentó redirigir la solicitud fuera del endpoint local.")


class OllamaProvider(AIProvider):
    name = "ollama"
    _LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
    MAX_PREDICT_TOKENS = 384

    def __init__(self, endpoint: str = "http://127.0.0.1:11434", *,
                 connect_timeout: float = 3.0, inference_timeout: float = 300.0) -> None:
        endpoint = endpoint.rstrip("/")
        parsed = urlparse(endpoint)
        if parsed.scheme != "http" or parsed.hostname not in self._LOCAL_HOSTS or parsed.username or parsed.password:
            raise ValueError("Ollama debe utilizar un endpoint HTTP local.")
        try:
            addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 80, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ValueError("No se pudo resolver el endpoint local de Ollama.") from exc
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_loopback for item in addresses):
            raise ValueError("Ollama debe resolver exclusivamente a direcciones loopback.")
        self.endpoint = endpoint
        self.connect_timeout = connect_timeout
        self.inference_timeout = inference_timeout
        # Never honor HTTP(S)_PROXY for document-bearing requests. Even a
        # misconfigured environment cannot route Ollama traffic off-device.
        self._opener = build_opener(ProxyHandler({}), _RejectRedirects())

    def _request(self, path: str, *, data: dict[str, object] | None = None, timeout: float) -> dict[str, object]:
        encoded = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.endpoint + path, data=encoded,
            headers={"Content-Type": "application/json"},
            method="GET" if data is None else "POST",
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:  # endpoint is validated loopback
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code == 404 and "model" in detail.casefold():
                raise ModelNotInstalled("El modelo configurado no está instalado en Ollama.") from exc
            raise ProviderUnavailable(f"Ollama respondió con HTTP {exc.code}.") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ProviderTimeout("Ollama tardó demasiado en responder.") from exc
        except (URLError, ConnectionError, OSError) as exc:
            if isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout)):
                raise ProviderTimeout("Ollama tardó demasiado en responder.") from exc
            raise ProviderUnavailable("IA local no disponible. Inicia Ollama e inténtalo de nuevo.") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderUnavailable("Ollama devolvió una respuesta no válida.") from exc
        if not isinstance(payload, dict):
            raise ProviderUnavailable("Ollama devolvió una respuesta no válida.")
        return payload

    def check(self) -> ProviderStatus:
        try:
            payload = self._request("/api/tags", timeout=self.connect_timeout)
            models = tuple(sorted(
                str(item.get("name", "")) for item in payload.get("models", [])
                if isinstance(item, dict) and item.get("name")
            ))
            return ProviderStatus(self.name, True, models, "Ollama disponible")
        except (ProviderUnavailable, ProviderTimeout) as exc:
            return ProviderStatus(self.name, False, (), str(exc))

    def generate(self, system_prompt: str, user_prompt: str, model: str) -> AIResponse:
        if not model.strip():
            raise ModelNotInstalled("Configura un modelo local de Ollama.")
        payload = self._request("/api/chat", data={
            "model": model.strip(),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "think": False,
            "format": SUGGESTION_JSON_SCHEMA,
            "stream": False,
            "keep_alive": "5m",
            "options": {"temperature": 0.1, "num_predict": self.MAX_PREDICT_TOKENS},
        }, timeout=self.inference_timeout)
        message = payload.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ProviderUnavailable("Ollama no devolvió contenido utilizable.")
        return AIResponse(
            content=content,
            load_seconds=float(payload.get("load_duration") or 0) / 1_000_000_000,
            prompt_eval_count=int(payload.get("prompt_eval_count") or 0),
            eval_count=int(payload.get("eval_count") or 0),
            eval_seconds=float(payload.get("eval_duration") or 0) / 1_000_000_000,
        )
