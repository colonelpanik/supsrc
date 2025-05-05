#
# supsrc/rules.py
#
"""
Implements the rule engine logic for supsrc triggers.

Determines if configured conditions (e.g., inactivity, save count) are met
based on the current state of a repository.
"""

from datetime import UTC, datetime

import structlog

# Use ABSOLUTE imports based on the 'src' layout
# --- Import the RENAMED config model classes ---
from supsrc.config.models import (
    # Import the specific rule config classes using their new names
    InactivityRuleConfig,
    ManualRuleConfig,  # Import the Union type alias if needed, but specific types are used below
    RepositoryConfig,
    SaveCountRuleConfig,
)
from supsrc.state import RepositoryState
from supsrc.telemetry import StructLogger  # Assuming this type hint exists

# Logger specific to the rule engine
log: StructLogger = structlog.get_logger("rules")

# --- Helper Functions (Specific Checkers) ---

def check_inactivity(
    repo_state: RepositoryState, rule_config: InactivityRuleConfig
) -> bool:
    """
    Checks if the inactivity period has elapsed since the last change.

    Args:
        repo_state: The current dynamic state of the repository.
        rule_config: The specific InactivityRuleConfig object.

    Returns:
        True if the inactivity period has been met or exceeded, False otherwise.
    """
    repo_id = repo_state.repo_id
    last_change_time_utc = repo_state.last_change_time

    if last_change_time_utc is None:
        log.debug("Inactivity check: No last change time recorded, condition false.", repo_id=repo_id)
        return False

    now_utc = datetime.now(UTC)
    elapsed_time = now_utc - last_change_time_utc
    required_period = rule_config.period

    log.debug(
        "Checking inactivity period",
        repo_id=repo_id,
        last_change_utc=last_change_time_utc.isoformat(),
        current_time_utc=now_utc.isoformat(),
        elapsed_seconds=elapsed_time.total_seconds(),
        required_period_seconds=required_period.total_seconds(),
    )
    return elapsed_time >= required_period


def check_save_count(
    repo_state: RepositoryState, rule_config: SaveCountRuleConfig
) -> bool:
    """
    Checks if the number of saves meets or exceeds the configured count.

    Args:
        repo_state: The current dynamic state of the repository.
        rule_config: The specific SaveCountRuleConfig object.

    Returns:
        True if the save count has been met or exceeded, False otherwise.
    """
    repo_id = repo_state.repo_id
    current_saves = repo_state.save_count
    required_saves = rule_config.count

    log.debug(
        "Checking save count",
        repo_id=repo_id,
        current_saves=current_saves,
        required_saves=required_saves,
    )
    return current_saves >= required_saves

# --- Main Rule Checking Function ---

def check_trigger_condition(
    repo_state: RepositoryState, repo_config: RepositoryConfig
) -> bool:
    """
    Checks if the configured trigger condition for the repository is met.

    Delegates to specific checking functions based on the rule type.

    Args:
        repo_state: The current dynamic state of the repository.
        repo_config: The static configuration for the repository.

    Returns:
        True if the trigger condition is met, False otherwise.
    """
    # Access the structured rule object from the repository config
    rule_config_obj = repo_config.rule
    repo_id = repo_state.repo_id # For logging context

    rule_type_str = getattr(rule_config_obj, "type", "unknown_rule_type") # Get type string
    log.debug("Checking trigger condition", repo_id=repo_id, rule_type=rule_type_str)

    # Match against the specific *structured* rule config object types
    match rule_config_obj:
        case InactivityRuleConfig(): # <<< Use new class name
            result = check_inactivity(repo_state, rule_config_obj)
            log.debug("Inactivity check result", repo_id=repo_id, result=result)
            return result
        case SaveCountRuleConfig(): # <<< Use new class name
            result = check_save_count(repo_state, rule_config_obj)
            log.debug("Save count check result", repo_id=repo_id, result=result)
            return result
        case ManualRuleConfig(): # <<< Use new class name
            log.debug("Manual rule configured, condition always false for automation.", repo_id=repo_id)
            return False
        case _:
            # This case should ideally be less likely now due to cattrs structuring hook,
            # but good to keep as a fallback.
            log.error(
                "Unsupported rule configuration object encountered in rule engine",
                repo_id=repo_id,
                rule_config_type=type(rule_config_obj).__name__,
                rule_config_obj=rule_config_obj, # Log the object itself
            )
            return False

    log.debug("Rule check completed", repo_id=repo_id, rule_type=rule_type_str, result=final_result)

# 🔼⚙️
