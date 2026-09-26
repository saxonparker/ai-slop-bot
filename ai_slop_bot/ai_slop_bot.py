"""Main Lambda handler for ai-slop bot. Routes text vs image generation."""

import dataclasses
import json
import os
import sys
import traceback

import budget
import bufo
import hall_of_fame
import image_upload
import media_refs
import model_config
import parsing
import payments
import prompts
import providers
import slack
import usage


CANONICAL_SLASH_COMMAND = "/slop-bot"


def ai_slop_bot(event, _):
    """Entry point for the Lambda that generates text or images."""
    response_url = None
    source = "slash"
    try:
        # pylint: disable=broad-except
        print(f"SNS MESSAGE: {event['Records'][0]['Sns']['Message']}")
        message = json.loads(event["Records"][0]["Sns"]["Message"])
        response_url = message.get("response_url", "")
        source = message.get("source", "slash")
        if source in ("hall_of_fame", "hall_of_fame_shortcut"):
            try:
                if message["source"] == "hall_of_fame_shortcut":
                    key = hall_of_fame.key_from_slack_message(message["slack_message"])
                    if key is None:
                        slack.post_ephemeral(response_url,
                            "Choose a generated photo or video. If this is an older video or a message "
                            f"with multiple items, add it from the <{hall_of_fame.CLOUDFRONT}/index.html|gallery>.")
                        return
                    selection = hall_of_fame.set_featured(key, True)
                else:
                    selection = hall_of_fame.set_featured(message["media_key"], message["featured"])
            except Exception as exc:  # pylint: disable=broad-except
                print(f"HALL OF FAME ERROR: {exc}")
                slack.post_ephemeral(response_url, "Could not save that Hall of Fame change. Please try again.")
                return
            slack.post_hall_of_fame_result(response_url, selection)
            return
        if source == "link_shared":
            _unfurl_gallery_links(message)
            return
        input_str = message["prompt"]
        user = message["user"]
        channel_id = message.get("channel_id", "")
        channel_name = message.get("channel_name", "")

        parsed = parsing.parse_command(input_str)
        if parsed.pay_error:
            slack.post_ephemeral(response_url, parsed.pay_error)
            return
        payload_references = [
            media_refs.ReferenceImage.from_payload(ref)
            for ref in message.get("reference_images", [])
        ]
        payload_source_video = media_refs.ReferenceVideo.from_payload(
            message.get("source_video")
        )
        if payload_source_video:
            parsed = dataclasses.replace(
                parsed,
                video_op=payload_source_video.role,
                video_source_url=None,
            )
        bufo_error = _validate_bufo_mode(parsed)
        if bufo_error:
            slack.post_ephemeral(response_url, bufo_error)
            return
        resolution_error = _validate_video_resolution(parsed)
        if resolution_error:
            slack.post_ephemeral(response_url, resolution_error)
            return
        source_ref, reference_refs = _collect_media_references(parsed, payload_references)
        validation_error = _validate_media_references(parsed, source_ref, reference_refs)
        if validation_error:
            slack.post_ephemeral(response_url, validation_error)
            return

        if parsed.upload_requested:
            slack.post_ephemeral(response_url, "--upload can only be used from the slash command composer.")
            return

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

        if parsed.pay_test_amount is not None:
            amount = parsed.pay_test_amount
            try:
                link = payments.create_sandbox_checkout(user, amount)
            except ValueError as exc:
                slack.post_ephemeral(response_url, str(exc))
                return
            slack.post_ephemeral(
                response_url,
                f":test_tube: *PayPal Sandbox test* — <{link}|Test a ${amount:.2f} payment>.\n"
                "Use a Personal sandbox buyer account. No real money is charged and your real balance is unchanged.",
            )
            return

        if parsed.pay_amount is not None:
            amount = parsed.pay_amount
            if os.environ.get("PAYMENTS_ENABLED") != "true":
                # Preserve the deployed Venmo flow until live PayPal is explicitly enabled.
                budget.add_credit(user, float(amount), source_user=user, note="Venmo payment")
                link = budget.generate_venmo_link(amount)
                slack.post_ephemeral(
                    response_url,
                    f":white_check_mark: Credited *${amount:.2f}* to your balance.\n"
                    f"Pay here: <{link}|Pay ${amount:.2f} on Venmo>",
                )
                return
            try:
                link = payments.create_live_checkout(user, amount)
            except ValueError as exc:
                slack.post_ephemeral(response_url, str(exc))
                return
            slack.post_ephemeral(
                response_url,
                f"<{link}|Buy ${amount:.2f} in credits with PayPal or Venmo>.\n"
                "Credits are added only after payment is confirmed. This link expires in one hour.",
            )
            return

        if parsed.report:
            if user not in budget.ADMIN_USERS:
                slack.post_ephemeral(response_url, "Only admins can use /slop-bot --report.")
                return
            slack.post_ephemeral(response_url, budget.get_all_balances())
            return

        if parsed.credit_target is not None:
            if user not in budget.ADMIN_USERS:
                slack.post_ephemeral(response_url, "Only admins can use /slop-bot --credit.")
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

        # Account commands above remain available so users can pay and recover.
        # Gate every generation path before resolving media or calling providers.
        balance = budget.get_balance(user)
        if balance <= budget.GENERATION_CUTOFF_BALANCE:
            slack.post_ephemeral(response_url, budget.get_payment_required_message(balance))
            return
        if balance <= budget.PROMPT_OVERRIDE_BALANCE:
            parsed = dataclasses.replace(
                parsed, prompt_text=prompts.get_payment_prompt(parsed.mode),
                emoji_mode=False, bufo_mode=False, potato_mode=False,
            )

        if parsed.mode == "video":
            prompt = prompts.sanitize_prompt(parsed.prompt_text, user, parsed.potato_mode)
            print(f"GENERATE VIDEO: {prompt}")
            backend = _backend_for_mode("video", parsed.backend_override)
            provider = providers.get_video_provider(parsed.backend_override)
            if parsed.video_op:
                source_image = None
                references = []
                video_url = parsed.video_source_url
                if payload_source_video:
                    source_video = media_refs.resolve_reference_video(payload_source_video)
                    video_url = image_upload.upload_to_s3(
                        prompt,
                        source_video.data,
                        extension=source_video.extension,
                        user=user,
                        channel=channel_name,
                        model="source-video",
                        s3_prefix=image_upload.SOURCE_VIDEO_PREFIX,
                        add_to_manifest=False,
                    )
            else:
                source_image = (
                    media_refs.resolve_reference_image(source_ref)
                    if source_ref else None
                )
                references = media_refs.resolve_reference_images(reference_refs)
                video_url = None
            result = _provider_call_or_record_failure(
                user=user,
                mode="video",
                backend=backend,
                model=_model_for_request("video", backend),
                cost_estimate=_failure_cost_estimate(
                    "video", backend, duration=parsed.video_duration,
                    resolution=parsed.video_resolution,
                    has_references=bool(references or parsed.voices),
                    video_op=parsed.video_op,
                ),
                call=lambda: provider.generate(
                    prompt,
                    duration=parsed.video_duration,
                    source_image=source_image,
                    references=references,
                    voices=parsed.voices,
                    video_op=parsed.video_op,
                    video_url=video_url,
                    resolution=parsed.video_resolution,
                ),
            )
            usage.record_usage(user, result)
            print("GENERATE VIDEO COMPLETE")
            video_url = image_upload.upload_to_s3(prompt, result.content, extension="mp4",
                                                 user=user, channel=channel_name,
                                                 model=result.model)
            slack_file_id = slack.post_video_response(channel_id, user, parsed.display_text,
                                                      result.content)
            if hall_of_fame.is_enabled():
                try:
                    hall_of_fame.register_slack_file(slack_file_id, video_url)
                except Exception as exc:  # pylint: disable=broad-except
                    print(f"SLACK VIDEO GALLERY LINK ERROR: {exc}")
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
                model=_model_for_request("image", backend, image_edit=bool(references)),
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
            slack.post_image_response(response_url, user, parsed.display_text, url)
            return

        if parsed.bufo_mode:
            names = bufo.get_bufo_emoji_names()
            system = prompts.get_bufo_system_message(names)
            print(f"GENERATE BUFO TEXT: {parsed.prompt_text}")
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
            response = bufo.sanitize_bufo_output(result.content, set(names))
            print(f"GENERATE BUFO TEXT COMPLETE: {response}")
            slack.post_text_response(response_url, user, parsed.display_text, response,
                                     render_in_block=True)
            return

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
        _post_error_safe(_describe_error_for_user(exc), source=source, response_url=response_url)
    # pylint: enable=broad-except


def _unfurl_gallery_links(message):
    """Preview shared gallery links; dispatch only forwards URLs naming gallery media."""
    items = {}
    for url in message["links"]:
        details = hall_of_fame.media_details(hall_of_fame.key_from_url(url))
        if details:  # None when S3 can't serve it, e.g. deleted
            items[url] = details
    if items:
        slack.post_gallery_unfurls(message, items)


def _describe_error_for_user(exc: Exception) -> str:
    """Human-readable error text for Slack, falling back to the raw message.

    Providers that classify their own failures (Grok, Gemini) set
    `user_message` on ProviderGenerationError; other exceptions fall back to
    their raw text, same as before.
    """
    message = getattr(exc, "user_message", None) or str(exc)
    cost_actual = getattr(exc, "cost_actual", None)
    if cost_actual:
        message += f" Cost: ${cost_actual:.2f}"
    return message


def _post_error_safe(text, *, source, response_url):
    """Best-effort error post — never raises into the caller."""
    try:
        if source in ("hall_of_fame", "hall_of_fame_shortcut") and response_url:
            slack.post_ephemeral(response_url, "Could not confirm the Hall of Fame change. Please check the gallery.")
        elif response_url:
            slack.post_error(response_url, text)
    # pylint: disable=broad-except
    except Exception as exc:
        print(f"ERROR POSTING ERROR: {exc}")


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


def _model_for_request(mode: str, backend: str, *, image_edit: bool = False) -> str:
    """Best-effort model label for failed calls that return no GenerationResult."""
    return model_config.get_model(mode, backend, image_edit=image_edit)


def _failure_cost_estimate(  # pylint: disable=too-many-arguments
    mode: str,
    backend: str,
    *,
    duration: int | None = None,
    reference_count: int = 0,
    resolution: str | None = None,
    has_references: bool = False,
    video_op: str | None = None,
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
        # Grok's per-second rate scales with resolution, so estimate against the
        # size it will actually render. Edits and extensions inherit the source
        # clip's resolution capped at 720p, so estimate at that cap.
        rendered = (
            model_config.REFERENCE_MAX_VIDEO_RESOLUTION if video_op
            else model_config.resolve_video_resolution(
                resolution, has_references=has_references,
            )
        )
        return seconds * usage.video_cost_per_second(backend, rendered)
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


def _validate_bufo_mode(parsed) -> str | None:
    """Return a user-facing error when bufo mode is combined with non-text modes."""
    if not parsed.bufo_mode:
        return None

    conflicts = []
    if parsed.mode == "image":
        conflicts.append("-i")
    elif parsed.mode == "video":
        conflicts.append("-v")

    if not conflicts:
        return None

    return (
        f"-bufo cannot be combined with {', '.join(conflicts)}: "
        "bufo mode is single-shot text-only."
    )


def _validate_video_resolution(parsed) -> str | None:
    """Return a user-facing error when -r is unusable for this request."""
    if parsed.resolution_error:
        return parsed.resolution_error
    if not parsed.video_resolution:
        return None
    if parsed.mode != "video":
        return "-r can only be used with -v."
    if parsed.video_op:
        # xAI matches the source clip for edits and extensions.
        return "-r cannot be combined with --edit-video or --extend-video."
    if _backend_for_mode("video", parsed.backend_override) != "grok":
        return "-r is only supported on the grok backend; use -b grok."
    return None


def _validate_media_references(parsed, source_ref, reference_refs) -> str | None:
    """Return a user-facing validation error for unsupported media combinations."""
    video_op = getattr(parsed, "video_op", None)
    if video_op:
        if source_ref or reference_refs:
            return "--edit-video/--extend-video cannot be combined with --start, --ref, or --edit."
        backend = parsed.backend_override or os.environ.get("VIDEO_BACKEND", "grok")
        if backend != "grok":
            return "Video edit/extend is only supported on the grok backend; use -b grok."

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


def main():
    """Process the command given on the command line."""
    input_str = " ".join(sys.argv[1:])
    parsed = parsing.parse_command(input_str)
    if not parsed.bufo_mode:
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

    bufo_error = _validate_bufo_mode(parsed)
    if bufo_error:
        raise SystemExit(bufo_error)

    if parsed.bufo_mode:
        names = bufo.get_bufo_emoji_names()
        system = prompts.get_bufo_system_message(names)
        provider = providers.get_text_provider(parsed.backend_override)
        result = provider.generate(system, parsed.prompt_text)
        print(bufo.sanitize_bufo_output(result.content, set(names)))
        return

    resolution_error = _validate_video_resolution(parsed)
    if resolution_error:
        raise SystemExit(resolution_error)

    if parsed.mode == "video":
        source_ref, reference_refs = _collect_media_references(parsed, [])
        validation_error = _validate_media_references(parsed, source_ref, reference_refs)
        if validation_error:
            raise SystemExit(validation_error)
        prompt = prompts.sanitize_prompt(parsed.prompt_text, "cli", parsed.potato_mode)
        provider = providers.get_video_provider(parsed.backend_override)
        if parsed.video_op:
            source_image = None
            references = []
        else:
            source_image = media_refs.resolve_reference_image(source_ref) if source_ref else None
            references = media_refs.resolve_reference_images(reference_refs)
        result = provider.generate(
            prompt,
            duration=parsed.video_duration,
            source_image=source_image,
            references=references,
            video_op=parsed.video_op,
            video_url=parsed.video_source_url,
            resolution=parsed.video_resolution,
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
