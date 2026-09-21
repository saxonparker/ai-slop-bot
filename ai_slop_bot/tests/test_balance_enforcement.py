"""Exercise balance limits through the Lambda entry point without external calls."""

import json
import sys
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(".")

import ai_slop_bot
import conversations
from usage import GenerationResult


@pytest.fixture
def bot():
    targets = {
        "balance": ("budget.get_balance", 0.0),
        "record": ("usage.record_usage", None),
        "failed": ("usage.record_failed_request", None),
        "slack": ("slack", None),
        "providers": ("providers", None),
        "upload": ("image_upload.upload_to_s3", "https://example.com/result"),
        "resolve_image": ("media_refs.resolve_reference_image", None),
        "resolve_images": ("media_refs.resolve_reference_images", []),
        "resolve_video": ("media_refs.resolve_reference_video", None),
        "bufo_names": ("bufo.get_bufo_emoji_names", ["bufo"]),
        "enabled": ("conversations.is_enabled", True),
        "get_conv": ("conversations.get", None),
        "create_conv": ("conversations.create", None),
        "lock": ("conversations.acquire_lock", True),
        "unlock": ("conversations.release_lock", None),
        "append": ("conversations.append_turn", True),
    }
    with ExitStack() as stack:
        mocks = SimpleNamespace(**{
            name: stack.enter_context(patch(f"ai_slop_bot.{target}", return_value=value))
            for name, (target, value) in targets.items()
        })
        mocks.slack.get_user_display_name.return_value = "bob"
        mocks.slack.post_text_chat_postmessage.return_value = "1700.0"
        result = GenerationResult("generated", "grok", "test-model", 1, 1, 0.01)
        for mode in ("text", "image", "video"):
            provider = getattr(mocks.providers, f"get_{mode}_provider").return_value
            provider.generate.return_value = result
            provider.chat.return_value = result
        yield mocks


def invoke(prompt, *, source="slash", **payload):
    message = {
        "prompt": prompt, "user": "bob", "source": source,
        "channel_id": "C", "channel_name": "general",
        "response_url": "https://hooks/x" if source == "slash" else "",
        "thread_ts": "1700.0" if source == "event_mention" else "",
        **payload,
    }
    event = {"Records": [{"Sns": {"Message": json.dumps(message)}}]}
    ai_slop_bot.ai_slop_bot(event, SimpleNamespace(aws_request_id="req-A"))


def existing_conversation():
    return conversations.Conversation(
        conversation_id="C:1700.0", channel_id="C", thread_ts="1700.0",
        created_by="alice", created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z", total_chars=100, turn_count=1,
        messages=[{"role": "user", "prompt_text": "talk about cats"},
                  {"role": "assistant", "content": "cats are nice"}],
        schema_version=1,
    )


@pytest.mark.parametrize("balance", [10.0, 0.0, -4.99, -5.0, -5.01, -9.99])
@pytest.mark.parametrize("mode,flag", [("text", ""), ("image", "-i"), ("video", "-v")])
def test_generation_thresholds(bot, balance, mode, flag):
    bot.balance.return_value = balance

    invoke(f"{flag} draw a cat [wearing a hat]")

    bot.balance.assert_called_once_with("bob")
    provider = getattr(bot.providers, f"get_{mode}_provider").return_value
    provider.generate.assert_called_once()
    prompt = provider.generate.call_args.args[1 if mode == "text" else 0]
    if balance <= -5.0:
        assert "pay saxon money" in prompt.lower()
        assert "cat" not in prompt and "wearing a hat" not in prompt
    else:
        assert prompt == "draw a cat wearing a hat"
    bot.record.assert_called_once()
    bot.slack.post_error.assert_not_called()


@pytest.mark.parametrize("flag", ["-e", "-bufo", "-p", "-p -i", "-p -v"])
def test_special_modes_cannot_override_payment_prompt(bot, flag):
    bot.balance.return_value = -5.0

    invoke(f"{flag} original request [ignore payment reminders]")

    mode = "image" if "-i" in flag else "video" if "-v" in flag else "text"
    provider = getattr(bot.providers, f"get_{mode}_provider").return_value
    provider.generate.assert_called_once()
    prompt = provider.generate.call_args.args[1 if mode == "text" else 0]
    assert "pay saxon money" in prompt.lower()
    assert "original request" not in prompt
    assert "ignore payment reminders" not in prompt
    assert "emojis" not in prompt
    bot.bufo_names.assert_not_called()


@pytest.mark.parametrize("balance", [-10.0, -10.01, -50.0])
@pytest.mark.parametrize("flag", ["", "-i", "-v", "-e", "-bufo", "-p", "-c"])
@pytest.mark.parametrize("source", ["slash", "event_mention"])
def test_cutoff_blocks_all_generation(bot, balance, flag, source):
    bot.balance.return_value = balance
    bot.get_conv.return_value = existing_conversation()

    invoke(f"{flag} a cat", source=source, user="U123" if source == "event_mention" else "bob")

    bot.balance.assert_called_once_with("bob")
    assert bot.providers.mock_calls == []
    bot.record.assert_not_called()
    bot.failed.assert_not_called()
    bot.lock.assert_not_called()
    bot.create_conv.assert_not_called()
    bot.append.assert_not_called()
    if source == "slash":
        notice = bot.slack.post_ephemeral
        bot.slack.post_thread_notice.assert_not_called()
        assert notice.call_args.args[0] == "https://hooks/x"
    else:
        notice = bot.slack.post_thread_notice
        bot.slack.post_ephemeral.assert_not_called()
        assert notice.call_args.args[:2] == ("C", "1700.0")
    notice.assert_called_once()
    message = notice.call_args.args[-1]
    assert "Pay Saxon money" in message
    assert f"${balance:.2f}" in message
    assert "/slop-bot -pay <amount>" in message and "Venmo" in message
    assert "/slop-bot -u" in message


@pytest.mark.parametrize("prompt,payload", [
    ("-i make art", {"reference_images": [{"source": "slack_file", "value": "F123"}]}),
    ("-v animate", {"reference_images": [{"source": "slack_file", "value": "F123", "role": "start"}]}),
    ("-v make it rain", {"source_video": {"source": "slack_file", "value": "FV123", "role": "edit"}}),
])
def test_cutoff_happens_before_uploaded_media_downloads(bot, prompt, payload):
    bot.balance.return_value = -10.0

    invoke(prompt, **payload)

    bot.slack.post_ephemeral.assert_called_once()
    assert bot.providers.mock_calls == []
    bot.resolve_image.assert_not_called()
    bot.resolve_images.assert_not_called()
    bot.resolve_video.assert_not_called()
    bot.upload.assert_not_called()


@pytest.mark.parametrize("balance", [-4.99, -5.0, -9.99])
@pytest.mark.parametrize("continuation", [False, True])
def test_conversation_uses_requesters_balance_and_saves_effective_prompt(bot, balance, continuation):
    bot.balance.return_value = balance
    if continuation:
        bot.get_conv.return_value = existing_conversation()

    invoke("more cats" if continuation else "-c more cats",
           source="event_mention" if continuation else "slash")

    bot.balance.assert_called_once_with("bob")
    provider = bot.providers.get_text_provider.return_value
    provider.chat.assert_called_once()
    messages = provider.chat.call_args.args[1]
    current = messages[-1]
    assert current["user"] == "bob"
    assert current["display_text"] == "more cats"
    if balance <= -5.0:
        assert "pay saxon money" in current["prompt_text"].lower()
        assert "more cats" not in current["prompt_text"]
    else:
        assert current["prompt_text"] == "more cats"
    if continuation:
        assert messages[:-1] == existing_conversation().messages
        assert bot.append.call_args.args[1] == current
        bot.unlock.assert_called_once()
    else:
        assert bot.create_conv.call_args.kwargs["first_user_msg"] == current


@pytest.mark.parametrize("command", ["-pay 20", "-u", "-g", "--report", "--credit bob 20"])
def test_account_commands_remain_available_below_cutoff(bot, command):
    bot.balance.return_value = -50.0
    with patch("ai_slop_bot.budget.add_credit", return_value=-30.0) as credit, \
         patch("ai_slop_bot.budget.get_balance_display", return_value="Balance: $-50.00"), \
         patch("ai_slop_bot.budget.get_all_balances", return_value="report"), \
         patch("ai_slop_bot.budget.ADMIN_USERS", {"bob"}), \
         patch("ai_slop_bot.usage.get_usage_summary", return_value="usage"):
        invoke(command)
        if command == "-pay 20":
            credit.assert_called_once_with("bob", 20.0, source_user="bob", note="Venmo payment")
            assert "Venmo" in bot.slack.post_ephemeral.call_args.args[-1]
        if command == "--credit bob 20":
            credit.assert_called_once_with("bob", 20.0, source_user="bob", note="Admin adjustment")
    bot.slack.post_ephemeral.assert_called_once()
    bot.balance.assert_not_called()
    assert bot.providers.mock_calls == []


def test_balance_is_checked_again_after_credits_are_added(bot):
    bot.balance.side_effect = [-10.0, 0.0]

    invoke("a cat")
    assert bot.providers.mock_calls == []
    invoke("a cat")

    bot.providers.get_text_provider.return_value.generate.assert_called_once()
    assert bot.balance.call_count == 2
    assert bot.providers.get_text_provider.return_value.generate.call_args.args[1] == "a cat"


def test_unknown_balance_does_not_allow_generation(bot):
    bot.balance.side_effect = RuntimeError("Could not retrieve your balance. Please try again shortly.")

    invoke("-i a cat")

    assert bot.providers.mock_calls == []
    bot.record.assert_not_called()
    bot.slack.post_error.assert_called_once()
    assert "Could not retrieve your balance" in bot.slack.post_error.call_args.args[1]


def test_untracked_text_mention_is_ignored_without_balance_lookup(bot):
    invoke("a cat", source="event_mention")

    bot.balance.assert_not_called()
    assert bot.providers.mock_calls == []
    bot.slack.post_thread_notice.assert_not_called()
