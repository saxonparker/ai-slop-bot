"""Main Lambda handler for ai-slop bot. Routes text vs image generation."""

import json
import sys
import traceback

import budget
import image_upload
import parsing
import prompts
import providers
import slack
import usage


def ai_slop_bot(event, _):
    """Entry point for the Lambda that generates text or images."""
    response_url = None
    try:
        # pylint: disable=broad-except
        print(f"SNS MESSAGE: {event['Records'][0]['Sns']['Message']}")
        message = json.loads(event["Records"][0]["Sns"]["Message"])
        response_url = message["response_url"]
        input_str = message["prompt"]
        user = message["user"]
        channel_id = message.get("channel_id", "")

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

        if parsed.mode == "video":
            prompt = prompts.sanitize_prompt(parsed.prompt_text, user, parsed.potato_mode)
            print(f"GENERATE VIDEO: {prompt}")
            provider = providers.get_video_provider(parsed.backend_override)
            result = provider.generate(prompt, duration=parsed.video_duration)
            print("GENERATE VIDEO COMPLETE")
            slack.post_video_response(channel_id, user, parsed.display_text, result.content)
            usage.record_usage(user, result)
        elif parsed.mode == "image":
            prompt = prompts.sanitize_prompt(parsed.prompt_text, user, parsed.potato_mode)
            print(f"GENERATE IMAGE: {prompt}")
            provider = providers.get_image_provider(parsed.backend_override)
            result = provider.generate(prompt)
            print("GENERATE IMAGE COMPLETE")
            url = image_upload.upload_to_s3(prompt, result.content)
            print(f"UPLOAD URL {url}")
            slack.post_image_response(response_url, user, parsed.display_text, url)
            usage.record_usage(user, result)
        else:
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
