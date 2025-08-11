#
# src/supsrc/llm/factory.py
#
"""
Factory for creating LLM provider instances.
"""

import structlog

from supsrc.config.models import LlmConfig
from supsrc.exceptions import ConfigurationError
from supsrc.llm.protocols import LlmProvider

# Import specific providers
from supsrc.llm.ollama import OllamaProvider

log = structlog.get_logger("llm.factory")

PROVIDER_MAP = {
    "ollama": OllamaProvider,
    # "openai": OpenAiProvider,
    # "gemini": GeminiProvider,
}


def get_llm_provider(config: LlmConfig) -> LlmProvider:
    """
    Factory function to get an instance of an LLM provider based on config.
    """
    provider_name = config.provider.lower()
    provider_class = PROVIDER_MAP.get(provider_name)

    if not provider_class:
        log.error("Unsupported LLM provider specified", provider=provider_name)
        raise ConfigurationError(
            f"Unsupported LLM provider: '{config.provider}'. "
            f"Available providers: {list(PROVIDER_MAP.keys())}"
        )

    log.debug("Instantiating LLM provider", provider=provider_name)
    try:
        return provider_class(config)
    except Exception as e:
        log.error("Failed to instantiate LLM provider", provider=provider_name, error=str(e))
        raise ConfigurationError(f"Failed to initialize provider '{provider_name}': {e}") from e

# 🔼⚙️
