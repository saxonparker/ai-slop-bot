"""Continue-button conversations through the Lambda entry point.

Reuses the `bot` fixture and `invoke` helper from test_balance_enforcement so
providers, Slack, usage, and balance are mocked the same way.
"""

import sys
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.append(".")

import ai_slop_bot  # noqa: E402  pylint: disable=wrong-import-position
import conversations  # noqa: E402  pylint: disable=wrong-import-position
from tests.test_balance_enforcement import bot, invoke  # noqa: E402,F401  pylint: disable=wrong-import-position,unused-import


@pytest.fixture
def conv(monkeypatch, bot):  # pylint: disable=redefined-outer-name
    monkeypatch.setenv("CONVERSATIONS_TABLE_NAME", "test-conversations")
    provider = bot.providers.get_text_provider.return_value
    provider.chat.return_value = provider.generate.return_value
    targets = {"create": None, "get": None, "append_turn": True, "new_id": "conv1"}
    with ExitStack() as stack:
        yield SimpleNamespace(**{
            name: stack.enter_context(patch(f"ai_slop_bot.conversations.{name}", return_value=value))
            for name, value in targets.items()
        })


HISTORY = [
    {"role": "user", "content": "talk about cats", "user": "alice"},
    {"role": "assistant", "content": "cats are nice"},
]


def stored(turn_count=1, **flags):
    return {
        "conversation_id": "conv1", "channel_id": "C", "created_by": "alice",
        "turn_count": turn_count, "messages": list(HISTORY),
        "flags": {"backend": "", "potato": False, "emoji": False, **flags},
    }


def continue_turn(prompt, **payload):
    invoke(prompt, source="conversation", conversation_id="conv1", **payload)


def posted(bot):
    bot.slack.post_text_response.assert_called_once()
    return bot.slack.post_text_response.call_args


# ── first turn ───────────────────────────────────────────────────────────────

def test_first_text_reply_stores_exchange_and_posts_continue_button(bot, conv):
    invoke("-p -b grok tell a joke [about dogs]")

    kwargs = conv.create.call_args.kwargs
    assert kwargs["conversation_id"] == "conv1"
    assert kwargs["channel_id"] == "C"
    assert kwargs["created_by"] == "bob"
    assert kwargs["flags"] == {"backend": "grok", "potato": True, "emoji": False}
    assert kwargs["user_msg"] == {"role": "user", "content": "tell a joke about dogs", "user": "bob"}
    assert kwargs["assistant_msg"] == {"role": "assistant", "content": "generated"}
    bot.slack.conversation_action.assert_called_once_with("conv1")
    call = posted(bot)
    assert call.args[:4] == ("https://hooks/x", "bob", "tell a joke", "generated")
    assert call.kwargs["actions"] is bot.slack.conversation_action.return_value


def test_emoji_flag_is_stored_for_later_turns(bot, conv):
    invoke("-e how are you")
    assert conv.create.call_args.kwargs["flags"]["emoji"] is True


def test_no_button_when_conversations_are_not_configured(bot, monkeypatch):
    monkeypatch.delenv("CONVERSATIONS_TABLE_NAME", raising=False)
    with patch("ai_slop_bot.conversations.create") as create:
        invoke("tell a joke")
    create.assert_not_called()
    assert posted(bot).kwargs["actions"] is None


def test_reply_still_posts_when_the_row_write_fails(bot, conv):
    conv.create.side_effect = RuntimeError("dynamodb down")
    invoke("tell a joke")
    assert posted(bot).kwargs["actions"] is None
    bot.slack.post_error.assert_not_called()


@pytest.mark.parametrize("prompt", ["-bufo hello there", "-i a cat", "-v a cat"])
def test_non_conversation_modes_store_nothing(bot, conv, prompt):
    invoke(prompt)
    conv.create.assert_not_called()
    bot.slack.conversation_action.assert_not_called()


def test_payment_reminder_replies_are_not_continuable(bot, conv):
    bot.balance.return_value = -5.0
    invoke("tell a joke")
    conv.create.assert_not_called()
    assert posted(bot).kwargs["actions"] is None


# ── continuation ─────────────────────────────────────────────────────────────

def test_continuation_replays_history_with_first_turn_flags(bot, conv):
    conv.get.return_value = stored(backend="grok", potato=True)
    with patch("ai_slop_bot.prompts.get_system_message", return_value="be spuddy") as system:
        continue_turn("more cats [make it rhyme]")

    conv.get.assert_called_once_with("conv1")
    bot.balance.assert_called_once_with("bob")
    system.assert_called_once_with("bob", True)
    bot.providers.get_text_provider.assert_called_once_with("grok")
    provider = bot.providers.get_text_provider.return_value
    provider.generate.assert_not_called()
    provider.chat.assert_called_once()
    assert provider.chat.call_args.args[0] == "be spuddy"
    assert provider.chat.call_args.args[1] == [
        {"role": "user", "content": "talk about cats"},
        {"role": "assistant", "content": "cats are nice"},
        {"role": "user", "content": "more cats make it rhyme"},
    ]
    bot.record.assert_called_once()
    assert bot.record.call_args.args[0] == "bob"
    conv.append_turn.assert_called_once_with(
        "conv1",
        {"role": "user", "content": "more cats make it rhyme", "user": "bob"},
        {"role": "assistant", "content": "generated"},
        1,
    )
    call = posted(bot)
    assert call.args[:4] == ("https://hooks/x", "bob", "more cats", "generated")
    assert call.kwargs["actions"] is bot.slack.conversation_action.return_value
    bot.slack.conversation_action.assert_called_once_with("conv1")


def test_continuation_reapplies_emoji_mode_and_default_backend(bot, conv):
    conv.get.return_value = stored(emoji=True)
    continue_turn("more cats")

    bot.providers.get_text_provider.assert_called_once_with(None)
    history = bot.providers.get_text_provider.return_value.chat.call_args.args[1]
    assert history[-1]["content"] == "more cats Respond only with emojis. No text."
    assert posted(bot).args[2] == "more cats"


def test_missing_conversation_gets_an_ephemeral_notice(bot, conv):
    conv.get.return_value = None
    continue_turn("more cats")

    assert bot.providers.mock_calls == []
    bot.balance.assert_not_called()
    bot.slack.post_text_response.assert_not_called()
    assert "no longer available" in bot.slack.post_ephemeral.call_args.args[1]


def test_lost_append_race_asks_the_user_to_retry(bot, conv):
    conv.get.return_value = stored()
    conv.append_turn.return_value = False
    continue_turn("more cats")

    bot.record.assert_called_once()
    bot.slack.post_text_response.assert_not_called()
    assert "try again" in bot.slack.post_ephemeral.call_args.args[1]


def test_last_allowed_turn_drops_the_button(bot, conv):
    conv.get.return_value = stored(turn_count=conversations.MAX_TURNS - 1)
    continue_turn("more cats")

    call = posted(bot)
    assert call.kwargs["actions"] is None
    assert "full" in call.args[3]
    assert call.args[3].startswith("generated")


def test_full_conversation_refuses_new_turns(bot, conv):
    conv.get.return_value = stored(turn_count=conversations.MAX_TURNS)
    continue_turn("more cats")

    assert bot.providers.mock_calls == []
    conv.append_turn.assert_not_called()
    assert "full" in bot.slack.post_ephemeral.call_args.args[1]


def test_cutoff_balance_blocks_a_continuation(bot, conv):
    conv.get.return_value = stored()
    bot.balance.return_value = -10.0
    continue_turn("more cats")

    assert bot.providers.mock_calls == []
    conv.append_turn.assert_not_called()
    assert "Pay Saxon money" in bot.slack.post_ephemeral.call_args.args[1]


def test_override_balance_sends_payment_prompt_without_touching_history(bot, conv):
    conv.get.return_value = stored(potato=True)
    bot.balance.return_value = -5.0
    continue_turn("more cats")

    provider = bot.providers.get_text_provider.return_value
    provider.chat.assert_not_called()
    provider.generate.assert_called_once()
    assert "pay saxon money" in provider.generate.call_args.args[1].lower()
    bot.record.assert_called_once()
    conv.append_turn.assert_not_called()
    call = posted(bot)
    assert call.args[2] == "more cats"
    assert call.kwargs["actions"] is None


def test_provider_failure_is_recorded_and_not_appended(bot, conv):
    conv.get.return_value = stored()
    bot.providers.get_text_provider.return_value.chat.side_effect = RuntimeError("boom")
    continue_turn("more cats")

    bot.failed.assert_called_once()
    assert bot.failed.call_args.kwargs["mode"] == "text"
    bot.record.assert_not_called()
    conv.append_turn.assert_not_called()
    bot.slack.post_text_response.assert_not_called()
    bot.slack.post_error.assert_called_once()
