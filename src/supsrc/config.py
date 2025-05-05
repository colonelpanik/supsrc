#
# supsrc/config.py
#
"""
Configuration loading and validation for supsrc. Uses structlog.
"""

import logging  # Still needed for level names in setup/validation
import re
import tomllib
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal, TypeAlias

# --- Third-party Libraries ---
try:
    import rich.pretty
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

import cattrs
import structlog  # Import structlog
from attrs import define, field, mutable

# --- Custom Exceptions Import ---
try:
    from .exceptions import (
        ConfigFileNotFoundError,
        ConfigParsingError,
        ConfigurationError,
        ConfigValidationError,
        DurationValidationError,
    )
except ImportError:
    # Fallback for direct execution
    from exceptions import (
        ConfigFileNotFoundError,
        ConfigParsingError,
        ConfigurationError,
        ConfigValidationError,
        DurationValidationError,
    )

# --- Centralized Logger ---
log: structlog.stdlib.BoundLogger = structlog.get_logger("cfg")

# --- Rich Markup Styles ---
PATH_STYLE = "bold cyan"
VALUE_STYLE = "bold magenta"
ERROR_DETAIL_STYLE = "italic red"
TIME_STYLE = "bold green"
WARN_STYLE = "yellow"

# --- Helper Functions & Validators ---

def _parse_duration(
    duration_str: str, config_path_context: Path | None = None
) -> timedelta:
    """Parses duration string. Raises DurationValidationError."""
    log.debug(
        "Parsing duration string", duration_str=duration_str, emoji_key="time"
    )
    pattern = re.compile(
        r"^\s*(?:(?P<hours>\d+)\s*h)?\s*(?:(?P<minutes>\d+)\s*m)?\s*(?:(?P<seconds>\d+)\s*s)?\s*$"
    )
    match = pattern.match(duration_str)
    if not match or not duration_str.strip():
        msg = "Invalid duration format. Use '1h', '30m', '15s'."
        log.error(msg, received=duration_str, emoji_key="fail")
        raise DurationValidationError(
            msg, duration_str, str(config_path_context)
        )

    parts = match.groupdict()
    time_params = {k: int(v) for k, v in parts.items() if v}

    if not time_params:
        msg = "Empty duration string provided"
        log.error(msg, received=duration_str, emoji_key="fail")
        raise DurationValidationError(
            msg, duration_str, str(config_path_context)
        )

    try:
        duration = timedelta(**time_params)
    except ValueError as e:
        msg = "Invalid time values for timedelta"
        log.error(
            msg,
            error=str(e),
            duration_str=duration_str,
            exc_info=True,
            emoji_key="fail",
        )
        raise DurationValidationError(
            f"{msg}: {e}", duration_str, str(config_path_context)
        ) from e

    if duration <= timedelta(0):
        msg = "Duration must be positive"
        log.error(
            msg,
            result=str(duration),
            duration_str=duration_str,
            emoji_key="fail",
        )
        raise DurationValidationError(
            f"{msg}: {duration}", duration_str, str(config_path_context)
        )

    log.debug(
        "Parsed duration",
        duration_str=duration_str,
        result=str(duration),
        emoji_key="time",
    )
    return duration


def _validate_log_level(inst: Any, attr: Any, value: str) -> None:
    """Validator for standard logging level names."""
    # Still use stdlib level names for validation consistency
    valid = logging._nameToLevel.keys()
    if value.upper() not in valid:
        msg = "Invalid log_level"
        log.error(
            msg, value=value, valid_levels=list(valid), emoji_key="fail"
        )
        raise ConfigValidationError(f"{msg}: '{value}'")
    log.debug("Log level valid", value=value)


def _validate_positive_int(inst: Any, attr: Any, value: int) -> None:
    """Validator ensures integer is positive."""
    if not isinstance(value, int) or value <= 0:
        msg = f"Field '{attr.name}' must be positive integer"
        log.error(
            msg, field_name=attr.name, value=value, emoji_key="fail"
        )
        raise ConfigValidationError(f"{msg}, got {value}")
    log.debug(
        f"Field '{attr.name}' validated positive",
        field_name=attr.name,
        value=value,
    )


# --- attrs Data Classes ---

@define(slots=True)
class InactivityTrigger:
    """Commit trigger based on inactivity period."""
    type: Literal["inactivity"] = field(default="inactivity", init=False)
    period: timedelta = field()


@define(slots=True)
class SaveCountTrigger:
    """Commit trigger based on number of save events."""
    type: Literal["save_count"] = field(default="save_count", init=False)
    count: int = field(validator=_validate_positive_int)


@define(slots=True)
class ManualTrigger:
    """Commit trigger requiring manual intervention."""
    type: Literal["manual"] = field(default="manual", init=False)


# Type alias for the union of trigger types
TriggerConfig: TypeAlias = InactivityTrigger | SaveCountTrigger | ManualTrigger


@mutable(slots=True)
class RepositoryConfig:
    """
    Configuration for a repository. Mutable to allow disabling on load if path invalid.
    """
    # Mandatory fields first
    path: Path = field()
    trigger: TriggerConfig = field()
    # Optional fields after
    enabled: bool = field(default=True)
    commit_message: str | None = field(default=None)
    auto_push: bool | None = field(default=None)
    # Internal state flag
    _path_valid: bool = field(default=True, repr=False, init=False)


@define(frozen=True, slots=True)
class GlobalConfig:
    """Global default settings for supsrc."""
    log_level: str = field(default="INFO", validator=_validate_log_level)
    default_commit_message: str = field(
        default="supsrc auto-commit: {{timestamp}}"
    )
    default_auto_push: bool = field(default=True)

    @property
    def numeric_log_level(self) -> int:
        """Return the numeric logging level."""
        # Still useful for passing to setup_logging
        return logging.getLevelName(self.log_level.upper())


@define(frozen=True, slots=True)
class SupsrcConfig:
    """Root configuration object for the supsrc application."""
    repositories: dict[str, RepositoryConfig] = field(factory=dict)
    global_config: GlobalConfig = field(
        factory=GlobalConfig, metadata={"toml_name": "global"}
    )


# --- Cattrs Converter and Hooks ---

converter = cattrs.Converter()
_CURRENT_CONFIG_PATH_CONTEXT: Path | None = None  # Context for hooks


def _structure_path_simple(path_str: str, type_hint: type[Path]) -> Path:
    """Cattrs structure hook for Path: Expands/resolves ONLY."""
    if not isinstance(path_str, str):
        raise ConfigValidationError(
            f"Path must be string, got: {type(path_str).__name__}"
        )
    log.debug("Structuring path string", path_str=path_str, emoji_key="path")
    try:
        p = Path(path_str).expanduser().resolve()
        log.debug("Expanded/resolved path", path=str(p), emoji_key="path")
        return p
    except Exception as e:
        msg = "Error processing path string"
        log.error(
            msg, path_str=path_str, error=str(e), exc_info=True, emoji_key="fail"
        )
        raise ConfigValidationError(
            f"{msg} '{path_str}': {e}"
        ) from e


# Register hooks with the converter
converter.register_structure_hook(Path, _structure_path_simple)
converter.register_structure_hook(
    timedelta,
    lambda d, t: _parse_duration(d, _CURRENT_CONFIG_PATH_CONTEXT),
)


# --- Core Loading Function ---

def load_config(config_path: Path) -> SupsrcConfig:
    """Loads, validates, structures config. Handles invalid paths gracefully."""
    global _CURRENT_CONFIG_PATH_CONTEXT
    _CURRENT_CONFIG_PATH_CONTEXT = config_path

    log.info(
        "Attempting config load", path=str(config_path), emoji_key="load"
    )
    if not config_path.is_file():
        msg = "Config file not found"
        log.error(msg, path=str(config_path), emoji_key="fail")
        raise ConfigFileNotFoundError(path=str(config_path))

    try:
        log.debug("Reading TOML...")
        with open(config_path, "rb") as f:
            toml_data = tomllib.load(f)
        log.debug("TOML read OK.")
    except tomllib.TOMLDecodeError as e:
        msg = "Invalid TOML syntax"
        log.error(
            msg,
            path=str(config_path),
            error=str(e),
            exc_info=True,
            emoji_key="fail",
        )
        raise ConfigParsingError(
            str(e), path=str(config_path), details=e
        ) from e

    try:
        log.debug("Structuring TOML data...")
        config_object = converter.structure(toml_data, SupsrcConfig)
        log.debug("Initial structuring complete.")

        # --- Post-Structuring Path Validation ---
        log.debug("Performing post-structuring path validation...")
        repos_to_process = list(config_object.repositories.items())
        for repo_id, repo_config in repos_to_process:
            p = repo_config.path
            path_valid = True
            if not p.exists():
                path_valid = False
                log.warning(
                    "Path does not exist, disabling repo",
                    repo_id=repo_id,
                    path=str(p),
                    emoji_key="fail",
                )
            elif not p.is_dir():
                path_valid = False
                log.warning(
                    "Path is not a directory, disabling repo",
                    repo_id=repo_id,
                    path=str(p),
                    emoji_key="fail",
                )

            if not path_valid:
                # Modify the mutable RepositoryConfig object
                repo_config.enabled = False
                repo_config._path_valid = False

        log.info(
            "Config loaded (potential warnings for invalid paths).",
            emoji_key="validate",
        )
        return config_object

    except (cattrs.BaseValidationError, ConfigValidationError) as e:
        log.error(
            "Config validation failed",
            path=str(config_path),
            error=str(e),
            exc_info=True,
            emoji_key="fail",
        )
        # Add details if available (Python 3.11+)
        details_str = ""
        notes = getattr(e, "__notes__", None)
        if notes:
            details_str = "\nDetails:\n" + "\n".join(notes)
        raise ConfigValidationError(
            f"{e}{details_str}", path=str(config_path), details=e
        ) from e
    except Exception as e:
        log.critical(
            "Unexpected error during config structuring",
            error=str(e),
            exc_info=True,
            emoji_key="fail",
        )
        raise ConfigurationError(
            f"Unexpected error processing config: {e}", path=str(config_path)
        ) from e
    finally:
        _CURRENT_CONFIG_PATH_CONTEXT = None

# 🔼⚙️
