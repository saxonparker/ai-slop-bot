"""Dispatch Lambda for /ai-slop. Receives Slack webhook and publishes to SNS."""

import base64
import json
import os
import traceback
import urllib.parse

import boto3


def dispatch(event, _):
    """Entry point for the dispatch Lambda. Publishes to SNS to invoke the bot Lambda."""

    def generate_response(message):
        """Generate a full HTTP JSON response."""
        return {
            "statusCode": str(200),
            "body": json.dumps({"text": message}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    try:
        print(event)
        params = dict(
            urllib.parse.parse_qsl(
                base64.b64decode(str(event["body"])).decode("ascii")
            )
        )
        print(params)
        if "text" not in params or not params["text"]:
            return generate_response("Usage:\n/ai-slop <prompt>\n/ai-slop -i <prompt>")
        prompt = params["text"]
        user = params["user_name"]
        print("DISPATCH COMMAND: " + prompt + " " + user)

        message = {
            "response_url": params["response_url"],
            "prompt": prompt,
            "user": user,
        }
        response = boto3.client("sns").publish(
            TopicArn=os.environ["AI_SLOP_SNS_TOPIC"],
            Message=json.dumps({"default": json.dumps(message)}),
            MessageStructure="json",
        )
        print("SNS PUBLISH: " + str(response))

        return generate_response(f'Processing prompt "{prompt}"...')
    # pylint: disable=broad-except
    except Exception as exc:
        print("DISPATCH ERROR: " + str(exc))
        traceback.print_exc()
        return generate_response(str(exc))
    # pylint: enable=broad-except
