"""Tests for the dispatch Lambda: thread_ts propagation and HELP_TEXT."""

import json
from pathlib import Path
import sys
from unittest.mock import MagicMock, patch
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ai_slop_dispatch"))

import ai_slop_dispatch  # noqa: E402  pylint: disable=wrong-import-position


def _slack_event(text: str, user="alice", channel_id="C123",
                 channel_name="general", thread_ts: str | None = None,
                 trigger_id: str | None = None):
    body_params = {
        "text": text,
        "user_name": user,
        "response_url": "https://hooks.slack.example/dispatch",
        "channel_id": channel_id,
        "channel_name": channel_name,
    }
    if thread_ts is not None:
        body_params["thread_ts"] = thread_ts
    if trigger_id is not None:
        body_params["trigger_id"] = trigger_id
    return {"body": urllib.parse.urlencode(body_params)}


@patch.dict("os.environ", {"AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic"})
@patch("ai_slop_dispatch.boto3.client")
def test_dispatch_propagates_thread_ts_when_present(mock_boto):
    mock_sns = MagicMock()
    mock_boto.return_value = mock_sns
    mock_sns.publish.return_value = {"MessageId": "abc"}

    ai_slop_dispatch.dispatch(_slack_event("hello", thread_ts="1700000000.000001"), None)

    publish_kwargs = mock_sns.publish.call_args.kwargs
    inner = json.loads(publish_kwargs["Message"])
    payload = json.loads(inner["default"])
    assert payload["thread_ts"] == "1700000000.000001"


@patch.dict("os.environ", {"AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic"})
@patch("ai_slop_dispatch.boto3.client")
def test_dispatch_thread_ts_empty_when_top_level(mock_boto):
    mock_sns = MagicMock()
    mock_boto.return_value = mock_sns
    mock_sns.publish.return_value = {"MessageId": "abc"}

    ai_slop_dispatch.dispatch(_slack_event("hello"), None)

    publish_kwargs = mock_sns.publish.call_args.kwargs
    inner = json.loads(publish_kwargs["Message"])
    payload = json.loads(inner["default"])
    assert payload["thread_ts"] == ""


def test_help_text_mentions_conversation_flag():
    assert "-c" in ai_slop_dispatch.HELP_TEXT
    assert "conversation" in ai_slop_dispatch.HELP_TEXT.lower()


def test_help_text_mentions_reference_image_modal():
    help_text = ai_slop_dispatch.HELP_TEXT
    assert "--upload" in help_text
    assert "--edit" in help_text
    assert "--start" in help_text
    assert "--edit-video" in help_text
    assert "--extend-video" in help_text
    assert "edit/extend" in help_text
    assert "deleted from Slack" in help_text


def _events_request(payload: dict):
    return {
        "path": "/slack/events",
        "body": json.dumps(payload),
    }


def test_url_verification_echoes_challenge():
    response = ai_slop_dispatch.dispatch(
        _events_request({"type": "url_verification", "challenge": "abc123"}),
        None,
    )
    assert response["statusCode"] == "200"
    body = json.loads(response["body"])
    assert body["challenge"] == "abc123"


@patch.dict("os.environ", {"AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic"})
@patch("ai_slop_dispatch.boto3.client")
def test_app_mention_in_thread_publishes_event_mention_sns(mock_boto):
    mock_sns = MagicMock()
    mock_boto.return_value = mock_sns
    mock_sns.publish.return_value = {"MessageId": "abc"}

    payload = {
        "type": "event_callback",
        "event": {
            "type": "app_mention",
            "user": "U999",
            "text": "<@UBOT> follow up question",
            "channel": "C123",
            "thread_ts": "1700000000.000001",
            "ts": "1700000000.000005",
        },
    }
    ai_slop_dispatch.dispatch(_events_request(payload), None)

    mock_sns.publish.assert_called_once()
    inner = json.loads(mock_sns.publish.call_args.kwargs["Message"])
    sns_msg = json.loads(inner["default"])
    assert sns_msg["source"] == "event_mention"
    assert sns_msg["thread_ts"] == "1700000000.000001"
    assert sns_msg["channel_id"] == "C123"
    assert sns_msg["prompt"] == "follow up question"
    assert sns_msg["event_user_id"] == "U999"
    assert sns_msg["response_url"] == ""


@patch.dict("os.environ", {"AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic"})
@patch("ai_slop_dispatch.boto3.client")
def test_app_mention_with_pipe_form_user_id_strips_correctly(mock_boto):
    mock_sns = MagicMock()
    mock_boto.return_value = mock_sns
    mock_sns.publish.return_value = {"MessageId": "abc"}

    payload = {
        "type": "event_callback",
        "event": {
            "type": "app_mention", "user": "U999",
            "text": "<@UBOT|slop-bot>   tell me a joke",
            "channel": "C123", "thread_ts": "1700.0", "ts": "1700.5",
        },
    }
    ai_slop_dispatch.dispatch(_events_request(payload), None)

    inner = json.loads(mock_sns.publish.call_args.kwargs["Message"])
    sns_msg = json.loads(inner["default"])
    assert sns_msg["prompt"] == "tell me a joke"


@patch.dict("os.environ", {"AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic"})
@patch("ai_slop_dispatch.boto3.client")
def test_app_mention_without_thread_ts_is_ignored(mock_boto):
    mock_sns = MagicMock()
    mock_boto.return_value = mock_sns

    payload = {
        "type": "event_callback",
        "event": {
            "type": "app_mention", "user": "U999",
            "text": "<@UBOT> hello", "channel": "C123", "ts": "1700.0",
        },
    }
    response = ai_slop_dispatch.dispatch(_events_request(payload), None)

    assert response["statusCode"] == "200"
    mock_sns.publish.assert_not_called()


@patch.dict("os.environ", {"AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic"})
@patch("ai_slop_dispatch.boto3.client")
def test_app_mention_from_bot_is_ignored(mock_boto):
    mock_sns = MagicMock()
    mock_boto.return_value = mock_sns

    payload = {
        "type": "event_callback",
        "event": {
            "type": "app_mention", "user": "U999", "bot_id": "BSELF",
            "text": "<@UBOT> hello", "channel": "C123",
            "thread_ts": "1700.0", "ts": "1700.0",
        },
    }
    ai_slop_dispatch.dispatch(_events_request(payload), None)

    mock_sns.publish.assert_not_called()


@patch.dict("os.environ", {"AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic"})
@patch("ai_slop_dispatch.boto3.client")
def test_app_mention_with_empty_prompt_is_ignored(mock_boto):
    mock_sns = MagicMock()
    mock_boto.return_value = mock_sns

    payload = {
        "type": "event_callback",
        "event": {
            "type": "app_mention", "user": "U999",
            "text": "<@UBOT>   ", "channel": "C123",
            "thread_ts": "1700.0", "ts": "1700.0",
        },
    }
    ai_slop_dispatch.dispatch(_events_request(payload), None)

    mock_sns.publish.assert_not_called()


@patch.dict("os.environ", {"AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic"})
@patch("ai_slop_dispatch.boto3.client")
def test_slash_command_publishes_with_source_slash(mock_boto):
    mock_sns = MagicMock()
    mock_boto.return_value = mock_sns
    mock_sns.publish.return_value = {"MessageId": "abc"}

    ai_slop_dispatch.dispatch(_slack_event("hello"), None)

    inner = json.loads(mock_sns.publish.call_args.kwargs["Message"])
    sns_msg = json.loads(inner["default"])
    assert sns_msg["source"] == "slash"


@patch.dict("os.environ", {
    "AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic",
    "SLACK_BOT_TOKEN": "xoxb-token",
})
@patch("ai_slop_dispatch.urllib.request.urlopen")
@patch("ai_slop_dispatch.boto3.client")
def test_upload_slash_command_opens_modal(mock_boto, mock_urlopen):
    mock_boto.return_value = MagicMock()
    mock_urlopen.return_value.__enter__.return_value.read.return_value = b'{"ok": true}'

    response = ai_slop_dispatch.dispatch(
        _slack_event("-v 10 -b grok --upload make it move", trigger_id="trig"),
        None,
    )

    assert response["statusCode"] == "200"
    mock_boto.return_value.publish.assert_not_called()
    request = mock_urlopen.call_args.args[0]
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["trigger_id"] == "trig"
    view = payload["view"]
    assert view["callback_id"] == "ai_slop_upload"
    metadata = json.loads(view["private_metadata"])
    assert metadata["mode"] == "video"
    blocks = {block["block_id"]: block for block in view["blocks"]}
    assert "video_op_block" in blocks
    video_op = blocks["video_op_block"]["element"]
    assert [option["value"] for option in video_op["options"]] == [
        "generate", "edit", "extend",
    ]
    assert video_op["initial_option"]["value"] == "generate"
    assert blocks["video_op_block"]["dispatch_action"] is True
    assert "video_url_block" not in blocks
    assert "source_video_block" not in blocks
    assert "reference_role_block" in blocks
    assert blocks["files_block"]["optional"] is True


@patch.dict("os.environ", {
    "AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic",
    "SLACK_BOT_TOKEN": "xoxb-token",
})
@patch("ai_slop_dispatch.urllib.request.urlopen")
@patch("ai_slop_dispatch.boto3.client")
def test_video_upload_slash_command_generate_modal_hides_video_source_blocks(mock_boto, mock_urlopen):
    mock_boto.return_value = MagicMock()
    mock_urlopen.return_value.__enter__.return_value.read.return_value = b'{"ok": true}'

    response = ai_slop_dispatch.dispatch(
        _slack_event("-v --upload make it move", trigger_id="trig"),
        None,
    )

    assert response["statusCode"] == "200"
    mock_boto.return_value.publish.assert_not_called()
    request = mock_urlopen.call_args.args[0]
    payload = json.loads(request.data.decode("utf-8"))
    blocks = {block["block_id"]: block for block in payload["view"]["blocks"]}
    assert "video_op_block" in blocks
    assert "duration_block" in blocks
    assert "reference_role_block" in blocks
    assert "files_block" in blocks
    assert "video_url_block" not in blocks
    assert "source_video_block" not in blocks


@patch.dict("os.environ", {
    "AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic",
    "SLACK_BOT_TOKEN": "xoxb-token",
})
@patch("ai_slop_dispatch.urllib.request.urlopen")
@patch("ai_slop_dispatch.boto3.client")
def test_video_edit_upload_slash_command_prefills_modal(mock_boto, mock_urlopen):
    mock_boto.return_value = MagicMock()
    mock_urlopen.return_value.__enter__.return_value.read.return_value = b'{"ok": true}'

    response = ai_slop_dispatch.dispatch(
        _slack_event(
            "-v 12 --upload --extend-video https://example.com/source.mp4 keep going",
            trigger_id="trig",
        ),
        None,
    )

    assert response["statusCode"] == "200"
    request = mock_urlopen.call_args.args[0]
    payload = json.loads(request.data.decode("utf-8"))
    blocks = {block["block_id"]: block for block in payload["view"]["blocks"]}
    assert blocks["video_op_block"]["element"]["initial_option"]["value"] == "extend"
    assert "source_video_block" in blocks
    assert blocks["source_video_block"]["optional"] is True
    assert blocks["source_video_block"]["element"]["filetypes"] == ["mp4", "mov", "webm"]
    assert blocks["source_video_block"]["element"]["max_files"] == 1
    assert (
        blocks["video_url_block"]["element"]["initial_value"]
        == "https://example.com/source.mp4"
    )
    assert "reference_role_block" not in blocks
    assert "files_block" not in blocks
    assert blocks["prompt_block"]["element"]["initial_value"] == "keep going"


@patch.dict("os.environ", {
    "AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic",
    "SLACK_BOT_TOKEN": "xoxb-token",
})
@patch("ai_slop_dispatch.urllib.request.urlopen")
@patch("ai_slop_dispatch.boto3.client")
def test_video_operation_select_updates_modal_fields(mock_boto, mock_urlopen):
    mock_boto.return_value = MagicMock()
    mock_urlopen.return_value.__enter__.return_value.read.return_value = b'{"ok": true}'
    metadata = {
        "response_url": "https://hooks.slack.example/dispatch",
        "channel_id": "C123",
        "channel_name": "general",
        "user": "alice",
        "mode": "video",
    }
    payload = {
        "type": "block_actions",
        "actions": [
            {
                "action_id": "video_op",
                "selected_option": {"value": "edit"},
            }
        ],
        "view": {
            "id": "V123",
            "hash": "h123",
            "callback_id": "ai_slop_upload",
            "private_metadata": json.dumps(metadata),
            "state": {
                "values": {
                    "prompt_block": {"prompt": {"value": "make it rain"}},
                    "backend_block": {
                        "backend": {"selected_option": {"value": "grok"}}
                    },
                    "duration_block": {"duration": {"value": "12"}},
                    "reference_role_block": {
                        "reference_role": {"selected_option": {"value": "start"}}
                    },
                    "files_block": {"files": {"files": []}},
                }
            },
        },
    }

    response = ai_slop_dispatch.dispatch(_interaction_request(payload), None)

    assert response["statusCode"] == "200"
    request = mock_urlopen.call_args.args[0]
    update = json.loads(request.data.decode("utf-8"))
    assert request.full_url.endswith("/views.update")
    assert update["view_id"] == "V123"
    assert update["hash"] == "h123"
    blocks = {block["block_id"]: block for block in update["view"]["blocks"]}
    assert blocks["video_op_block"]["element"]["initial_option"]["value"] == "edit"
    assert blocks["prompt_block"]["element"]["initial_value"] == "make it rain"
    assert blocks["duration_block"]["element"]["initial_value"] == "12"
    assert "video_url_block" in blocks
    assert "source_video_block" in blocks
    assert "reference_role_block" not in blocks
    assert "files_block" not in blocks


@patch.dict("os.environ", {
    "AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic",
    "SLACK_BOT_TOKEN": "xoxb-token",
})
@patch("ai_slop_dispatch.urllib.request.urlopen")
@patch("ai_slop_dispatch.boto3.client")
def test_upload_slash_command_accepts_ios_smart_dash(mock_boto, mock_urlopen):
    mock_boto.return_value = MagicMock()
    mock_urlopen.return_value.__enter__.return_value.read.return_value = b'{"ok": true}'

    response = ai_slop_dispatch.dispatch(
        _slack_event("-i \u2014upload make it strange", trigger_id="trig"),
        None,
    )

    assert response["statusCode"] == "200"
    mock_boto.return_value.publish.assert_not_called()
    request = mock_urlopen.call_args.args[0]
    payload = json.loads(request.data.decode("utf-8"))
    view = payload["view"]
    metadata = json.loads(view["private_metadata"])
    assert metadata["mode"] == "image"
    prompt_block = next(block for block in view["blocks"] if block["block_id"] == "prompt_block")
    assert prompt_block["element"]["initial_value"] == "make it strange"


@patch.dict("os.environ", {
    "AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic",
    "SLACK_BOT_TOKEN": "xoxb-token",
})
@patch("ai_slop_dispatch.urllib.request.urlopen")
@patch("ai_slop_dispatch.boto3.client")
def test_bare_image_edit_slash_command_opens_modal(mock_boto, mock_urlopen):
    mock_boto.return_value = MagicMock()
    mock_urlopen.return_value.__enter__.return_value.read.return_value = b'{"ok": true}'

    response = ai_slop_dispatch.dispatch(
        _slack_event("-i --edit make this watercolor", trigger_id="trig"),
        None,
    )

    assert response["statusCode"] == "200"
    mock_boto.return_value.publish.assert_not_called()
    request = mock_urlopen.call_args.args[0]
    payload = json.loads(request.data.decode("utf-8"))
    view = payload["view"]
    metadata = json.loads(view["private_metadata"])
    assert metadata["mode"] == "image"
    prompt_block = next(block for block in view["blocks"] if block["block_id"] == "prompt_block")
    assert prompt_block["element"]["initial_value"] == "make this watercolor"


@patch.dict("os.environ", {"AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic"})
@patch("ai_slop_dispatch.urllib.request.urlopen")
@patch("ai_slop_dispatch.boto3.client")
def test_image_edit_url_slash_command_does_not_open_modal(mock_boto, mock_urlopen):
    mock_sns = MagicMock()
    mock_boto.return_value = mock_sns
    mock_sns.publish.return_value = {"MessageId": "abc"}

    ai_slop_dispatch.dispatch(
        _slack_event(
            "-i --edit https://example.com/cat.png make this watercolor",
            trigger_id="trig",
        ),
        None,
    )

    mock_urlopen.assert_not_called()
    inner = json.loads(mock_sns.publish.call_args.kwargs["Message"])
    sns_msg = json.loads(inner["default"])
    assert sns_msg["prompt"] == "-i --edit https://example.com/cat.png make this watercolor"


def _interaction_request(payload: dict):
    return {
        "path": "/slack/interactions",
        "body": urllib.parse.urlencode({"payload": json.dumps(payload)}),
    }


def _video_source_submission_payload(
    video_op: str,
    video_url: str,
    *,
    source_video_files: list[dict] | None = None,
    image_files: list[dict] | None = None,
):
    metadata = {
        "response_url": "https://hooks.slack.example/dispatch",
        "channel_id": "C123",
        "channel_name": "general",
        "user": "alice",
        "mode": "video",
    }
    return {
        "type": "view_submission",
        "view": {
            "callback_id": "ai_slop_upload",
            "private_metadata": json.dumps(metadata),
            "state": {
                "values": {
                    "prompt_block": {"prompt": {"value": "make it rain"}},
                    "backend_block": {
                        "backend": {"selected_option": {"value": "grok"}}
                    },
                    "duration_block": {"duration": {"value": "12"}},
                    "video_op_block": {
                        "video_op": {"selected_option": {"value": video_op}}
                    },
                    "video_url_block": {
                        "video_url": {"value": video_url}
                    },
                    "source_video_block": {
                        "source_video": {"files": source_video_files or []}
                    },
                    "files_block": {"files": {"files": image_files or []}},
                }
            },
        },
    }


@patch.dict("os.environ", {"AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic"})
@patch("ai_slop_dispatch.boto3.client")
def test_upload_modal_submission_publishes_sns(mock_boto):
    mock_sns = MagicMock()
    mock_boto.return_value = mock_sns
    mock_sns.publish.return_value = {"MessageId": "abc"}
    metadata = {
        "response_url": "https://hooks.slack.example/dispatch",
        "channel_id": "C123",
        "channel_name": "general",
        "user": "alice",
        "mode": "video",
    }
    payload = {
        "type": "view_submission",
        "view": {
            "callback_id": "ai_slop_upload",
            "private_metadata": json.dumps(metadata),
            "state": {
                "values": {
                    "prompt_block": {"prompt": {"value": "slow push in"}},
                    "backend_block": {"backend": {"selected_option": {"value": "grok"}}},
                    "duration_block": {"duration": {"value": "10"}},
                    "reference_role_block": {
                        "reference_role": {"selected_option": {"value": "start"}}
                    },
                    "files_block": {"files": {"files": [{"id": "F123"}]}},
                }
            },
        },
    }

    response = ai_slop_dispatch.dispatch(_interaction_request(payload), None)

    assert response["statusCode"] == "200"
    inner = json.loads(mock_sns.publish.call_args.kwargs["Message"])
    sns_msg = json.loads(inner["default"])
    assert sns_msg["prompt"] == "-v 10 -b grok slow push in"
    assert sns_msg["reference_images"] == [
        {"source": "slack_file", "value": "F123", "role": "start"}
    ]


@patch.dict("os.environ", {"AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic"})
@patch("ai_slop_dispatch.boto3.client")
def test_upload_modal_video_edit_submission_publishes_command_without_files(mock_boto):
    mock_sns = MagicMock()
    mock_boto.return_value = mock_sns
    mock_sns.publish.return_value = {"MessageId": "abc"}

    response = ai_slop_dispatch.dispatch(
        _interaction_request(
            _video_source_submission_payload("edit", "https://example.com/source.mp4")
        ),
        None,
    )

    assert response["statusCode"] == "200"
    inner = json.loads(mock_sns.publish.call_args.kwargs["Message"])
    sns_msg = json.loads(inner["default"])
    assert (
        sns_msg["prompt"]
        == "-v 12 -b grok --edit-video https://example.com/source.mp4 make it rain"
    )
    assert sns_msg["reference_images"] == []


@patch.dict("os.environ", {"AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic"})
@patch("ai_slop_dispatch.boto3.client")
def test_upload_modal_video_extend_submission_publishes_command_without_files(mock_boto):
    mock_sns = MagicMock()
    mock_boto.return_value = mock_sns
    mock_sns.publish.return_value = {"MessageId": "abc"}

    response = ai_slop_dispatch.dispatch(
        _interaction_request(
            _video_source_submission_payload("extend", "https://example.com/source.mp4")
        ),
        None,
    )

    assert response["statusCode"] == "200"
    inner = json.loads(mock_sns.publish.call_args.kwargs["Message"])
    sns_msg = json.loads(inner["default"])
    assert (
        sns_msg["prompt"]
        == "-v 12 -b grok --extend-video https://example.com/source.mp4 make it rain"
    )
    assert sns_msg["reference_images"] == []


@patch.dict("os.environ", {"AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic"})
@patch("ai_slop_dispatch.boto3.client")
def test_upload_modal_video_edit_submission_accepts_uploaded_source_video(mock_boto):
    mock_sns = MagicMock()
    mock_boto.return_value = mock_sns
    mock_sns.publish.return_value = {"MessageId": "abc"}

    response = ai_slop_dispatch.dispatch(
        _interaction_request(
            _video_source_submission_payload(
                "edit",
                "",
                source_video_files=[
                    {"id": "FV123", "mimetype": "video/mp4", "name": "source.mp4"}
                ],
            )
        ),
        None,
    )

    assert response["statusCode"] == "200"
    inner = json.loads(mock_sns.publish.call_args.kwargs["Message"])
    sns_msg = json.loads(inner["default"])
    assert sns_msg["prompt"] == "-v 12 -b grok make it rain"
    assert sns_msg["reference_images"] == []
    assert sns_msg["source_video"] == {
        "source": "slack_file",
        "value": "FV123",
        "mime_type": "video/mp4",
        "filename": "source.mp4",
        "role": "edit",
    }


@patch.dict("os.environ", {"AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic"})
@patch("ai_slop_dispatch.boto3.client")
def test_upload_modal_video_edit_submission_rejects_missing_source_video(mock_boto):
    mock_sns = MagicMock()
    mock_boto.return_value = mock_sns

    response = ai_slop_dispatch.dispatch(
        _interaction_request(_video_source_submission_payload("edit", "")),
        None,
    )

    body = json.loads(response["body"])
    assert body["response_action"] == "errors"
    assert "source_video_block" in body["errors"]
    mock_sns.publish.assert_not_called()


@patch.dict("os.environ", {"AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic"})
@patch("ai_slop_dispatch.boto3.client")
def test_upload_modal_video_edit_submission_rejects_invalid_url(mock_boto):
    mock_sns = MagicMock()
    mock_boto.return_value = mock_sns

    response = ai_slop_dispatch.dispatch(
        _interaction_request(_video_source_submission_payload("edit", "not-a-url")),
        None,
    )

    body = json.loads(response["body"])
    assert body["response_action"] == "errors"
    assert "video_url_block" in body["errors"]
    mock_sns.publish.assert_not_called()


@patch.dict("os.environ", {"AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic"})
@patch("ai_slop_dispatch.boto3.client")
def test_upload_modal_video_edit_submission_rejects_url_and_upload(mock_boto):
    mock_sns = MagicMock()
    mock_boto.return_value = mock_sns

    response = ai_slop_dispatch.dispatch(
        _interaction_request(
            _video_source_submission_payload(
                "edit",
                "https://example.com/source.mp4",
                source_video_files=[{"id": "FV123"}],
            )
        ),
        None,
    )

    body = json.loads(response["body"])
    assert body["response_action"] == "errors"
    assert "source_video_block" in body["errors"]
    mock_sns.publish.assert_not_called()
