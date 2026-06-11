"""Main Lambda handler for ai-slop bot. Routes text vs image generation."""

import dataclasses
import json
import os
import sys
import time
import traceback

import budget
import conversations
import image_upload
import media_refs
import parsing
import prompts
import providers
import slack
import usage


def ai_slop_bot(event, context):
    """Entry point for the Lambda that generates text or images."""
    response_url = None
    source = "slash"
    channel_id = ""
    thread_ts = ""
    lambda_start = time.time()
    try:
        # pylint: disable=broad-except
        print(f"SNS MESSAGE: {event['Records'][0]['Sns']['Message']}")
        message = json.loads(event["Records"][0]["Sns"]["Message"])
        response_url = message.get("response_url", "")
        input_str = message["prompt"]
        user = message["user"]
        channel_id = message.get("channel_id", "")
        channel_name = message.get("channel_name", "")
        thread_ts = message.get("thread_ts", "") or ""
        source = message.get("source", "slash")

        if source == "event_mention":
            event_user_id = message.get("event_user_id", "") or user
            user = slack.get_user_display_name(event_user_id)

        parsed = parsing.parse_command(input_str)
        payload_references = [
            media_refs.ReferenceImage.from_payload(ref)
            for ref in message.get("reference_images", [])
        ]
        source_ref, reference_refs = _collect_media_references(parsed, payload_references)
        validation_error = _validate_media_references(parsed, source_ref, reference_refs)
        if validation_error:
            _notify(validation_error, source=source, response_url=response_url,
                    channel_id=channel_id, thread_ts=thread_ts)
            return

        if parsed.upload_requested:
            _notify("--upload can only be used from the slash command composer.",
                    source=source, response_url=response_url,
                    channel_id=channel_id, thread_ts=thread_ts)
            return

        # Events have no concept of "starting a conversation" — the thread
        # already exists. Strip the -c flag so downstream routing only
        # branches on existing_conv.
        if source == "event_mention" and parsed.conversation:
            parsed = dataclasses.replace(parsed, conversation=False)

        if parsed.usage:
            summary = usage.get_usage_summary(user)
            balance_info = budget.get_balance_display(user)
            if isinstance(summary, list):
                blocks = summary + [{"type": "section", "text": {"type": "mrkdwn", "text": balance_info}}]
                if source == "event_mention":
                    slack.post_thread_notice(channel_id, thread_ts, _blocks_to_text(blocks))
                else:
                    slack.post_ephemeral(response_url, blocks=blocks)
            else:
                _notify(summary + "\n" + balance_info, source=source,
                        response_url=response_url, channel_id=channel_id,
                        thread_ts=thread_ts)
            return

        if parsed.gallery:
            _notify(
                ":frame_with_picture: <https://d2jagmvo7k5q5j.cloudfront.net/index.html|AI Slop Gallery>",
                source=source, response_url=response_url,
                channel_id=channel_id, thread_ts=thread_ts,
            )
            return

        if parsed.pay_amount is not None:
            amount = parsed.pay_amount
            budget.add_credit(user, amount, source_user=user, note="Venmo payment")
            link = budget.generate_venmo_link(amount)
            _notify(
                f":white_check_mark: Credited *${amount:.2f}* to your balance.\n"
                f"Pay here: <{link}|Pay ${amount:.2f} on Venmo>",
                source=source, response_url=response_url,
                channel_id=channel_id, thread_ts=thread_ts,
            )
            return

        if parsed.report:
            if user not in budget.ADMIN_USERS:
                _notify("Only admins can use -report.", source=source,
                        response_url=response_url, channel_id=channel_id,
                        thread_ts=thread_ts)
                return
            _notify(budget.get_all_balances(), source=source,
                    response_url=response_url, channel_id=channel_id,
                    thread_ts=thread_ts)
            return

        if parsed.credit_target is not None:
            if user not in budget.ADMIN_USERS:
                _notify("Only admins can use -credit.", source=source,
                        response_url=response_url, channel_id=channel_id,
                        thread_ts=thread_ts)
                return
            target = parsed.credit_target
            amount = parsed.credit_amount
            new_bal = budget.add_credit(target, amount, source_user=user,
                                        note="Admin adjustment")
            _notify(
                f"Adjusted *{target}* by *${amount:.2f}*. New balance: *${new_bal:.2f}*",
                source=source, response_url=response_url,
                channel_id=channel_id, thread_ts=thread_ts,
            )
            return

        if parsed.conversation and parsed.mode in ("image", "video"):
            label = "-i" if parsed.mode == "image" else "-v"
            _notify(
                f"-c cannot be combined with {label}: conversations are text-only.",
                source=source, response_url=response_url,
                channel_id=channel_id, thread_ts=thread_ts,
            )
            return

        if parsed.mode == "video":
            prompt = prompts.sanitize_prompt(parsed.prompt_text, user, parsed.potato_mode)
            print(f"GENERATE VIDEO: {prompt}")
            backend = _backend_for_mode("video", parsed.backend_override)
            provider = providers.get_video_provider(parsed.backend_override)
            source_image = (
                media_refs.resolve_reference_image(source_ref)
                if source_ref else None
            )
            references = media_refs.resolve_reference_images(reference_refs)
            result = _provider_call_or_record_failure(
                user=user,
                mode="video",
                backend=backend,
                model=_model_for_request("video", backend),
                cost_estimate=_failure_cost_estimate(
                    "video", backend, duration=parsed.video_duration,
                ),
                call=lambda: provider.generate(
                    prompt,
                    duration=parsed.video_duration,
                    source_image=source_image,
                    references=references,
                ),
            )
            usage.record_usage(user, result)
            print("GENERATE VIDEO COMPLETE")
            image_upload.upload_to_s3(prompt, result.content, extension="mp4",
                                     user=user, channel=channel_name,
                                     model=result.model)
            video_thread_ts = thread_ts if source == "event_mention" else None
            slack.post_video_response(channel_id, user, parsed.display_text,
                                      result.content, thread_ts=video_thread_ts)
            return

        if parsed.mode == "image":
            prompt = prompts.sanitize_prompt(parsed.prompt_text, user, parsed.potato_mode)
            print(f"GENERATE IMAGE: {prompt}")
            backend = _backend_for_mode("image", parsed.backend_override)
            provider = providers.get_image_provider(parsed.backend_override)
            references = media_refs.resolve_reference_images(reference_refs)
            result = _provider_call_or_record_failure(
                user=user,
                mode="image",
                backend=backend,
                model=_model_for_request("image", backend),
                cost_estimate=_failure_cost_estimate(
                    "image", backend, reference_count=len(references),
                ),
                call=lambda: provider.generate(prompt, references=references),
            )
            usage.record_usage(user, result)
            print("GENERATE IMAGE COMPLETE")
            url = image_upload.upload_to_s3(prompt, result.content,
                                          user=user, channel=channel_name,
                                          model=result.model)
            print(f"UPLOAD URL {url}")
            if source == "event_mention":
                slack.post_image_response_in_thread(
                    channel_id, user, parsed.display_text, url, thread_ts,
                )
            else:
                slack.post_image_response(response_url, user, parsed.display_text, url)
            return

        # Text mode: route to conversation handler if applicable.
        conv_enabled = conversations.is_enabled()
        if parsed.conversation and not conv_enabled:
            _notify("Conversations are not enabled in this environment.",
                    source=source, response_url=response_url,
                    channel_id=channel_id, thread_ts=thread_ts)
            return

        existing_conv = None
        if conv_enabled and thread_ts:
            existing_conv = conversations.get(conversations.make_id(channel_id, thread_ts))

        # Event mentions in a thread without a tracked conversation are
        # ignored silently — the bot may have been mentioned incidentally.
        if source == "event_mention" and existing_conv is None:
            print(f"EVENT MENTION: no tracked conversation at "
                  f"{channel_id}:{thread_ts}, ignoring silently")
            return

        if existing_conv is not None or parsed.conversation:
            request_id = getattr(context, "aws_request_id", None) or "local"
            _handle_conversation_turn(
                parsed=parsed, user=user, channel_id=channel_id,
                response_url=response_url,
                existing_conv=existing_conv, request_id=request_id,
                lambda_start=lambda_start, source=source,
            )
            return

        # Single-shot text path (slash only — events with no conversation
        # were silently ignored above).
        system = prompts.get_system_message(user, parsed.potato_mode)
        print(f"GENERATE TEXT: {system}, {parsed.prompt_text}")
        backend = _backend_for_mode("text", parsed.backend_override)
        provider = providers.get_text_provider(parsed.backend_override)
        result = _provider_call_or_record_failure(
            user=user,
            mode="text",
            backend=backend,
            model=_model_for_request("text", backend),
            cost_estimate=0.0,
            call=lambda: provider.generate(system, parsed.prompt_text),
        )
        usage.record_usage(user, result)
        print(f"GENERATE TEXT COMPLETE: {result.content}")
        slack.post_text_response(response_url, user, parsed.display_text, result.content)

    except Exception as exc:
        print("COMMAND ERROR: " + str(exc))
        traceback.print_exc()
        _post_error_safe(str(exc), source=source, response_url=response_url,
                         channel_id=channel_id, thread_ts=thread_ts)
    # pylint: enable=broad-except


def _notify(text, *, source, response_url, channel_id, thread_ts):
    """Post an informational message to the user.

    Slash invocations get an ephemeral reply via response_url; event mentions
    get a thread notice via chat.postMessage.
    """
    if source == "event_mention":
        slack.post_thread_notice(channel_id, thread_ts, text)
    else:
        slack.post_ephemeral(response_url, text)


def _post_error_safe(text, *, source, response_url, channel_id, thread_ts):
    """Best-effort error post — never raises into the caller."""
    try:
        if source == "event_mention" and channel_id and thread_ts:
            slack.post_thread_notice(channel_id, thread_ts, text)
        elif response_url:
            slack.post_error(response_url, text)
    # pylint: disable=broad-except
    except Exception as exc:
        print(f"ERROR POSTING ERROR: {exc}")


def _blocks_to_text(blocks: list) -> str:
    """Flatten a Slack block list to plain text for thread-notice posting."""
    parts = []
    for block in blocks:
        text_field = block.get("text") or {}
        text = text_field.get("text") if isinstance(text_field, dict) else None
        if text:
            parts.append(text)
    return "\n".join(parts)


def _provider_call_or_record_failure(*, user: str, mode: str, backend: str,
                                     model: str, cost_estimate: float, call):
    """Run a provider call, recording failed attempts before re-raising."""
    try:
        return call()
    except Exception as exc:
        usage.record_failed_request(
            user,
            mode=mode,
            backend=backend,
            model=model,
            error_type=_classify_provider_error(exc),
            error_message=str(exc),
            cost_estimate=cost_estimate,
            exc=exc,
        )
        raise


def _backend_for_mode(mode: str, override: str | None) -> str:
    """Resolve the provider name that will be used for a request mode."""
    if mode == "text":
        return override or os.environ.get("TEXT_BACKEND", "gemini")
    if mode == "image":
        return override or os.environ.get("IMAGE_BACKEND", "grok")
    if mode == "video":
        return override or os.environ.get("VIDEO_BACKEND", "grok")
    return override or ""


def _model_for_request(mode: str, backend: str) -> str:
    """Best-effort model label for failed calls that return no GenerationResult."""
    if mode == "text":
        defaults = {
            "anthropic": "claude-sonnet-4-6",
            "gemini": "gemini-3.5-flash",
            "grok": "grok-4-1-fast-non-reasoning",
            "openai": "gpt-5.5",
        }
        return os.environ.get("TEXT_MODEL", defaults.get(backend, ""))
    if mode == "image":
        defaults = {
            "gemini": "gemini-3.1-flash-image",
            "grok": "grok-imagine-image-quality",
            "openai": "dall-e-3",
        }
        return os.environ.get("IMAGE_MODEL", defaults.get(backend, ""))
    if mode == "video":
        defaults = {
            "gemini": "veo-3.1-fast-generate-preview",
            "grok": "grok-imagine-video",
        }
        return os.environ.get("VIDEO_MODEL", defaults.get(backend, ""))
    return ""


def _failure_cost_estimate(
    mode: str,
    backend: str,
    *,
    duration: int | None = None,
    reference_count: int = 0,
) -> float:
    """Fallback cost for failed attempts when the provider omits actual cost."""
    if mode == "image":
        return usage.COST_PER_IMAGE.get(backend, 0.0) * max(1, 1 + reference_count)
    if mode == "video":
        default_seconds = "8" if backend == "gemini" else "10"
        seconds = duration or int(os.environ.get("VIDEO_DURATION", default_seconds))
        if backend == "gemini":
            seconds = min((4, 6, 8), key=lambda supported: abs(supported - seconds))
        return seconds * usage.COST_PER_VIDEO.get(backend, 0.0)
    return 0.0


def _classify_provider_error(exc: Exception) -> str:
    """Classify provider errors for audit summaries."""
    error_type = getattr(exc, "error_type", None)
    if error_type:
        return error_type
    text = str(exc).lower()
    if "moderation" in text or "safety" in text or "policy" in text:
        return "moderation"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    return "provider_error"


def _collect_media_references(parsed, payload_references: list[media_refs.ReferenceImage]):
    """Combine references parsed from command text and Slack modal payloads."""
    source_ref = parsed.source_image
    references = list(parsed.reference_images)
    extra_starts = []
    for reference in payload_references:
        if reference.role == "start":
            extra_starts.append(reference)
        else:
            references.append(reference)
    if extra_starts:
        if source_ref is None and len(extra_starts) == 1:
            source_ref = extra_starts[0]
        else:
            references.extend(extra_starts)
    return source_ref, references


def _validate_media_references(parsed, source_ref, reference_refs) -> str | None:
    """Return a user-facing validation error for unsupported media combinations."""
    has_references = bool(source_ref or reference_refs)
    if not has_references:
        return None
    if parsed.mode == "text":
        return "Reference images can only be used with -i or -v."
    if parsed.mode == "image":
        if source_ref:
            return "--start can only be used with -v."
        if len(reference_refs) > 3:
            return "Image generation supports at most 3 reference images."
        return None
    if parsed.mode == "video":
        if any(ref.role == "edit" for ref in reference_refs):
            return "--edit can only be used with -i."
        if source_ref and reference_refs:
            return "--start cannot be combined with --ref for a video request."
        if len(reference_refs) > 7:
            return "Video generation supports at most 7 reference images."
    return None


def _handle_conversation_turn(*, parsed, user, channel_id, response_url,
                              existing_conv, request_id, lambda_start, source):
    """Dispatch to first-turn or continuation-turn handler."""
    if existing_conv is None:
        _handle_first_turn(
            parsed=parsed, user=user, channel_id=channel_id,
            response_url=response_url, lambda_start=lambda_start,
        )
        return

    _handle_continuation_turn(
        parsed=parsed, user=user, response_url=response_url,
        thread_ts=existing_conv.thread_ts, existing_conv=existing_conv,
        request_id=request_id, lambda_start=lambda_start,
        channel_id=channel_id, source=source,
    )


def _handle_first_turn(*, parsed, user, channel_id, response_url, lambda_start):
    """First turn of a conversation. Always top-level (slash command can't
    fire inside a thread), so we mint thread_ts via chat.postMessage."""
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
    backend = _backend_for_mode("text", parsed.backend_override)
    provider = providers.get_text_provider(parsed.backend_override)
    print(f"GENERATE TEXT (conv turn 1): {parsed.prompt_text}")
    result = _provider_call_or_record_failure(
        user=user,
        mode="text",
        backend=backend,
        model=_model_for_request("text", backend),
        cost_estimate=0.0,
        call=lambda: provider.chat(effective_system, [user_msg]),
    )
    usage.record_usage(user, result)
    print(f"GENERATE TEXT COMPLETE: {result.content}")
    user_msg["backend"] = result.backend
    assistant_msg = conversations.build_assistant_message(result)
    footer = slack.conversation_started_footer(result.backend)

    # chat.postMessage first to mint a thread_ts, then create the row.
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
        raise


def _handle_continuation_turn(*, parsed, user, response_url, thread_ts, existing_conv,
                              request_id, lambda_start, channel_id="", source="slash"):
    """Continuation turn: lock, preflight, replay history, atomic append, post.

    `source` selects the posting path: "slash" uses response_url; "event_mention"
    uses chat.postMessage / thread notices via the bot token.
    """
    conv_id = existing_conv.conversation_id
    acquired = conversations.acquire_lock(conv_id, request_id)
    if not acquired:
        time.sleep(conversations.LOCK_RETRY_SLEEP_SECONDS)
        acquired = conversations.acquire_lock(conv_id, request_id)
    if not acquired:
        _continuation_notice(
            "Another turn in this conversation is in flight; try again in a few seconds.",
            source=source, response_url=response_url,
            channel_id=channel_id, thread_ts=thread_ts,
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
            _continuation_error(
                "This conversation has reached its limit. "
                "Start a new one with `/slop-bot -c <prompt>`.",
                source=source, response_url=response_url,
                channel_id=channel_id, thread_ts=thread_ts,
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
            _continuation_error(
                "Aborting before model call to avoid Lambda timeout.",
                source=source, response_url=response_url,
                channel_id=channel_id, thread_ts=thread_ts,
            )
            return

        backend = _backend_for_mode("text", parsed.backend_override)
        provider = providers.get_text_provider(parsed.backend_override)
        print(f"GENERATE TEXT (conv turn {conv.turn_count + 1}): {parsed.prompt_text}")
        result = _provider_call_or_record_failure(
            user=user,
            mode="text",
            backend=backend,
            model=_model_for_request("text", backend),
            cost_estimate=0.0,
            call=lambda: provider.chat(effective_system, api_messages),
        )
        usage.record_usage(user, result)
        print(f"GENERATE TEXT COMPLETE: {result.content}")
        user_msg["backend"] = result.backend
        assistant_msg = conversations.build_assistant_message(result)
        added = len(parsed.prompt_text) + len(result.content)

        appended = conversations.append_turn(
            conv_id, user_msg, assistant_msg, added, conv.turn_count,
        )
        if not appended:
            print(f"PHANTOM TURN DROPPED: {conv_id} (turn_count moved)")
            _continuation_error(
                "This conversation was modified by another in-flight turn; please retry.",
                source=source, response_url=response_url,
                channel_id=channel_id, thread_ts=thread_ts,
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
        if source == "event_mention":
            slack.post_text_chat_postmessage(
                channel_id=channel_id, user=user, display=parsed.display_text,
                response=result.content, thread_ts=thread_ts,
                footer_blocks=footer_blocks,
            )
        else:
            slack.post_text_response_in_thread(
                response_url=response_url, user=user, display=parsed.display_text,
                response=result.content, thread_ts=thread_ts,
                footer_blocks=footer_blocks,
            )
    finally:
        conversations.release_lock(conv_id, request_id)


def _continuation_notice(text, *, source, response_url, channel_id, thread_ts):
    """Soft notice in a continuation context (lock contention)."""
    if source == "event_mention":
        slack.post_thread_notice(channel_id, thread_ts, text)
    else:
        slack.post_ephemeral(response_url, text)


def _continuation_error(text, *, source, response_url, channel_id, thread_ts):
    """Hard error in a continuation context."""
    if source == "event_mention":
        slack.post_thread_notice(channel_id, thread_ts, text)
    else:
        slack.post_error(response_url, text)


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
        source_ref, reference_refs = _collect_media_references(parsed, [])
        source_image = media_refs.resolve_reference_image(source_ref) if source_ref else None
        references = media_refs.resolve_reference_images(reference_refs)
        result = provider.generate(
            prompt,
            duration=parsed.video_duration,
            source_image=source_image,
            references=references,
        )
        outfile = "/tmp/claude-1000/ai_slop_output.mp4"
        with open(outfile, "wb") as f:
            f.write(result.content)
        print(f"Video saved to {outfile}")
    elif parsed.mode == "image":
        prompt = prompts.sanitize_prompt(parsed.prompt_text, "cli", parsed.potato_mode)
        provider = providers.get_image_provider(parsed.backend_override)
        _source_ref, reference_refs = _collect_media_references(parsed, [])
        references = media_refs.resolve_reference_images(reference_refs)
        result = provider.generate(prompt, references=references)
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
