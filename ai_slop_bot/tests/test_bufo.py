"""Tests for Bufo emoji catalog loading and output sanitization."""

import sys

sys.path.append(".")

import bufo  # noqa: E402  pylint: disable=wrong-import-position


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self._payload


def _reset_cache(monkeypatch):
    monkeypatch.setattr(bufo, "_BUFO_EMOJI_NAMES", None)


def test_normalize_names_lowercases_dedupes_and_drops_invalid():
    names = bufo._normalize_names([  # pylint: disable=protected-access
        ":Bufo-Party:",
        "bufo-party.png",
        "Awesomebufo.gif",
        "bigbufo_0_0.png",
        "all-the-bufo/bufo-sad.png",
        "señor-bufo.png",
        "bufo+1.png",
        "bufo's-father.png",
    ])

    # Hyphens and underscores are preserved verbatim; names with characters
    # Slack emoji cannot contain are dropped.
    assert names == ["bufo-party", "awesomebufo", "bigbufo_0_0", "bufo-sad"]


def test_fetch_parses_all_the_bufo_index(monkeypatch):
    index = (
        "| name | image |\n"
        "| - | - |\n"
        "| bufo-party.png | ![bufo-party.png](all-the-bufo/bufo-party.png) |\n"
        "| bigbufo_0_0.png | ![bigbufo_0_0.png](all-the-bufo/bigbufo_0_0.png) |\n"
        "| Awesomebufo.gif | ![Awesomebufo.gif](all-the-bufo/Awesomebufo.gif) |\n"
        "| señor-bufo.png | ![señor-bufo.png](all-the-bufo/señor-bufo.png) |\n"
    ).encode("utf-8")

    def fake_urlopen(request, timeout):  # pylint: disable=unused-argument
        url = getattr(request, "full_url", request)
        assert url == bufo.BUFO_CATALOG_URL
        return _FakeResponse(index)

    monkeypatch.setattr(bufo.urllib.request, "urlopen", fake_urlopen)

    assert bufo._fetch_bufo_emoji_names() == [  # pylint: disable=protected-access
        "bufo-party",
        "bigbufo_0_0",
        "awesomebufo",
    ]


def test_get_names_falls_back_to_vendored_list_when_fetch_fails(monkeypatch, capsys):
    _reset_cache(monkeypatch)

    def fail_urlopen(*_, **__):
        raise OSError("network unavailable")

    monkeypatch.setattr(bufo.urllib.request, "urlopen", fail_urlopen)

    names = bufo.get_bufo_emoji_names()

    assert names == bufo._load_fallback_bufo_emoji_names()  # pylint: disable=protected-access
    assert "BUFO CATALOG FETCH FAILED" in capsys.readouterr().out


def test_get_names_caches_fetch_result(monkeypatch):
    _reset_cache(monkeypatch)
    calls = []

    def fake_fetch():
        calls.append(True)
        return ["bufo-party"]

    monkeypatch.setattr(bufo, "_fetch_bufo_emoji_names", fake_fetch)

    assert bufo.get_bufo_emoji_names() == ["bufo-party"]
    assert bufo.get_bufo_emoji_names() == ["bufo-party"]
    assert calls == [True]


def test_sanitize_bufo_output_filters_unknown_text_and_defaults():
    names = {"bufo", "bufo-party", "bufo-sad"}

    assert (
        bufo.sanitize_bufo_output("prose :bufo-party: :foo::bufo-sad: done", names)
        == ":bufo-party: :bufo-sad:"
    )
    assert bufo.sanitize_bufo_output("plain prose :foo:", names) == ":bufo:"
    assert bufo.sanitize_bufo_output(":foo:", {"bufo-sad"}) == ":bufo-sad:"
    assert bufo.sanitize_bufo_output(":foo:", set()) == ":bufo:"
