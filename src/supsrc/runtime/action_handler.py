# src/supsrc/runtime/action_handler.py
"""
Handles the execution of the triggered action sequence for a repository.
"""
import asyncio
import os
from pathlib import Path

import structlog

from supsrc.config import SupsrcConfig
from supsrc.config.models import LLMConfig
from supsrc.protocols import (
    CommitResult,
    PushResult,
    RepositoryEngine,
    RepoStatusResult,
    StageResult,
)
from supsrc.runtime.tui_interface import TUIInterface
from supsrc.state import RepositoryState, RepositoryStatus
from supsrc.telemetry import StructLogger

# LLM imports are conditional
try:
    from supsrc.llm.providers.base import LLMProvider
    from supsrc.llm.providers.gemini import GeminiProvider
    from supsrc.llm.providers.ollama import OllamaProvider

    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False
    LLMProvider = None
    GeminiProvider = None
    OllamaProvider = None


log: StructLogger = structlog.get_logger("runtime.action_handler")


class ActionHandler:
    """Executes the full status -> stage -> commit -> push sequence."""

    def __init__(
        self,
        config: SupsrcConfig,
        repo_states: dict[str, RepositoryState],
        repo_engines: dict[str, RepositoryEngine],
        tui: TUIInterface,
    ):
        self.config = config
        self.repo_states = repo_states
        self.repo_engines = repo_engines
        self.tui = tui
        self._llm_providers: dict[str, LLMProvider] = {}
        log.debug("ActionHandler initialized.")

    def _get_llm_provider(self, llm_config: LLMConfig) -> LLMProvider | None:
        """Instantiates and returns an LLM provider based on config."""
        if not LLM_AVAILABLE:
            return None

        provider_key = f"{llm_config.provider}:{llm_config.model}"
        if provider_key in self._llm_providers:
            return self._llm_providers[provider_key]

        api_key = (
            os.environ.get(llm_config.api_key_env_var) if llm_config.api_key_env_var else None
        )

        provider_map = {"gemini": GeminiProvider, "ollama": OllamaProvider}
        provider_class = provider_map.get(llm_config.provider)

        if not provider_class:
            log.error("Unsupported LLM provider specified", provider=llm_config.provider)
            return None

        try:
            provider = provider_class(model=llm_config.model, api_key=api_key)
            self._llm_providers[provider_key] = provider
            return provider
        except (ImportError, ValueError) as e:
            log.error("Failed to instantiate LLM provider", error=str(e), exc_info=True)
            return None

    async def _get_staged_diff(self, workdir: Path) -> str:
        """Runs `git diff --staged` and returns the output."""
        proc = await asyncio.create_subprocess_shell(
            "git diff --staged",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            log.error("Failed to get staged diff", stderr=stderr.decode())
            return ""
        return stdout.decode()

    def _infer_test_command(self, workdir: Path) -> str | None:
        """Infers a default test command based on project structure."""
        if (workdir / "pyproject.toml").exists():
            log.info("Inferred 'pytest' for Python project.", repo_path=str(workdir))
            return "pytest"
        if (workdir / "package.json").exists():
            log.info("Inferred 'npm test' for Node.js project.", repo_path=str(workdir))
            return "npm test"
        if (workdir / "go.mod").exists():
            log.info("Inferred 'go test ./...' for Go project.", repo_path=str(workdir))
            return "go test ./..."
        if (workdir / "Cargo.toml").exists():
            log.info("Inferred 'cargo test' for Rust project.", repo_path=str(workdir))
            return "cargo test"

        log.warning("Could not infer a default test command.", repo_path=str(workdir))
        return None

    async def _run_tests(
        self, command: str | None, workdir: Path
    ) -> tuple[int, str, str]:
        """Runs the configured or inferred test command."""
        effective_command = command or self._infer_test_command(workdir)

        if not effective_command:
            log.warning("No test command configured or inferred, skipping tests.")
            return 0, "Skipped: No test command configured or inferred.", ""

        log.info("Running test command", command=effective_command)
        proc = await asyncio.create_subprocess_shell(
            effective_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir,
        )
        stdout, stderr = await proc.communicate()
        return proc.returncode or 0, stdout.decode(), stderr.decode()

    async def _save_change_fragment(
        self, content: str, repo_path: Path, fragment_dir: str | None, commit_hash: str
    ):
        """Saves a change fragment to the specified directory."""
        if not fragment_dir:
            return
        dir_path = repo_path / fragment_dir
        dir_path.mkdir(exist_ok=True)
        file_path = dir_path / f"{commit_hash[:12]}.feature"
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            log.info("Saved change fragment", path=str(file_path))
            self.tui.post_log_update(None, "INFO", f"Saved change fragment to {file_path.name}")
        except OSError as e:
            log.error("Failed to save change fragment", path=str(file_path), error=str(e))

    async def execute_action_sequence(self, repo_id: str) -> None:
        """Runs the full action workflow, including optional LLM steps."""
        repo_state = self.repo_states.get(repo_id)
        repo_config = self.config.repositories.get(repo_id)
        repo_engine = self.repo_engines.get(repo_id)
        action_log = log.bind(repo_id=repo_id)

        if not all((repo_state, repo_config, repo_engine)):
            action_log.error("Action failed: Missing state, config, or engine.")
            self.tui.post_log_update(
                repo_id, "ERROR", "Action failed: Missing state/config/engine."
            )
            return

        action_log.info("Executing action sequence...")
        self.tui.post_log_update(repo_id, "INFO", "Action triggered. Starting workflow...")

        try:
            # 1. Get Status
            repo_state.update_status(RepositoryStatus.PROCESSING)
            repo_state.action_description = "Checking status..."
            self.tui.post_state_update(self.repo_states)

            status_result: RepoStatusResult = await repo_engine.get_status(
                repo_state, repo_config.repository, self.config.global_config, repo_config.path
            )
            if not status_result.success or status_result.is_conflicted or status_result.is_clean:
                if not status_result.success:
                    repo_state.update_status(RepositoryStatus.ERROR, f"Status check failed: {status_result.message}")
                elif status_result.is_conflicted:
                    repo_state.update_status(RepositoryStatus.ERROR, "Repo has conflicts.")
                else: # is_clean
                    repo_state.reset_after_action()
                self.tui.post_state_update(self.repo_states)
                return

            # 2. Stage Changes
            repo_state.update_status(RepositoryStatus.STAGING)
            stage_result: StageResult = await repo_engine.stage_changes(
                None, repo_state, repo_config.repository, self.config.global_config, repo_config.path
            )
            if not stage_result.success:
                repo_state.update_status(RepositoryStatus.ERROR, f"Staging failed: {stage_result.message}")
                self.tui.post_state_update(self.repo_states)
                return

            staged_diff = await self._get_staged_diff(repo_config.path)
            commit_message = ""

            # --- LLM Pipeline ---
            llm_config = repo_config.llm
            if llm_config and llm_config.enabled and LLM_AVAILABLE:
                llm_provider = self._get_llm_provider(llm_config)
                if not llm_provider:
                    repo_state.update_status(RepositoryStatus.ERROR, "LLM provider failed to init.")
                    self.tui.post_state_update(self.repo_states)
                    return

                if llm_config.review_changes:
                    repo_state.update_status(RepositoryStatus.REVIEWING)
                    veto, reason = await llm_provider.review_changes(staged_diff)
                    if veto:
                        repo_state.update_status(RepositoryStatus.ERROR, f"LLM Review Veto: {reason}")
                        self.tui.post_state_update(self.repo_states)
                        return

                if llm_config.run_tests:
                    repo_state.update_status(RepositoryStatus.TESTING)
                    exit_code, stdout, stderr = await self._run_tests(llm_config.test_command, repo_config.path)
                    if exit_code != 0:
                        failure_output = f"STDOUT:\n{stdout}\n\nSTDERR:\n{stderr}"
                        analysis = "Test run failed."
                        if llm_config.analyze_test_failures:
                            repo_state.update_status(RepositoryStatus.ANALYZING)
                            analysis = await llm_provider.analyze_test_failure(failure_output)
                        repo_state.update_status(RepositoryStatus.ERROR, f"Tests Failed: {analysis}")
                        self.tui.post_state_update(self.repo_states)
                        return

                if llm_config.generate_commit_message:
                    repo_state.update_status(RepositoryStatus.GENERATING_COMMIT)
                    # The LLM now generates only the subject line of the commit.
                    llm_subject = await llm_provider.generate_commit_message(
                        staged_diff, llm_config.use_conventional_commit
                    )
                    # We construct the final message template, preserving the placeholder for the body.
                    commit_message = f"{llm_subject}\n\n{{{{change_summary}}}}"

            # 3. Perform Commit
            repo_state.update_status(RepositoryStatus.COMMITTING)
            commit_result: CommitResult = await repo_engine.perform_commit(
                commit_message, repo_state, repo_config.repository, self.config.global_config, repo_config.path
            )

            if not commit_result.success:
                repo_state.update_status(RepositoryStatus.ERROR, f"Commit failed: {commit_result.message}")
            elif commit_result.commit_hash is None:
                repo_state.reset_after_action()
            else:
                repo_state.last_commit_short_hash = commit_result.commit_hash[:7]

                if llm_config and llm_config.enabled and llm_config.generate_change_fragment and llm_provider:
                    summary_result = await repo_engine.get_summary(repo_config.path)
                    final_commit_message = summary_result.head_commit_message_summary or ""
                    fragment = await llm_provider.generate_change_fragment(staged_diff, final_commit_message)
                    await self._save_change_fragment(fragment, repo_config.path, llm_config.change_fragment_dir, commit_result.commit_hash)

                # 4. Perform Push
                action_log.info("Commit successful", commit_hash=repo_state.last_commit_short_hash)
                repo_state.update_status(RepositoryStatus.PUSHING)
                push_result: PushResult = await repo_engine.perform_push(
                    repo_state, repo_config.repository, self.config.global_config, repo_config.path
                )
                if not push_result.success:
                    action_log.warning("Push failed", reason=push_result.message)

                repo_state.reset_after_action()

            self.tui.post_state_update(self.repo_states)

        except Exception as e:
            action_log.critical("Unexpected error in action sequence", error=str(e), exc_info=True)
            if repo_state:
                repo_state.update_status(RepositoryStatus.ERROR, f"Action failure: {e}")
                self.tui.post_state_update(self.repo_states)
