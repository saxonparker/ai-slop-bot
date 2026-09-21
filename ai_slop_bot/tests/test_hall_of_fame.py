"""Shared curation persists exact media keys without touching generated files."""

import base64
import json
from pathlib import Path
import sys
from unittest.mock import patch
from urllib.parse import quote, urlencode

from botocore.exceptions import ClientError
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ai_slop_dispatch"))

import ai_slop_bot
import ai_slop_dispatch
import hall_of_fame
import slack


KEY = 'dalle/a_"great"_cat_🏆_ABC.jpeg'


@pytest.fixture
def storage(monkeypatch):
    monkeypatch.setenv("HALL_OF_FAME_TABLE_NAME", "test-hall")
    monkeypatch.setenv("GALLERY_MEDIA_TABLE_NAME", "test-media")
    with patch("hall_of_fame.boto3.resource") as resource, patch("hall_of_fame.boto3.client") as client:
        table = resource.return_value.Table.return_value
        table.scan.return_value = {"Items": []}
        yield table, client.return_value


def request(method, body=None, **kwargs):
    return {"path": hall_of_fame.API_PATH, "httpMethod": method,
            "body": json.dumps(body) if body is not None else "", **kwargs}


def test_public_api_does_not_require_slack_signature(storage, monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "a-secret")
    table, s3 = storage
    for featured in (True, True, False, False):
        response = ai_slop_dispatch.dispatch(request("PUT", {"key": KEY, "featured": featured}), None)
        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"key": KEY, "featured": featured}
        assert response["headers"]["Cache-Control"] == "no-store"
    assert table.put_item.call_count == 2
    table.put_item.assert_called_with(Item={"media_key": KEY})
    assert table.delete_item.call_count == 2
    table.delete_item.assert_called_with(Key={"media_key": KEY})
    assert s3.head_object.call_count == 2
    s3.delete_object.assert_not_called()
    s3.put_object.assert_not_called()


def test_scan_reads_all_pages(storage):
    table, _ = storage
    table.scan.side_effect = [
        {"Items": [{"media_key": KEY}], "LastEvaluatedKey": {"media_key": KEY}},
        {"Items": [{"media_key": "dalle/video.mp4"}]},
    ]
    response = hall_of_fame.handle_http(request("GET"))
    assert json.loads(response["body"])["keys"] == [KEY, "dalle/video.mp4"]
    assert table.scan.call_args.kwargs["ExclusiveStartKey"] == {"media_key": KEY}
    assert table.scan.call_args.kwargs["ConsistentRead"] is True


@pytest.mark.parametrize("body", [
    [], None, {}, {"key": KEY, "featured": "false"}, {"key": KEY, "featured": 1},
    {"key": "source-videos/a.mp4", "featured": True},
    {"key": "dalle/manifest.json", "featured": True},
    {"key": "https://example.com/cat.jpg", "featured": True},
    {"key": [KEY], "featured": True}, {"key": "dalle/" + "🏆" * 300 + ".jpg", "featured": True},
])
def test_invalid_requests_do_not_write(storage, body):
    table, s3 = storage
    assert hall_of_fame.handle_http(request("PUT", body))["statusCode"] == 400
    table.put_item.assert_not_called()
    table.delete_item.assert_not_called()
    s3.head_object.assert_not_called()


def test_missing_media_cannot_be_added_but_can_be_removed(storage):
    table, s3 = storage
    s3.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
    assert hall_of_fame.handle_http(request("PUT", {"key": KEY, "featured": True}))["statusCode"] == 400
    table.put_item.assert_not_called()
    assert hall_of_fame.handle_http(request("PUT", {"key": KEY, "featured": False}))["statusCode"] == 200


def test_unavailable_store_reports_failure(storage):
    table, _ = storage
    table.put_item.side_effect = RuntimeError("private infrastructure details")
    response = hall_of_fame.handle_http(request("PUT", {"key": KEY, "featured": True}))
    assert response["statusCode"] == 503
    assert "private infrastructure" not in response["body"]


def test_base64_request_and_disabled_feature(storage, monkeypatch):
    event = request("PUT", {"key": KEY, "featured": True})
    event.update(body=base64.b64encode(event["body"].encode()).decode(), isBase64Encoded=True)
    assert hall_of_fame.handle_http(event)["statusCode"] == 200
    monkeypatch.delenv("HALL_OF_FAME_TABLE_NAME")
    assert hall_of_fame.handle_http(request("GET"))["statusCode"] == 503


@pytest.mark.parametrize("featured", [True, False])
def test_slack_action_queues_curation_before_acknowledging(featured):
    action = slack.hall_of_fame_action(KEY, featured)["elements"][0]
    payload = {"type": "block_actions", "actions": [action], "response_url": "https://hooks.slack.com/example"}
    with patch("ai_slop_dispatch._publish") as publish:
        response = ai_slop_dispatch._handle_interaction({"body": urlencode({"payload": json.dumps(payload)})})
    assert int(response["statusCode"]) == 200
    publish.assert_called_once_with({"source": "hall_of_fame", "media_key": KEY,
                                    "featured": featured, "response_url": payload["response_url"]})


def test_unsigned_slack_curation_is_rejected(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "test-secret")
    with patch("ai_slop_dispatch._publish") as publish:
        response = ai_slop_dispatch.dispatch({"path": "/slack/interactions", "body": "payload={}"}, None)
    assert response["statusCode"] == 401
    publish.assert_not_called()


def test_curation_worker_skips_generation_and_billing(storage):
    event = {"Records": [{"Sns": {"Message": json.dumps({
        "source": "hall_of_fame", "media_key": KEY, "featured": False,
        "response_url": "https://hooks.slack.com/example",
    })}}]}
    with patch("ai_slop_bot.slack.post_hall_of_fame_result") as result, \
            patch("ai_slop_bot.budget.get_balance") as balance, \
            patch("ai_slop_bot.providers.get_image_provider") as provider:
        ai_slop_bot.ai_slop_bot(event, None)
    result.assert_called_once_with("https://hooks.slack.com/example", {"key": KEY, "featured": False})
    balance.assert_not_called()
    provider.assert_not_called()


def test_failure_preserves_slack_generation_post(storage):
    table, _ = storage
    table.delete_item.side_effect = RuntimeError("unavailable")
    event = {"Records": [{"Sns": {"Message": json.dumps({
        "source": "hall_of_fame", "media_key": KEY, "featured": False,
        "response_url": "https://hooks.slack.com/example",
    })}}]}
    with patch("slack.requests.post") as post:
        ai_slop_bot.ai_slop_bot(event, None)
    payload = json.loads(post.call_args.kwargs["data"])
    assert payload["replace_original"] is False
    assert payload["response_type"] == "ephemeral"
    assert "Could not save" in payload["text"]


@pytest.mark.parametrize("featured", [True, False])
def test_slack_confirmation_has_undo_and_gallery_link(featured):
    with patch("slack.requests.post") as post:
        slack.post_hall_of_fame_result("https://hooks.slack.com/example", {"key": KEY, "featured": featured})
    payload = post.call_args.kwargs["json"]
    assert payload["replace_original"] is False
    assert payload["response_type"] == "ephemeral"
    assert "#hall-of-fame" in payload["blocks"][0]["text"]["text"]
    undo = payload["blocks"][1]["elements"][0]
    assert undo["value"] == KEY
    assert undo["action_id"] == ("hall_of_fame_remove" if featured else "hall_of_fame_add")


def test_generated_images_have_no_visible_curation_controls(storage, monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "test-token")
    url = hall_of_fame.CLOUDFRONT + "/" + quote(KEY)
    assert hall_of_fame.key_from_url(url) == KEY
    with patch("slack.requests.post") as post:
        slack.post_image_response("https://hooks.slack.com/example", "alice", "a cat", url)
        payload = json.loads(post.call_args.kwargs["data"])
        assert [block["type"] for block in payload["blocks"]] == ["section", "image"]
        assert hall_of_fame.key_from_slack_message(payload) == KEY
        slack.post_image_response_in_thread("C123", "alice", "a cat", url, "123.456")
        payload = json.loads(post.call_args.kwargs["data"])
        assert [block["type"] for block in payload["blocks"]] == ["section", "image"]
        assert hall_of_fame.key_from_slack_message(payload) == KEY
        assert payload["thread_ts"] == "123.456"


def test_video_shortcut_resolves_registered_slack_file(storage):
    table, _ = storage
    url = hall_of_fame.CLOUDFRONT + "/dalle/a_video_ABC.mp4"
    hall_of_fame.register_slack_file("F123", url)
    table.put_item.assert_called_once_with(Item={"slack_file_id": "F123", "media_key": "dalle/a_video_ABC.mp4"})
    table.get_item.return_value = {"Item": table.put_item.call_args.kwargs["Item"]}
    assert hall_of_fame.key_from_slack_message({"files": [{"id": "F123"}]}) == "dalle/a_video_ABC.mp4"
    table.get_item.assert_called_once_with(Key={"slack_file_id": "F123"}, ConsistentRead=True)


def test_shortcut_does_not_guess_which_media_to_add(storage):
    table, _ = storage
    table.get_item.return_value = {}
    for message in ({"text": "hello"}, {"files": [{"id": "old-video"}]}, {"blocks": [
        {"image_url": hall_of_fame.CLOUDFRONT + "/dalle/one.jpeg"},
        {"image_url": hall_of_fame.CLOUDFRONT + "/dalle/two.jpeg"},
    ]}):
        assert hall_of_fame.key_from_slack_message(message) is None


def test_message_shortcut_is_queued_then_curates_media(storage):
    payload = {"type": "message_action", "callback_id": "hall_of_fame_add",
               "response_url": "https://hooks.slack.com/example",
               "message": {"blocks": [{"type": "image", "image_url": hall_of_fame.CLOUDFRONT + "/" + quote(KEY)}]}}
    with patch("ai_slop_dispatch._publish") as publish:
        response = ai_slop_dispatch._handle_interaction({"body": urlencode({"payload": json.dumps(payload)})})
    assert int(response["statusCode"]) == 200
    message = publish.call_args.args[0]
    assert message["source"] == "hall_of_fame_shortcut"
    with patch("slack.post_hall_of_fame_result") as result, patch("ai_slop_bot.budget.get_balance") as balance:
        ai_slop_bot.ai_slop_bot({"Records": [{"Sns": {"Message": json.dumps(message)}}]}, None)
    result.assert_called_once_with(payload["response_url"], {"key": KEY, "featured": True})
    balance.assert_not_called()


def test_unrecognized_shortcut_media_gets_private_gallery_fallback(storage):
    table, _ = storage
    message = {"source": "hall_of_fame_shortcut", "slack_message": {"text": "hello"},
               "response_url": "https://hooks.slack.com/example"}
    with patch("slack.post_ephemeral") as notice:
        ai_slop_bot.ai_slop_bot({"Records": [{"Sns": {"Message": json.dumps(message)}}]}, None)
    assert "gallery" in notice.call_args.args[1]
    table.put_item.assert_not_called()


def test_failed_confirmation_never_replaces_original_slack_post(storage):
    message = {"source": "hall_of_fame", "media_key": KEY, "featured": True,
               "response_url": "https://hooks.slack.com/example"}
    with patch("slack.post_hall_of_fame_result", side_effect=RuntimeError("Slack unavailable")), \
            patch("slack.requests.post") as post:
        ai_slop_bot.ai_slop_bot({"Records": [{"Sns": {"Message": json.dumps(message)}}]}, None)
    payload = json.loads(post.call_args.kwargs["data"])
    assert payload["replace_original"] is False
    assert payload["response_type"] == "ephemeral"


def test_video_upload_returns_file_id_without_posting_extra_controls(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "test-token")
    with patch("slack.requests.post") as post:
        post.return_value.json.side_effect = [
            {"ok": True, "file_id": "F123", "upload_url": "https://files.slack.com/upload"},
            {"ok": True},
        ]
        assert slack.post_video_response("C123", "alice", "surfing dog", b"video") == "F123"
    assert post.call_count == 3
    complete = post.call_args.kwargs["json"]
    assert complete["initial_comment"] == 'alice generated video: "surfing dog"'
    assert "blocks" not in complete
