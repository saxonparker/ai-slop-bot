"""Tests for prompts module."""

import sys

sys.path.append(".")

import prompts


def test_system_message_default():
    msg = prompts.get_system_message("someuser")
    assert "helpful assistant" in msg
    assert "literally" in msg


def test_system_message_corn():
    msg = prompts.get_system_message("matthew.moskowitz9")
    assert "corn" in msg


def test_sanitize_prompt_no_manipulation():
    result = prompts.sanitize_prompt("a cat", "someuser")
    assert result == "a cat"


def test_sanitize_prompt_corn_user():
    result = prompts.sanitize_prompt("a cat", "matthew.moskowitz9")
    assert "a cat" in result
    # Should have some corn-related manipulation
    assert result != "a cat"


def test_get_user_specific_manipulations_empty():
    manips = prompts.get_user_specific_manipulations("someuser")
    assert len(manips) == 0


def test_get_user_specific_manipulations_corn():
    manips = prompts.get_user_specific_manipulations("matthew.moskowitz9")
    assert len(manips) > 0
