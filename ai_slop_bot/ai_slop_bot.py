"""Main Lambda handler for ai-slop bot. Routes text vs image generation."""

import json
import sys
import time
import traceback

import budget
import conversations
import image_upload
import parsing
import prompts
import providers
import slack
import usage


def ai_slop_bot(event, context):
    """Entry point for the Lambda that generates text or images."""
    response_url = None
    lambda_start = time.time()
    try:
        # pylint: disable=broad-except
        print(f"SNS MESSAGE: {event['Records'][0]['Sns']['Message']}")
        message = json.loads(event["Records"][0]["Sns"]["Message"])
        response_url = message["response_url"]
        input_str = message["prompt"]
        user = message["user"]
        channel_id = message.get("channel_id", "")
        channel_name = message.get("channel_name", "")
        thread_ts = message.get("thread_ts", "") or ""

        parsed = parsing.parse_command(input_str)

        if parsed.usage:
            summary = usage.get_usage_summary(user)
            balance_info = budget.get_balance_display(user)
            if isinstance(summary, list):
                blocks = summary + [{"type": "section", "text": {"type": "mrkdwn", "text": balance_info}}]
                slack.post_ephemeral(response_url, blocks=blocks)
            else:
                slack.post_ephemeral(response_url, summary + "\n" + balance_info)
            return

        if parsed.gallery:
            slack.post_ephemeral(
                response_url,
                ":frame_with_picture: <https://d2jagmvo7k5q5j.cloudfront.net/index.html|AI Slop Gallery>",
            )
            return

        if parsed.pay_amount is not None:
            amount = parsed.pay_amount
            budget.add_credit(user, amount, source_user=user, note="Venmo payment")
            link = budget.generate_venmo_link(amount)
            slack.post_ephemeral(
                response_url,
                f":white_check_mark: Credited *${amount:.2f}* to your balance.\n"
                f"Pay here: <{link}|Pay ${amount:.2f} on Venmo>",
            )
            return

        if parsed.report:
            if user not in budget.ADMIN_USERS:
                slack.post_ephemeral(response_url, "Only admins can use -report.")
                return
            slack.post_ephemeral(response_url, budget.get_all_balances())
            return

        if parsed.credit_target is not None:
            if user not in budget.ADMIN_USERS:
                slack.post_ephemeral(response_url, "Only admins can use -credit.")
                return
            target = parsed.credit_target
            amount = parsed.credit_amount
            new_bal = budget.add_credit(target, amount, source_user=user,
                                        note="Admin adjustment")
            slack.post_ephemeral(
                response_url,
                f"Adjusted *{target}* by *${amount:.2f}*. New balance: *${new_bal:.2f}*",
            )
            return

        if parsed.conversation and parsed.mode in ("image", "video"):
            label = "-i" if parsed.mode == "image" else "-v"
            slack.post_error(
                response_url,
                f"-c cannot be combined with {label}: conversations are text-only.",
            )
            return

        if parsed.mode == "video":
            prompt = prompts.sanitize_prompt(parsed.prompt_text, user, parsed.potato_mode)
            print(f"GENERATE VIDEO: {prompt}")
            provider = providers.get_video_provider(parsed.backend_override)
            result = provider.generate(prompt, duration=parsed.video_duration)
            print("GENERATE VIDEO COMPLETE")
            image_upload.upload_to_s3(prompt, result.content, extension="mp4",
                                     user=user, channel=channel_name,
                                     model=result.model)
            slack.post_video_response(channel_id, user, parsed.display_text, result.content)
            usage.record_usage(user, result)
            return

        if parsed.mode == "image":
            prompt = prompts.sanitize_prompt(parsed.prompt_text, user, parsed.potato_mode)
            print(f"GENERATE IMAGE: {prompt}")
            provider = providers.get_image_provider(parsed.backend_override)
            result = provider.generate(prompt)
            print("GENERATE IMAGE COMPLETE")
            url = image_upload.upload_to_s3(prompt, result.content,
                                          user=user, channel=channel_name,
                                          model=result.model)
            print(f"UPLOAD URL {url}")
            slack.post_image_response(response_url, user, parsed.display_text, url)
            usage.record_usage(user, result)
            return

        # Text mode: route to conversation handler if applicable.
        conv_enabled = conversations.is_enabled()
        if parsed.conversation and not conv_enabled:
            slack.post_ephemeral(
                response_url,
                "Conversations are not enabled in this environment.",
            )
            return

        existing_conv = None
        if conv_enabled and thread_ts:
            existing_conv = conversations.get(conversations.make_id(channel_id, thread_ts))

        if existing_conv is not None or parsed.conversation:
            if existing_conv is not None and parsed.conversation:
                slack.post_ephemeral(
                    response_url,
                    "This thread is already a conversation — continuing the existing"
                    " one rather than starting a new conversation.",
                )
            request_id = getattr(context, "aws_request_id", None) or "local"
            _handle_conversation_turn(
                parsed=parsed, user=user, channel_id=channel_id,
                response_url=response_url, thread_ts=thread_ts,
                existing_conv=existing_conv, request_id=request_id,
                lambda_start=lambda_start,
            )
            return

        # Single-shot text path.
        system = prompts.get_system_message(user, parsed.potato_mode)
        print(f"GENERATE TEXT: {system}, {parsed.prompt_text}")
        provider = providers.get_text_provider(parsed.backend_override)
        result = provider.generate(system, parsed.prompt_text)
        print(f"GENERATE TEXT COMPLETE: {result.content}")
        slack.post_text_response(response_url, user, parsed.display_text, result.content)
        usage.record_usage(user, result)

    except Exception as exc:
        print("COMMAND ERROR: " + str(exc))
        traceback.print_exc()
        if response_url:
            slack.post_error(response_url, str(exc))
    # pylint: enable=broad-except


def _handle_conversation_turn(*, parsed, user, channel_id, response_url, thread_ts,
                              existing_conv, request_id, lambda_start):
    """Dispatch to first-turn (top-level or in-thread) or continuation-turn handler."""
    if existing_conv is None:
        _handle_first_turn(
            parsed=parsed, user=user, channel_id=channel_id,
            response_url=response_url, thread_ts=thread_ts or None,
            lambda_start=lambda_start,
        )
        return

    _handle_continuation_turn(
        parsed=parsed, user=user, response_url=response_url,
        thread_ts=existing_conv.thread_ts, existing_conv=existing_conv,
        request_id=request_id, lambda_start=lambda_start,
    )


def _handle_first_turn(*, parsed, user, channel_id, response_url, thread_ts, lambda_start):
    """First turn of a conversation. thread_ts=None → top-level (chat.postMessage to mint ts)."""
    effective_system = prompts.get_system_message(user, parsed.potato_mode)
    user_msg = conversations.build_user_message(
        prompt_text=parsed.prompt_text,
        display_text=parsed.display_text,
        user=user,
        backend=parsed.backend_override or "",
        potato=parsed.potato_mode,
    )
    if (time.time() - lambda_start) > conversations.IN_HANDLER_ABORT_SECONDS:
        slack.post_error(response_url, "Aborting before model call to avoid Lambda timeout.")
        return
    provider = providers.get_text_provider(parsed.backend_override)
    print(f"GENERATE TEXT (conv turn 1): {parsed.prompt_text}")
    result = provider.chat(effective_system, [user_msg])
    print(f"GENERATE TEXT COMPLETE: {result.content}")
    user_msg["backend"] = result.backend
    assistant_msg = conversations.build_assistant_message(result)
    footer = slack.conversation_started_footer(result.backend)

    if thread_ts:
        # In-thread: thread_ts known; create row first, then post via response_url.
        conv_id = conversations.make_id(channel_id, thread_ts)
        try:
            conversations.create(
                conversation_id=conv_id,
                channel_id=channel_id,
                thread_ts=thread_ts,
                created_by=user,
                first_user_msg=user_msg,
                first_assistant_msg=assistant_msg,
            )
        except conversations.ConversationAlreadyExists:
            slack.post_error(
                response_url,
                "Another turn started this conversation first; please retry.",
            )
            return
        slack.post_text_response_in_thread(
            response_url=response_url, user=user, display=parsed.display_text,
            response=result.content, thread_ts=thread_ts, footer_blocks=[footer],
        )
    else:
        # Top-level: chat.postMessage first to mint thread_ts, then create row.
        ts = slack.post_text_chat_postmessage(
            channel_id=channel_id, user=user, display=parsed.display_text,
            response=result.content, thread_ts=None, footer_blocks=[footer],
        )
        conv_id = conversations.make_id(channel_id, ts)
        try:
            conversations.create(
                conversation_id=conv_id,
                channel_id=channel_id,
                thread_ts=ts,
                created_by=user,
                first_user_msg=user_msg,
                first_assistant_msg=assistant_msg,
            )
        except Exception:
            # Response already posted to a fresh thread; warn into the thread
            # so users don't expect continuations to work.
            slack.post_thread_notice(
                channel_id=channel_id, thread_ts=ts,
                text=(
                    ":warning: Could not start conversation tracking — replies in"
                    " this thread won't continue the conversation."
                ),
            )
            usage.record_usage(user, result)
            raise

    usage.record_usage(user, result)


def _handle_continuation_turn(*, parsed, user, response_url, thread_ts, existing_conv,
                              request_id, lambda_start):
    """Continuation turn: lock, preflight, replay history, atomic append, post."""
    conv_id = existing_conv.conversation_id
    acquired = conversations.acquire_lock(conv_id, request_id)
    if not acquired:
        time.sleep(conversations.LOCK_RETRY_SLEEP_SECONDS)
        acquired = conversations.acquire_lock(conv_id, request_id)
    if not acquired:
        slack.post_ephemeral(
            response_url,
            "Another turn in this conversation is in flight; try again in a few seconds.",
        )
        return

    try:
        conv = conversations.get(conv_id, consistent=True) or existing_conv
        new_chars = len(parsed.prompt_text)
        projected = conv.total_chars + new_chars

        # Reserve worst-case assistant response so we never start a turn that
        # could push the persisted transcript past CONVERSATION_MAX_CHARS.
        if (projected + conversations.ASSISTANT_RESERVE_CHARS
                > conversations.CONVERSATION_MAX_CHARS
                or conv.turn_count >= conversations.MAX_TURNS):
            slack.post_error(
                response_url,
                "This conversation has reached its limit. "
                "Start a new one with `/slop-bot -c <prompt>`.",
            )
            return

        soft_warn_threshold = int(
            conversations.SOFT_WARN_FRACTION * conversations.CONVERSATION_MAX_CHARS
        )
        soft_warn = projected >= soft_warn_threshold

        effective_system = prompts.get_system_message(user, parsed.potato_mode)
        user_msg = conversations.build_user_message(
            prompt_text=parsed.prompt_text,
            display_text=parsed.display_text,
            user=user,
            backend=parsed.backend_override or "",
            potato=parsed.potato_mode,
        )
        api_messages = list(conv.messages) + [user_msg]

        if (time.time() - lambda_start) > conversations.IN_HANDLER_ABORT_SECONDS:
            slack.post_error(
                response_url, "Aborting before model call to avoid Lambda timeout.",
            )
            return

        provider = providers.get_text_provider(parsed.backend_override)
        print(f"GENERATE TEXT (conv turn {conv.turn_count + 1}): {parsed.prompt_text}")
        result = provider.chat(effective_system, api_messages)
        print(f"GENERATE TEXT COMPLETE: {result.content}")
        user_msg["backend"] = result.backend
        assistant_msg = conversations.build_assistant_message(result)
        added = len(parsed.prompt_text) + len(result.content)

        appended = conversations.append_turn(
            conv_id, user_msg, assistant_msg, added, conv.turn_count,
        )
        if not appended:
            print(f"PHANTOM TURN DROPPED: {conv_id} (turn_count moved)")
            slack.post_error(
                response_url,
                "This conversation was modified by another in-flight turn; please retry.",
            )
            return

        footer_blocks = None
        if soft_warn:
            pct = int(100 * projected / conversations.CONVERSATION_MAX_CHARS)
            footer_blocks = [{
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": (
                        f":warning: This conversation is approaching its size cap "
                        f"(~{pct}% used). New turns will be refused at 100%."
                    ),
                }],
            }]
        slack.post_text_response_in_thread(
            response_url=response_url, user=user, display=parsed.display_text,
            response=result.content, thread_ts=thread_ts, footer_blocks=footer_blocks,
        )
        usage.record_usage(user, result)
    finally:
        conversations.release_lock(conv_id, request_id)


def main():
    """Process the command given on the command line."""
    input_str = " ".join(sys.argv[1:])
    parsed = parsing.parse_command(input_str)
    print(f"Mode: {parsed.mode}")
    print(f"Display: {parsed.display_text}")
    print(f"Prompt: {parsed.prompt_text}")

    if parsed.usage:
        summary = usage.get_usage_summary("cli")
        if isinstance(summary, list):
            for block in summary:
                print(block["text"]["text"])
        else:
            print(summary)
        return

    if parsed.mode == "video":
        prompt = prompts.sanitize_prompt(parsed.prompt_text, "cli", parsed.potato_mode)
        provider = providers.get_video_provider(parsed.backend_override)
        result = provider.generate(prompt, duration=parsed.video_duration)
        outfile = "/tmp/claude-1000/ai_slop_output.mp4"
        with open(outfile, "wb") as f:
            f.write(result.content)
        print(f"Video saved to {outfile}")
    elif parsed.mode == "image":
        prompt = prompts.sanitize_prompt(parsed.prompt_text, "cli", parsed.potato_mode)
        provider = providers.get_image_provider(parsed.backend_override)
        result = provider.generate(prompt)
        outfile = "/tmp/claude-1000/ai_slop_output.png"
        with open(outfile, "wb") as f:
            f.write(result.content)
        print(f"Image saved to {outfile}")
    else:
        system = prompts.get_system_message("cli", parsed.potato_mode)
        provider = providers.get_text_provider(parsed.backend_override)
        result = provider.generate(system, parsed.prompt_text)
        print(result.content)


if __name__ == "__main__":
    main()
