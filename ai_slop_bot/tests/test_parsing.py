"""Tests for parsing module."""

import sys

sys.path.append(".")

import parsing


def test_basic_prompt():
    result = parsing.parse_command("a basic prompt")
    assert result.mode == "text"
    assert result.display_text == "a basic prompt"
    assert result.prompt_text == "a basic prompt"
    assert result.emoji_mode is False
    assert result.potato_mode is False
    assert result.backend_override is None
    assert result.usage is False


def test_directive():
    result = parsing.parse_command("a basic prompt [with a directive]")
    assert result.display_text == "a basic prompt"
    assert result.prompt_text == "a basic prompt with a directive"


def test_post_directive():
    result = parsing.parse_command("a basic prompt [with a directive] and text after")
    assert result.display_text == "a basic prompt and text after"
    assert result.prompt_text == "a basic prompt with a directive and text after"


def test_no_closing_bracket():
    result = parsing.parse_command("a basic prompt [with a directive and text after")
    assert result.display_text == "a basic prompt"
    assert result.prompt_text == "a basic prompt with a directive and text after"


def test_right_bracket_only():
    result = parsing.parse_command("a basic prompt with a directive] and text after")
    assert result.display_text == "a basic prompt with a directive] and text after"
    assert result.prompt_text == "a basic prompt with a directive and text after"


def test_emoji_mode():
    result = parsing.parse_command("-e a basic prompt")
    assert result.mode == "text"
    assert result.emoji_mode is True
    assert result.display_text == "a basic prompt"
    assert result.prompt_text == "a basic prompt Respond only with emojis. No text."


def test_image_mode():
    result = parsing.parse_command("-i a sunset")
    assert result.mode == "image"
    assert result.display_text == "a sunset"
    assert result.prompt_text == "a sunset"


def test_backend_override():
    result = parsing.parse_command("-b gemini hello world")
    assert result.mode == "text"
    assert result.backend_override == "gemini"
    assert result.display_text == "hello world"
    assert result.prompt_text == "hello world"


def test_image_with_backend():
    result = parsing.parse_command("-i -b openai a cat")
    assert result.mode == "image"
    assert result.backend_override == "openai"
    assert result.display_text == "a cat"
    assert result.prompt_text == "a cat"


def test_flags_any_order():
    result = parsing.parse_command("-b openai -i -e describe a cat")
    assert result.mode == "image"
    assert result.emoji_mode is True
    assert result.backend_override == "openai"
    assert result.display_text == "describe a cat"


def test_image_with_directive():
    result = parsing.parse_command("-i a sunset [in watercolor style]")
    assert result.mode == "image"
    assert result.display_text == "a sunset"
    assert result.prompt_text == "a sunset in watercolor style"


def test_potato_mode():
    result = parsing.parse_command("-p what is the meaning of life")
    assert result.mode == "text"
    assert result.potato_mode is True
    assert result.display_text == "what is the meaning of life"
    assert result.prompt_text == "what is the meaning of life"


def test_potato_image_mode():
    result = parsing.parse_command("-p -i a beautiful sunset")
    assert result.mode == "image"
    assert result.potato_mode is True
    assert result.display_text == "a beautiful sunset"


def test_video_mode():
    result = parsing.parse_command("-v a dancing cat")
    assert result.mode == "video"
    assert result.display_text == "a dancing cat"
    assert result.prompt_text == "a dancing cat"
    assert result.video_duration is None


def test_video_with_duration():
    result = parsing.parse_command("-v 5 a dancing cat")
    assert result.mode == "video"
    assert result.video_duration == 5
    assert result.display_text == "a dancing cat"
    assert result.prompt_text == "a dancing cat"


def test_video_with_backend():
    result = parsing.parse_command("-v -b grok a dancing cat")
    assert result.mode == "video"
    assert result.backend_override == "grok"
    assert result.video_duration is None


def test_video_with_duration_and_backend():
    result = parsing.parse_command("-v 15 -b grok a dancing cat")
    assert result.mode == "video"
    assert result.video_duration == 15
    assert result.backend_override == "grok"


def test_usage_short_flag():
    result = parsing.parse_command("-u")
    assert result.usage is True
    assert result.prompt_text == ""


def test_usage_long_flag():
    result = parsing.parse_command("--usage")
    assert result.usage is True


def test_usage_with_other_flags():
    result = parsing.parse_command("-i -u a prompt")
    assert result.usage is True
    assert result.mode == "image"
