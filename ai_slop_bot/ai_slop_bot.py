"""Main Lambda handler for ai-slop bot. Routes text vs image generation."""

import json
import sys
import traceback

import image_upload
import parsing
import prompts
import providers
import slack


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

        parsed = parsing.parse_command(input_str)

        if parsed.mode == "image":
            prompt = prompts.sanitize_prompt(parsed.prompt_text, user)
            print(f"GENERATE IMAGE: {prompt}")
            provider = providers.get_image_provider(parsed.backend_override)
            image_bytes = provider.generate(prompt)
            print("GENERATE IMAGE COMPLETE")
            url = image_upload.upload_to_s3(prompt, image_bytes)
            print(f"UPLOAD URL {url}")
            slack.post_image_response(response_url, user, parsed.display_text, url)
        else:
            system = prompts.get_system_message(user)
            print(f"GENERATE TEXT: {system}, {parsed.prompt_text}")
            provider = providers.get_text_provider(parsed.backend_override)
            response = provider.generate(system, parsed.prompt_text)
            print(f"GENERATE TEXT COMPLETE: {response}")
            slack.post_text_response(response_url, user, parsed.display_text, response)

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

    if parsed.mode == "image":
        prompt = prompts.sanitize_prompt(parsed.prompt_text, "cli")
        provider = providers.get_image_provider(parsed.backend_override)
        image_bytes = provider.generate(prompt)
        outfile = "/tmp/claude-1000/ai_slop_output.png"
        with open(outfile, "wb") as f:
            f.write(image_bytes)
        print(f"Image saved to {outfile}")
    else:
        system = prompts.get_system_message("cli")
        provider = providers.get_text_provider(parsed.backend_override)
        response = provider.generate(system, parsed.prompt_text)
        print(response)


if __name__ == "__main__":
    main()
