"""Provider boundary: AIService depends on this interface, not Ollama."""

from abc import ABC, abstractmethod

from ordenia.ai.models import AIResponse, ProviderStatus


class ProviderError(RuntimeError):
    pass


class ProviderUnavailable(ProviderError):
    pass


class ProviderTimeout(ProviderError):
    pass


class ModelNotInstalled(ProviderError):
    pass


class AIProvider(ABC):
    name: str

    @abstractmethod
    def check(self) -> ProviderStatus:
        raise NotImplementedError

    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str, model: str) -> AIResponse:
        raise NotImplementedError

    def generate_structured(
        self, system_prompt: str, user_prompt: str, model: str,
        schema: dict[str, object],
    ) -> AIResponse:
        """Generate typed output; providers may enforce the schema natively."""
        return self.generate(system_prompt, user_prompt, model)
