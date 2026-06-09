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


# ── Pay flag ────────────────────────────────────────────────────────────────

def test_pay_amount():
    result = parsing.parse_command("-pay 5.00")
    assert result.pay_amount == 5.00
    assert result.prompt_text == ""


def test_pay_amount_integer():
    result = parsing.parse_command("-pay 10")
    assert result.pay_amount == 10.0


def test_pay_with_other_flags():
    result = parsing.parse_command("-pay 3.50 -u")
    assert result.pay_amount == 3.50
    assert result.usage is True


def test_pay_invalid_amount():
    result = parsing.parse_command("-pay notanumber")
    assert result.pay_amount is None
    assert "notanumber" in result.prompt_text


# ── Credit flag ─────────────────────────────────────────────────────────────

def test_credit_user_amount():
    result = parsing.parse_command("-credit testuser 5.00")
    assert result.credit_target == "testuser"
    assert result.credit_amount == 5.00


def test_credit_negative_adjustment():
    result = parsing.parse_command("-credit testuser -2.50")
    assert result.credit_target == "testuser"
    assert result.credit_amount == -2.50


def test_credit_invalid_amount():
    result = parsing.parse_command("-credit testuser notanumber")
    assert result.credit_target is None
    assert result.credit_amount is None


# ── Conversation flag ───────────────────────────────────────────────────────

def test_conversation_short_flag():
    result = parsing.parse_command("-c hello world")
    assert result.conversation is True
    assert result.mode == "text"
    assert result.display_text == "hello world"
    assert result.prompt_text == "hello world"


def test_conversation_long_flag():
    result = parsing.parse_command("--conversation hello")
    assert result.conversation is True
    assert result.prompt_text == "hello"


def test_conversation_position_invariant():
    result = parsing.parse_command("hello world -c")
    assert result.conversation is True
    assert result.prompt_text == "hello world"


def test_conversation_with_potato():
    result = parsing.parse_command("-c -p hello")
    assert result.conversation is True
    assert result.potato_mode is True


def test_conversation_with_backend():
    result = parsing.parse_command("-c -b anthropic hello")
    assert result.conversation is True
    assert result.backend_override == "anthropic"


def test_conversation_distinguished_from_credit():
    result = parsing.parse_command("-credit testuser 5.00")
    assert result.conversation is False
    assert result.credit_target == "testuser"
    assert result.credit_amount == 5.00


def test_default_conversation_false():
    result = parsing.parse_command("plain prompt")
    assert result.conversation is False


# ── Reverse-bracket (channel-only) syntax ───────────────────────────────────

def test_reverse_directive():
    result = parsing.parse_command("hello ]extra context[")
    assert result.display_text == "hello extra context"
    assert result.prompt_text == "hello"


def test_reverse_with_text_after():
    result = parsing.parse_command("a ]b[ c")
    assert result.display_text == "a b c"
    assert result.prompt_text == "a c"


def test_reverse_only():
    result = parsing.parse_command("]channel only[")
    assert result.display_text == "channel only"
    assert result.prompt_text == ""


def test_both_bracket_syntaxes():
    result = parsing.parse_command("hi [hidden] mid ]shown[ end")
    assert result.display_text == "hi mid shown end"
    assert result.prompt_text == "hi hidden mid end"


def test_reverse_with_emoji_flag():
    result = parsing.parse_command("-e hi ]aside[")
    assert result.emoji_mode is True
    assert result.display_text == "hi aside"
    assert result.prompt_text == "hi Respond only with emojis. No text."


# ── Reference image flags ───────────────────────────────────────────────────

def test_image_edit_reference_url():
    result = parsing.parse_command("-i --edit https://example.com/cat.png make it watercolor")
    assert result.mode == "image"
    assert result.prompt_text == "make it watercolor"
    assert len(result.reference_images) == 1
    assert result.reference_images[0].role == "edit"
    assert result.reference_images[0].value == "https://example.com/cat.png"


def test_video_start_reference_url():
    result = parsing.parse_command("-v --start https://example.com/frame.jpg slow push in")
    assert result.mode == "video"
    assert result.prompt_text == "slow push in"
    assert result.source_image.role == "start"
    assert result.source_image.value == "https://example.com/frame.jpg"


def test_repeatable_reference_urls_and_slack_escaped_url():
    result = parsing.parse_command(
        "-v --ref <https://example.com/a.png|a> --ref https://example.com/b.png combine"
    )
    assert result.mode == "video"
    assert result.prompt_text == "combine"
    assert [ref.value for ref in result.reference_images] == [
        "https://example.com/a.png",
        "https://example.com/b.png",
    ]


def test_upload_flag():
    result = parsing.parse_command("-i --upload make something")
    assert result.upload_requested is True
    assert result.prompt_text == "make something"


def test_upload_flag_accepts_ios_smart_dash():
    result = parsing.parse_command("-i \u2014upload make something")
    assert result.upload_requested is True
    assert result.prompt_text == "make something"
