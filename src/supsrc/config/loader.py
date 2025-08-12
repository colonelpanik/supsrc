#
# config/loader.py
#
"""
Handles loading, validation, and structuring of supsrc configuration files.
Applies environment variable overrides for global defaults.
"""

import re
import tomllib
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any, TypeAlias

import attrs
import cattrs
import structlog

# --- Custom Exceptions Import ---
# Use relative imports within the same package structure
from supsrc.exceptions import (
    ConfigFileNotFoundError,
    ConfigParsingError,
    ConfigurationError,
    ConfigValidationError,
    DurationValidationError,
)

# Import models from sibling module
from .models import (  # Import specific rule configs too
    InactivityRuleConfig,
    ManualRuleConfig,
    RuleConfig,
    SaveCountRuleConfig,
    SupsrcConfig,
)

# Import type hint from telemetry if needed, or define locally
# from ..telemetry import StructLogger # Assuming telemetry package exists
# Define StructLogger locally if preferred or if telemetry isn't stable yet
StructLogger: TypeAlias = Any  # Replace with actual type from structlog if preferred

log: StructLogger = structlog.get_logger("config.loader")

# --- Rich Markup Styles (Constants for reference) ---
PATH_STYLE = "bold cyan"
VALUE_STYLE = "bold magenta"
ERROR_DETAIL_STYLE = "italic red"
TIME_STYLE = "bold green"
WARN_STYLE = "yellow"

# --- Helper Functions & Validators ---

_CURRENT_CONFIG_PATH_CONTEXT: Path | None = None


def _parse_duration(duration_str: str, config_path_context: Path | None = None) -> timedelta:
    """Parses duration string. Raises DurationValidationError."""
    log.debug("Parsing duration", duration_str=duration_str, emoji_key="time")
    pattern = re.compile(
        r"^\s*(?:(?P<hours>\d+)\s*h)?\s*(?:(?P<minutes>\d+)\s*m)?\s*(?:(?P<seconds>\d+)\s*s)?\s*$"
    )
    match = pattern.match(duration_str)
    if not match or not duration_str.strip():
        msg = "Invalid duration format. Use '1h', '30m', '15s'."
        log.error(msg, received=duration_str, emoji_key="fail")
        raise DurationValidationError(msg, duration_str, str(config_path_context))

    parts = match.groupdict()
    time_params = {k: int(v) for k, v in parts.items() if v}
    if not time_params:
        msg = "Empty duration string provided"
        log.error(msg, received=duration_str, emoji_key="fail")
        raise DurationValidationError(msg, duration_str, str(config_path_context))
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
        raise DurationValidationError(f"{msg}: {e}", duration_str, str(config_path_context)) from e
    if duration <= timedelta(0):
        msg = "Duration must be positive"
        log.error(msg, result=str(duration), duration_str=duration_str, emoji_key="fail")
        raise DurationValidationError(f"{msg}: {duration}", duration_str, str(config_path_context))
    log.debug(
        "Parsed duration",
        duration_str=duration_str,
        result=str(duration),
        emoji_key="time",
    )
    return duration


# --- Cattrs Converter and Hooks ---

converter = cattrs.Converter()


def _structure_path_simple(path_str: str, type_hint: type[Path]) -> Path:
    """Cattrs structure hook for Path: Expands/resolves ONLY."""
    if not isinstance(path_str, str):
        raise ConfigValidationError(f"Path must be string, got: {type(path_str).__name__}")
    log.debug("Structuring path string", path_str=path_str, emoji_key="path")
    try:
        p = Path(path_str).expanduser().resolve()
        log.debug("Expanded/resolved path", path=str(p), emoji_key="path")
        return p
    except Exception as e:
        msg = "Error processing path string"
        log.error(msg, path_str=path_str, error=str(e), exc_info=True, emoji_key="fail")
        raise ConfigValidationError(f"{msg} '{path_str}': {e}") from e


# Hook to structure the RuleConfig union based on the 'type' field
def structure_rule_hook(data: Mapping[str, Any], cl: type[RuleConfig]) -> RuleConfig:
    """Structures the correct RuleConfig based on the 'type' field."""
    if not isinstance(data, Mapping):
        raise ConfigValidationError(
            f"Rule configuration must be a mapping (dict), got {type(data).__name__}"
        )

    rule_type = data.get("type")
    if not rule_type or not isinstance(rule_type, str):
        raise ConfigValidationError("Rule configuration missing or invalid 'type' field.")

    # Map type string to the actual class (adjust paths if needed)
    type_map: dict[str, type[RuleConfig]] = {
        "supsrc.rules.inactivity": InactivityRuleConfig,
        "supsrc.rules.save_count": SaveCountRuleConfig,
        "supsrc.rules.manual": ManualRuleConfig,
        # Add mappings for plugin types here if known, or handle dynamically
    }

    target_class = type_map.get(rule_type)
    if target_class is None:
        # TODO: Add dynamic loading for plugin types here if desired
        raise ConfigValidationError(f"Unknown rule type specified: '{rule_type}'")

    # Use the converter to structure into the specific target class
    try:
        # We structure into the *specific* class found (e.g., InactivityRuleConfig)
        # Need to remove 'type' if the target class uses kw_only=True and doesn't expect it
        data_copy = dict(data)
        if target_class.__attrs_attrs__.type.kw_only:  # Check if 'type' is kw_only
            data_copy.pop("type", None)

        return converter.structure(data_copy, target_class)
    except Exception as e:
        # Add context about which rule type failed
        raise ConfigValidationError(
            f"Failed structuring rule type '{rule_type}': {e}", details=e
        ) from e


# Register standard hooks
converter.register_structure_hook(Path, _structure_path_simple)
converter.register_structure_hook(
    timedelta, lambda d, t: _parse_duration(d, _CURRENT_CONFIG_PATH_CONTEXT)
)
# Register the hook for the RuleConfig union type
converter.register_structure_hook(RuleConfig, structure_rule_hook)


# --- Core Loading Function ---


def load_config(config_path: Path) -> SupsrcConfig:
    """
    Loads, validates, structures config. Handles invalid paths gracefully.
    Applies environment variable overrides for global settings.
    """
    global _CURRENT_CONFIG_PATH_CONTEXT
    _CURRENT_CONFIG_PATH_CONTEXT = config_path
    final_config_object: SupsrcConfig | None = None  # Define outside try

    log.info("Attempting config load", path=str(config_path), emoji_key="load")
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
        log.error(msg, path=str(config_path), error=str(e), exc_info=True, emoji_key="fail")
        raise ConfigParsingError(str(e), path=str(config_path), details=e) from e

    try:
        log.debug("Structuring TOML data...")
        log.debug(f"TOML data: {toml_data}")
        # Initial structure from TOML + attrs defaults
        config_object = converter.structure(toml_data, SupsrcConfig)
        log.debug(f"Structured config: {config_object}")
        log.debug(f"Type of structured config: {type(config_object)}")
        log.debug("Initial structuring complete.")

        # --- Apply Environment Variable Overrides for Global Config ---
        # (Keep this section if you retain global defaults that can be overridden by env vars)
        global_config = config_object.global_config
        global_overrides: dict[str, Any] = {}

        # Example: Override log_level (though CLI already does this with higher precedence)
        # env_log_level = os.getenv('SUPSRC_GLOBAL_LOG_LEVEL') # Use a different name if needed
        # if env_log_level is not None:
        #     try:
        #         # Validate the level from env var
        #         _validate_log_level(None, None, env_log_level) # Use validator
        #         if env_log_level.upper() != global_config.log_level.upper():
        #              log.debug("Applying global.log_level override from env var", value=env_log_level)
        #              global_overrides['log_level'] = env_log_level.upper()
        #     except ValueError as val_err:
        #          log.warning("Invalid log level from environment variable ignored", env_var='SUPSRC_GLOBAL_LOG_LEVEL', value=env_log_level, error=str(val_err))

        # Apply overrides if any were found
        if global_overrides:
            log.info(
                "Applying global config overrides from environment variables",
                overrides=list(global_overrides.keys()),
            )
            try:
                new_global_config = attrs.evolve(global_config, **global_overrides)
                final_config_object = attrs.evolve(config_object, global_config=new_global_config)
            except Exception as evolve_exc:
                log.error(
                    "Failed to apply environment variable overrides",
                    error=str(evolve_exc),
                    exc_info=True,
                )
                final_config_object = config_object  # Fallback
        else:
            final_config_object = config_object  # No overrides

        # --- Post-Structuring Path Validation ---
        log.debug("Performing post-structuring path validation...")
        repos_to_process = list(final_config_object.repositories.items())
        for repo_id, repo_config in repos_to_process:
            p = repo_config.path
            path_valid = True
            try:
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
            except OSError as e:
                path_valid = False
                log.warning(
                    "Cannot access path, disabling repo",
                    repo_id=repo_id,
                    path=str(p),
                    error=str(e),
                    emoji_key="fail",
                )

            if not path_valid:
                repo_config.enabled = False
                repo_config._path_valid = False

        log.info(
            "Config loaded (env overrides applied, potential warnings for invalid paths).",
            emoji_key="validate",
        )
        return final_config_object

    except (cattrs.BaseValidationError, ConfigValidationError) as e:
        log.error(
            "Config validation failed",
            path=str(config_path),
            error=str(e),
            exc_info=True,
            emoji_key="fail",
        )
        details_str = ""
        if hasattr(e, "__notes__"):
            notes = getattr(e, "__notes__", [])
            if notes:
                details_str = "\nDetails:\n" + "\n".join(notes)
        raise ConfigValidationError(
            f"Configuration validation failed: {e}{details_str}",
            path=str(config_path),
            details=e,
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
