#
# tests/unit/test_rules.py
#
"""
Comprehensive tests for the rule engine.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from supsrc.config.models import (
    InactivityRuleConfig,
    ManualRuleConfig,
    SaveCountRuleConfig,
)
from supsrc.rules import check_inactivity, check_save_count, check_trigger_condition
from supsrc.state import RepositoryState


class TestInactivityRule:
    """Test inactivity rule functionality."""

    def test_no_last_change_time(self) -> None:
        """Test inactivity check with no last change time."""
        state = RepositoryState(repo_id="test-repo")
        rule_config = InactivityRuleConfig(period=timedelta(seconds=30))

        result = check_inactivity(state, rule_config)

        assert result is False

    def test_period_not_elapsed(self) -> None:
        """Test inactivity check when period has not elapsed."""
        state = RepositoryState(repo_id="test-repo")
        state.last_change_time = datetime.now(UTC) - timedelta(seconds=10)
        rule_config = InactivityRuleConfig(period=timedelta(seconds=30))

        result = check_inactivity(state, rule_config)

        assert result is False

    def test_period_elapsed(self) -> None:
        """Test inactivity check when period has elapsed."""
        state = RepositoryState(repo_id="test-repo")
        state.last_change_time = datetime.now(UTC) - timedelta(seconds=60)
        rule_config = InactivityRuleConfig(period=timedelta(seconds=30))

        result = check_inactivity(state, rule_config)

        assert result is True

    def test_exact_period_boundary(self) -> None:
        """Test inactivity check at exact period boundary."""
        state = RepositoryState(repo_id="test-repo")
        state.last_change_time = datetime.now(UTC) - timedelta(seconds=30)
        rule_config = InactivityRuleConfig(period=timedelta(seconds=30))

        result = check_inactivity(state, rule_config)

        assert result is True


class TestSaveCountRule:
    """Test save count rule functionality."""

    def test_count_not_reached(self) -> None:
        """Test save count check when count has not been reached."""
        state = RepositoryState(repo_id="test-repo")
        state.save_count = 5
        rule_config = SaveCountRuleConfig(count=10)

        result = check_save_count(state, rule_config)

        assert result is False

    def test_count_reached(self) -> None:
        """Test save count check when count has been reached."""
        state = RepositoryState(repo_id="test-repo")
        state.save_count = 10
        rule_config = SaveCountRuleConfig(count=10)

        result = check_save_count(state, rule_config)

        assert result is True

    def test_count_exceeded(self) -> None:
        """Test save count check when count has been exceeded."""
        state = RepositoryState(repo_id="test-repo")
        state.save_count = 15
        rule_config = SaveCountRuleConfig(count=10)

        result = check_save_count(state, rule_config)

        assert result is True

    def test_zero_count(self) -> None:
        """Test save count check with zero saves."""
        state = RepositoryState(repo_id="test-repo")
        state.save_count = 0
        rule_config = SaveCountRuleConfig(count=1)

        result = check_save_count(state, rule_config)

        assert result is False


class TestTriggerConditionCheck:
    """Test the main trigger condition check function."""

    def test_inactivity_rule_integration(self) -> None:
        """Test inactivity rule through main trigger check."""
        state = RepositoryState(repo_id="test-repo")
        state.last_change_time = datetime.now(UTC) - timedelta(seconds=60)

        repo_config = Mock()
        repo_config.rule = InactivityRuleConfig(period=timedelta(seconds=30))

        result = check_trigger_condition(state, repo_config)

        assert result is True

    def test_save_count_rule_integration(self) -> None:
        """Test save count rule through main trigger check."""
        state = RepositoryState(repo_id="test-repo")
        state.save_count = 10

        repo_config = Mock()
        repo_config.rule = SaveCountRuleConfig(count=10)

        result = check_trigger_condition(state, repo_config)

        assert result is True

    def test_manual_rule_integration(self) -> None:
        """Test manual rule through main trigger check."""
        state = RepositoryState(repo_id="test-repo")
        state.save_count = 100  # Even with high count

        repo_config = Mock()
        repo_config.rule = ManualRuleConfig()

        result = check_trigger_condition(state, repo_config)

        assert result is False

    def test_unknown_rule_type(self) -> None:
        """Test handling of unknown rule types."""
        state = RepositoryState(repo_id="test-repo")

        # Create a mock rule that doesn't match known types
        unknown_rule = Mock()
        unknown_rule.type = "unknown.rule.type"

        repo_config = Mock()
        repo_config.rule = unknown_rule

        result = check_trigger_condition(state, repo_config)

        assert result is False


class TestRuleEdgeCases:
    """Test edge cases and error conditions in rules."""

    def test_negative_save_count_config(self) -> None:
        """Test that negative save counts are handled properly."""
        # This should be caught at config validation level
        with pytest.raises(ValueError):
            SaveCountRuleConfig(count=-1)

    def test_zero_save_count_config(self) -> None:
        """Test that zero save counts are handled properly."""
        # This should be caught at config validation level
        with pytest.raises(ValueError):
            SaveCountRuleConfig(count=0)

    def test_zero_inactivity_period(self) -> None:
        """Test zero inactivity period handling."""
        state = RepositoryState(repo_id="test-repo")
        state.last_change_time = datetime.now(UTC)

        # Zero period should trigger immediately
        rule_config = InactivityRuleConfig(period=timedelta(seconds=0))

        result = check_inactivity(state, rule_config)

        assert result is True

    def test_very_large_save_count(self) -> None:
        """Test handling of very large save counts."""
        state = RepositoryState(repo_id="test-repo")
        state.save_count = 999999

        rule_config = SaveCountRuleConfig(count=1000000)

        result = check_save_count(state, rule_config)

        assert result is False

    def test_future_last_change_time(self) -> None:
        """Test handling of future last change time."""
        state = RepositoryState(repo_id="test-repo")
        # Set time in the future
        state.last_change_time = datetime.now(UTC) + timedelta(seconds=60)

        rule_config = InactivityRuleConfig(period=timedelta(seconds=30))

        # Should handle gracefully (likely return False)
        result = check_inactivity(state, rule_config)

        assert result is False

# 🧪⚡
