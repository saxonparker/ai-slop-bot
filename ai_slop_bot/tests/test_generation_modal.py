"""Slack form behavior and dispatch integration, without live Slack/provider calls."""

import copy
import json
from pathlib import Path
import sys
from unittest.mock import patch
import urllib.parse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ai_slop_dispatch"))
import ai_slop_dispatch
import generation_modal as modal
import parsing


PARAMS = {
    "response_url": "https://hooks.slack.example/response", "channel_id": "C123",
    "channel_name": "general", "user_name": "alice", "trigger_id": "trigger",
}


def inputs(view):
    return {block["element"]["action_id"]: block for block in view["blocks"] if block["type"] == "input"}


def filled(view, **changes):
    """Populate the Slack submission state, including untouched initial values."""
    view = copy.deepcopy(view)
    state = {}
    for action, block in inputs(view).items():
        element = block["element"]
        kind = element["type"]
        if kind == "static_select":
            selected = changes.get(action, element["initial_option"]["value"])
            value = {"selected_option": {"value": selected} if selected else None}
        elif kind in ("multi_static_select", "checkboxes"):
            selected = changes.get(action, [option["value"] for option in element.get("initial_options", [])])
            value = {"selected_options": [{"value": option} for option in selected]}
        elif kind == "file_input":
            value = {"files": changes.get(action, [])}
        else:
            value = {"value": changes.get(action, element.get("initial_value", ""))}
        state[block["block_id"]] = {action: value}
    view.update(id="V123", hash="hash123", state={"values": state})
    return view


def opened(mode="text", **options):
    return modal.initial_view(PARAMS, {"mode": mode, "prompt": "a fox", **options})


def switch(view, mode=None, **changes):
    actions = [{"action_id": f"mode_{mode}", "value": mode}] if mode else []
    return modal.updated_view(filled(view, **changes), actions)


def interaction(payload):
    return {"path": "/slack/interactions", "body": urllib.parse.urlencode({"payload": json.dumps(payload)})}


@pytest.mark.parametrize("command,mode", [("", "text"), ("  ", "text"), ("--modal", "text"),
                                          ("-i", "image"), ("-v", "video"),
                                          ("-i --upload", "image"), ("-v --upload", "video")])
def test_slash_entry_points_open_unified_form(command, mode):
    event = {"body": urllib.parse.urlencode({**PARAMS, "text": command})}
    with patch.object(ai_slop_dispatch, "_slack_api_post") as slack, patch.object(ai_slop_dispatch, "_publish") as publish:
        response = ai_slop_dispatch.dispatch(event, None)
    assert json.loads(response["body"]) == {}
    method, payload = slack.call_args.args
    assert method == "views.open"
    assert payload["trigger_id"] == "trigger"
    assert payload["view"]["callback_id"] == modal.CALLBACK_ID
    assert json.loads(payload["view"]["private_metadata"])["mode"] == mode
    publish.assert_not_called()


def test_help_still_available_and_no_modal_without_trigger():
    with patch.object(ai_slop_dispatch, "_slack_api_post") as slack:
        response = ai_slop_dispatch.dispatch({"body": urllib.parse.urlencode({**PARAMS, "text": "--help"})}, None)
        missing = ai_slop_dispatch.dispatch({"body": "text="}, None)
    assert json.loads(response["body"])["text"] == ai_slop_dispatch.HELP_TEXT
    assert "trigger_id" in json.loads(missing["body"])["text"]
    slack.assert_not_called()


@pytest.mark.parametrize("mode,backend", [("text", "anthropic"), ("image", "openai"), ("video", "gemini")])
def test_form_respects_deployment_provider_defaults(monkeypatch, mode, backend):
    monkeypatch.setenv(f"{mode.upper()}_BACKEND", backend)
    assert inputs(opened(mode))["backend"]["element"]["initial_option"]["value"] == backend


@pytest.mark.parametrize("mode", ["text", "image", "video"])
def test_generation_without_uploads_queues_structured_request(mode):
    view = filled(opened(mode))
    with patch.object(ai_slop_dispatch, "_publish") as publish:
        response = ai_slop_dispatch.dispatch(interaction({"type": "view_submission", "view": view}), None)
    assert json.loads(response["body"]) == {}
    message = publish.call_args.args[0]
    assert message["source"] == "generation_modal"
    assert message["generation"]["mode"] == mode
    assert message["generation"]["prompt"] == "a fox"
    assert message["reference_images"] == []
    assert message["user"] == "alice"
    assert message["channel_id"] == "C123"


def test_switches_retain_prompt_settings_and_uploaded_files():
    image = opened("image", backend="openai", image_op="edit")
    text = switch(image, "text", prompt="paint [secret] this", urls="https://example.com/ref.png",
                  upload_images=[{"id": "F123", "name": "fox.png"}])
    video = switch(text, "video", backend="anthropic", search="required", potato=["on"])
    image = switch(video, "image", duration="12")
    fields = inputs(image)
    assert fields["prompt"]["element"]["initial_value"] == "paint [secret] this"
    assert fields["backend"]["element"]["initial_option"]["value"] == "openai"
    assert fields["urls"]["element"]["initial_value"] == "https://example.com/ref.png"
    assert fields["keep_images"]["element"]["initial_options"][0]["value"] == "F123"
    errors, message = modal.submission(filled(image))
    assert not errors
    assert message["reference_images"] == [
        {"source": "url", "value": "https://example.com/ref.png", "role": "edit"},
        {"source": "slack_file", "value": "F123", "role": "edit"},
    ]
    text = switch(image, "text")
    errors, message = modal.submission(filled(text))
    assert not errors
    assert message["generation"]["search"] == "required"
    assert message["generation"]["potato"] is True
    assert message["generation"]["backend"] == "anthropic"
    assert message["reference_images"] == []


def test_uploads_are_removable_and_not_duplicated_on_updates():
    view = switch(opened("image"), upload_images=[{"id": "F123", "name": "fox.png"}])
    view = switch(view, upload_images=[{"id": "F123", "name": "fox.png"}])
    assert len(inputs(view)["keep_images"]["element"]["options"]) == 1
    errors, message = modal.submission(filled(view, keep_images=[], upload_images=[{"id": "F456"}]))
    assert not errors
    assert [ref["value"] for ref in message["reference_images"]] == ["F456"]


def test_provider_switch_removes_unsupported_controls_and_stale_values():
    video = opened("video", voices=["eve"], custom_voices="custom_01", role="reference", duration="10")
    gemini = switch(video, backend="gemini")
    fields = inputs(gemini)
    assert set(fields).isdisjoint({"video_op", "resolution", "voices", "custom_voices", "role"})
    assert [option["value"] for option in fields["duration"]["element"]["options"]] == ["4", "6", "8"]
    assert fields["duration"]["element"]["initial_option"]["value"] == "8"
    errors, message = modal.submission(filled(gemini, upload_images=[{"id": "F123"}]))
    assert not errors
    assert set(message["generation"]).isdisjoint({"resolution", "voices", "video_op"})
    assert message["reference_images"][0]["role"] == "start"


def test_changing_reference_role_resets_duration_input_to_supported_value():
    video = opened("video", duration="15")
    updated = switch(video, role="reference")
    assert inputs(video)["duration"]["block_id"] != inputs(updated)["duration"]["block_id"]
    assert inputs(updated)["duration"]["element"]["initial_option"]["value"] == "10"


@pytest.mark.parametrize("change", [{"backend": "gemini"}, {"output": "emoji"}, {"output": "bufo"}])
def test_text_provider_and_style_control_search(change):
    updated = switch(opened(search="required"), **change)
    assert "search" not in inputs(updated)
    errors, message = modal.submission(filled(updated))
    assert not errors
    assert message["generation"]["search"] == "off"


def test_video_operation_switch_preserves_source_and_excludes_generation_settings():
    view = switch(opened("video", voices=["eve"]), video_op="edit", upload_images=[{"id": "FI"}])
    assert "upload_images" not in inputs(view)
    assert "voices" not in inputs(view)
    assert "resolution" not in inputs(view)
    view = switch(view, video_op="generate", upload_videos=[{"id": "FV", "name": "source.mp4"}])
    assert inputs(view)["keep_images"]["element"]["initial_options"][0]["value"] == "FI"
    view = switch(view, video_op="extend")
    errors, message = modal.submission(filled(view))
    assert not errors
    assert message["reference_images"] == []
    assert message["source_video"] == {"source": "slack_file", "value": "FV", "role": "extend"}
    assert message["generation"]["video_op"] == "extend"
    assert "voices" not in message["generation"]
    assert "resolution" not in message["generation"]


@pytest.mark.parametrize("mode,options,changes,error_action", [
    ("text", {}, {"prompt": "  "}, "prompt"),
    ("image", {"image_op": "edit"}, {}, "upload_images"),
    ("image", {}, {"urls": "https:///missing-host"}, "urls"),
    ("image", {}, {"urls": "ftp://example.com/ref.png"}, "urls"),
    ("image", {}, {"urls": "https://example.com/ref.png", "upload_images": ["F1", "F2", "F3"]}, "upload_images"),
    ("video", {}, {"upload_images": ["F1", "F2"]}, "upload_images"),
    ("video", {"role": "reference"}, {"duration": "15"}, "duration"),
    ("video", {}, {"duration": "0"}, "duration"),
    ("video", {"backend": "gemini"}, {"duration": "5"}, "duration"),
    ("video", {"video_op": "edit"}, {}, "upload_videos"),
    ("video", {"video_op": "edit"}, {"video_url": "https://example.com/a.mp4", "upload_videos": ["FV"]}, "upload_videos"),
    ("video", {}, {"voices": ["eve", "leo", "luna"], "custom_voices": "custom_voice"}, "custom_voices"),
    ("video", {}, {"custom_voices": "invalid!"}, "custom_voices"),
    ("video", {}, {"resolution": "bad"}, "resolution"),
])
def test_invalid_submissions_keep_form_open(mode, options, changes, error_action):
    view = filled(opened(mode, **options), **changes)
    with patch.object(ai_slop_dispatch, "_publish") as publish:
        response = ai_slop_dispatch.dispatch(interaction({"type": "view_submission", "view": view}), None)
    body = json.loads(response["body"])
    assert body["response_action"] == "errors"
    assert inputs(view)[error_action]["block_id"] in body["errors"]
    publish.assert_not_called()


def test_source_video_url_and_preset_and_custom_voices():
    errors, message = modal.submission(filled(opened("video", video_op="extend"), video_url="https://example.com/video.mp4"))
    assert not errors
    assert message["generation"]["video_url"] == "https://example.com/video.mp4"
    assert message["source_video"] is None
    errors, message = modal.submission(filled(opened("video"), voices=["eve"], custom_voices="eve, custom01 leo"))
    assert not errors
    assert message["generation"]["voices"] == ["eve", "custom01", "leo"]


def test_expired_form_and_queue_failure_do_not_close_form():
    view = filled(opened())
    metadata = json.loads(view["private_metadata"])
    metadata["opened_at"] -= 30 * 60
    stale = {**view, "private_metadata": json.dumps(metadata)}
    errors, message = modal.submission(stale)
    assert "expired" in errors["prompt_block"]
    assert message is None
    with patch.object(ai_slop_dispatch, "_publish", side_effect=RuntimeError("queue offline")):
        response = ai_slop_dispatch.dispatch(interaction({"type": "view_submission", "view": view}), None)
    assert "try Generate again" in json.loads(response["body"])["errors"]["prompt_block"]


def test_update_uses_slack_hash_and_never_publishes():
    view = filled(opened(), prompt="fox", backend="anthropic")
    with patch.object(ai_slop_dispatch, "_slack_api_post") as slack, patch.object(ai_slop_dispatch, "_publish") as publish:
        response = ai_slop_dispatch.dispatch(interaction({"type": "block_actions", "view": view,
            "actions": [{"action_id": "mode_image", "value": "image"}]}), None)
    assert json.loads(response["body"]) == {}
    method, request = slack.call_args.args
    assert method == "views.update"
    assert request["view_id"] == "V123" and request["hash"] == "hash123"
    assert json.loads(request["view"]["private_metadata"])["mode"] == "image"
    publish.assert_not_called()


def test_oversize_metadata_keeps_current_form_and_draft():
    view = filled(opened("image"), prompt="keep me", urls="https://example.com/" + "x" * 2900, upload_images=["F123"])
    updated = modal.updated_view(view, [{"action_id": "mode_video", "value": "video"}])
    assert updated["private_metadata"] == view["private_metadata"]
    assert inputs(updated)["upload_images"]["block_id"] == inputs(view)["upload_images"]["block_id"]
    assert "too large" in updated["blocks"][1]["elements"][0]["text"]
    assert "keep me" not in updated["private_metadata"]
    # Slack preserves these inputs because all input IDs are unchanged.
    errors, message = modal.submission({**updated, "state": view["state"]})
    assert not errors
    assert message["prompt"] == "keep me"
    assert message["reference_images"][-1]["value"] == "F123"


def test_command_prefills_all_media_and_text_options():
    options = modal.command_options("--modal -v 6 -r 720 --ref https://example.com/ref.png --voice eve --voice custom01 -p a fox",
                                    ai_slop_dispatch._normalize_flag_token)
    assert options == {"mode": "video", "prompt": "a fox", "duration": "6", "resolution": "720p", "role": "reference",
                       "potato": ["on"], "urls": "https://example.com/ref.png", "voices": ["eve"], "custom_voices": "custom01"}
    options = modal.command_options("--modal -b anthropic -s -e -p hello", ai_slop_dispatch._normalize_flag_token)
    assert options["backend"] == "anthropic" and options["search"] == "required"
    assert options["output"] == "emoji" and options["potato"] == ["on"]


def test_prompt_is_literal_except_bracket_directives():
    prompt = "explain -v -pay 100 --credit alice 500 [quietly] ]FYI["
    errors, message = modal.submission(filled(opened(), prompt=prompt))
    assert not errors
    parsed = parsing.parse_generation_form(message["generation"])
    assert parsed.mode == "text" and parsed.pay_amount is None and parsed.credit_target is None
    assert "-pay 100" in parsed.prompt_text and "quietly" in parsed.prompt_text
    assert "FYI" not in parsed.prompt_text and "FYI" in parsed.display_text
    assert parsed.search_mode == "auto"


@pytest.mark.parametrize("output", ["emoji", "bufo"])
def test_emoji_options_reach_parsed_command(output):
    _, message = modal.submission(filled(opened(output=output)))
    parsed = parsing.parse_generation_form(message["generation"])
    assert parsed.emoji_mode == (output == "emoji")
    assert parsed.bufo_mode == (output == "bufo")
    assert parsed.search_mode == "off"
    if output == "emoji":
        assert "Respond only with emojis" in parsed.prompt_text
        assert "Respond only with emojis" not in parsed.display_text
