#
# src/supsrc/llm/ollama.py
#
"""
LLM Provider implementation for Ollama.
"""

import json
from pathlib import Path

import httpx
import structlog

from supsrc.config.models import LlmConfig
from supsrc.exceptions import SupsrcError
from supsrc.llm.protocols import LlmAnalysisResult, LlmProvider

log = structlog.get_logger("llm.ollama")

# --- Constants ---
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_TIMEOUT = 60.0  # seconds
SYSTEM_PROMPT = """
You are an expert programmer and test engineer integrated into an automated development tool called `supsrc`.
Your role is to analyze the results of a test run, a list of changed files, and the corresponding code changes (as a git diff) to make an intelligent recommendation.

You MUST respond with a single, valid JSON object. Do not add any explanatory text before or after the JSON.

The JSON object must have the following structure:
{
  "is_safe_to_commit": boolean,
  "analysis_type": "test_failure" | "commit_suggestion",
  "suggestion": "string"
}

- If the test exit code is 0 (success):
  - "is_safe_to_commit" MUST be true.
  - "analysis_type" MUST be "commit_suggestion".
  - "suggestion" MUST be a well-formatted git commit message.
  - The commit message should follow the conventional commit specification (e.g., "feat: ...", "fix: ...", "refactor: ...").
  - The commit subject line should be short and describe the core change.
  - The commit body should succinctly summarize the *intent* of the change, not just list what was done. Infer the "why" from the code.
  - The commit message MUST end with a trailer listing the changed files, like this:
    ```
    auto-commit: <commit message you created>

    Modified (2):
    - path/to/file1.py
    - path/to/another/file.ext

    Added (1):
    - path/to/another.py

    Removed (1):
    - path/to/removed.py
    ```
- If the test exit code is not 0 (failure):
  - "is_safe_to_commit" MUST be false.
  - "analysis_type" MUST be "test_failure".
  - "suggestion" MUST be a brief, helpful diagnosis of the test failure. Analyze the test output and the diff to determine if the failure is likely due to a bug in the new code or an issue with the test itself. Guide the developer on how to fix it.
"""

USER_PROMPT_TEMPLATE = """
Here is the data for your analysis:

1. Test Exit Code:
{exit_code}

2. Staged Files:
{staged_files}

3. Test Output (stdout/stderr):
{test_output}

4. Staged Code Changes (git diff):
```diff
{staged_diff}```

Please provide your analysis as a single JSON object.
"""

class OllamaProvider(LlmProvider):
    """LLM provider implementation for Ollama."""

    def __init__(self, config: LlmConfig):
        self.config = config
        self.base_url = config.base_url or DEFAULT_OLLAMA_URL
        self._log = log.bind(
            provider="ollama",
            model=self.config.model,
            base_url=self.base_url,
        )

    async def analyze_and_suggest(
        self,
        staged_diff: str,
        staged_files: list[str],
        test_output: str,
        test_exit_code: int,
        repo_path: Path,
    ) -> LlmAnalysisResult:
        """
        Sends analysis request to Ollama and parses the response.
        """
        self._log.info("Starting analysis with Ollama")

        formatted_staged_files = "\n".join(f"- {f}" for f in staged_files) if staged_files else "No files staged."

        user_prompt = USER_PROMPT_TEMPLATE.format(
            exit_code=test_exit_code,
            staged_files=formatted_staged_files,
            test_output=test_output or "No output.",
            staged_diff=staged_diff or "No staged changes.",
        )

        request_payload = {
            "model": self.config.model,
            "format": "json",
            "stream": False,
            "system": SYSTEM_PROMPT,
            "prompt": user_prompt,
        }

        api_url = f"{self.base_url}/api/generate"
        raw_json_response = "" # Initialize for use in exception logging

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                self._log.debug("Sending request to Ollama API", url=api_url)
                response = await client.post(api_url, json=request_payload)
                response.raise_for_status()

            response_data = response.json()
            raw_json_response = response_data.get("response")

            if not raw_json_response:
                raise SupsrcError("Ollama response was empty or missing 'response' field.")

            parsed_json = json.loads(raw_json_response)
            self._log.debug("Successfully parsed JSON from Ollama response", parsed_json=parsed_json)

            if not all(k in parsed_json for k in ["is_safe_to_commit", "analysis_type", "suggestion"]):
                raise SupsrcError("Ollama JSON response is missing required keys.")

            return LlmAnalysisResult(
                is_safe_to_commit=parsed_json["is_safe_to_commit"],
                analysis_type=parsed_json["analysis_type"],
                suggestion=parsed_json["suggestion"],
            )

        except httpx.RequestError as e:
            self._log.error("Ollama API request failed", error=str(e))
            raise SupsrcError(f"Failed to connect to Ollama at {self.base_url}. Is it running?") from e
        except httpx.HTTPStatusError as e:
            self._log.error("Ollama API returned an error status", status_code=e.response.status_code, response_text=e.response.text)
            raise SupsrcError(f"Ollama API error ({e.response.status_code}): {e.response.text}") from e
        except (json.JSONDecodeError, TypeError) as e:
            self._log.error("Failed to parse JSON from Ollama response", raw_response=raw_json_response, error=str(e))
            raise SupsrcError("Ollama returned invalid JSON.") from e
        except Exception as e:
            self._log.exception("An unexpected error occurred during Ollama analysis")
            if isinstance(e, SupsrcError):
                raise
            raise SupsrcError(f"An unexpected error occurred: {e}") from e
# 🔼⚙️
