#
# src/supsrc/llm/providers/ollama.py
#
"""
LLMProvider implementation for Ollama.
"""
import re

import structlog

from supsrc.llm.prompts import (
    BASIC_COMMIT_PROMPT_TEMPLATE,
    CHANGE_FRAGMENT_PROMPT_TEMPLATE,
    CODE_REVIEW_PROMPT_TEMPLATE,
    CONVENTIONAL_COMMIT_PROMPT_TEMPLATE,
    TEST_FAILURE_ANALYSIS_PROMPT_TEMPLATE,
)

try:
    import ollama
except ImportError:
    ollama = None


log = structlog.get_logger("llm.provider.ollama")


def _clean_llm_output(raw_text: str) -> str:
    """Strips conversational boilerplate and markdown from LLM responses."""
    # First, try to find a markdown code block and extract its content
    match = re.search(r"```(?:\w*\n)?(.*?)```", raw_text, re.DOTALL)
    if match:
        return match.group(1).strip()

    # If no markdown, find the last non-empty line, which is often the answer
    lines = [line.strip() for line in raw_text.strip().split("\n")]
    non_empty_lines = [line for line in lines if line]
    return non_empty_lines[-1] if non_empty_lines else raw_text


class OllamaProvider:
    """LLMProvider implementation for a local Ollama instance."""

    def __init__(self, model: str, api_key: str | None = None) -> None:
        if not ollama:
            raise ImportError("Ollama library not found. Please install `supsrc[llm]`.")
        self.model = model
        self.client = ollama.AsyncClient()
        log.info("OllamaProvider initialized", model=model)

    async def _generate(self, prompt: str) -> str:
        """Internal helper to run generation."""
        try:
            response = await self.client.generate(model=self.model, prompt=prompt)
            return response["response"].strip()
        except ollama.ResponseError as e:
            log.error("Ollama API call failed", error=str(e.body), status_code=e.status_code, exc_info=True)
            return f"Error: LLM generation failed. Status: {e.status_code}"
        except Exception as e:
            log.error("An unexpected error occurred with the Ollama provider", error=str(e), exc_info=True)
            return f"Error: An unexpected error occurred. {e}"

    async def generate_commit_message(self, diff: str, conventional: bool) -> str:
        log.debug("Generating commit message with Ollama", conventional=conventional)
        prompt_template = (
            CONVENTIONAL_COMMIT_PROMPT_TEMPLATE if conventional else BASIC_COMMIT_PROMPT_TEMPLATE
        )
        prompt = prompt_template.format(diff=diff)
        raw_response = await self._generate(prompt)
        return _clean_llm_output(raw_response)

    async def review_changes(self, diff: str) -> tuple[bool, str]:
        log.debug("Reviewing changes with Ollama")
        prompt = CODE_REVIEW_PROMPT_TEMPLATE.format(diff=diff)
        response = await self._generate(prompt)

        if response.startswith("VETO:"):
            reason = response.removeprefix("VETO:").strip()
            log.warning("Ollama review vetoed commit", reason=reason)
            return True, reason
        return False, "OK"

    async def analyze_test_failure(self, output: str) -> str:
        log.debug("Analyzing test failure with Ollama")
        prompt = TEST_FAILURE_ANALYSIS_PROMPT_TEMPLATE.format(output=output)
        return await self._generate(prompt)

    async def generate_change_fragment(self, diff: str, commit_message: str) -> str:
        log.debug("Generating change fragment with Ollama")
        prompt = CHANGE_FRAGMENT_PROMPT_TEMPLATE.format(
            commit_message=commit_message, diff=diff
        )
        raw_response = await self._generate(prompt)
        return _clean_llm_output(raw_response)


# 🧠🦙
