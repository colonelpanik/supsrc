#
# supsrc/telemetry/logger/processors.py
#
"""
Custom structlog processors for supsrc.
"""
import logging


# --- Helper ---
def get_emoji(event_dict: dict) -> str:
    from supsrc.telemetry.logger.base import LOG_EMOJIS
    """Gets appropriate emoji based on level or extra key in event_dict."""
    # Check for explicit emoji_key passed in log call
    if event_dict.get("emoji_key") in LOG_EMOJIS:
        return LOG_EMOJIS[event_dict["emoji_key"]]
    # Check for level mapping
    level = event_dict.get("level")
    level_num = getattr(logging, level.upper(), 0) if isinstance(level, str) else 0
    if level_num in LOG_EMOJIS:
        return LOG_EMOJIS[level_num]
    return LOG_EMOJIS.get("general", "➡️") # Fallback

# --- Custom Processors ---

def add_emoji_processor(logger, method_name: str, event_dict: dict) -> dict:
    """Adds an 'emoji' field based on level or emoji_key."""
    event_dict["emoji"] = get_emoji(event_dict)
    return event_dict

# def add_padded_logger_processor(logger, method_name: str, event_dict: dict) -> dict:
#     from supsrc.telemetry.logger.base import BASE_LOGGER_NAME
#     """Adds a 'padded_logger' field, truncating/padding the original logger name."""
#     logger_name = event_dict.get("logger", "unknown")
#     # Make relative to base if possible
#     if logger_name.startswith(BASE_LOGGER_NAME + "."):
#         logger_name = logger_name[len(BASE_LOGGER_NAME) + 1:]
#
#     if len(logger_name) > 32:
#         logger_name = "..." + logger_name[-29:]
#     padded_name = f"{logger_name:<32}"
#     event_dict["padded_logger"] = padded_name
#     return event_dict

def remove_extra_keys_processor(logger, method_name: str, event_dict: dict) -> dict:
    """Removes keys used only for internal processing (like emoji_key)."""
    event_dict.pop("emoji_key", None)
    return event_dict
