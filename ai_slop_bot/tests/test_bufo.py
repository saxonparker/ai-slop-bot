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


def test_normalize_names_dedupes_and_rejects_non_bufo_names():
    names = bufo._normalize_names([  # pylint: disable=protected-access
        ":Bufo-Party:",
        "bufo-party.png",
        "Awesomebufo.gif",
        "bufopedia",
        "wrong-frog.png",
        "frog-party",
        "BUFO_party",
    ])

    assert names == ["bufo-party", "awesomebufo", "bufo_party"]


def test_fetch_reads_bufopedia_shell_and_bundled_catalog(monkeypatch):
    root = b"""
        <!doctype html>
        <script type="module" crossorigin src="/assets/index-test.js"></script>
        <link rel="icon" href="/bufo-excited.png">
    """
    catalog = (
        '[{"id":"awesomebufo","fileName":"Awesomebufo.png","displayName":"Awesomebufo",'
        '"imageUrl":"https://cdn/Awesomebufo.png"},'
        '{"id":"party-bufo","fileName":"party-bufo.gif","displayName":"Party bufo",'
        '"imageUrl":"https://cdn/party-bufo.gif"},'
        '{"id":"se-or-bufo","fileName":"se%C3%B1or-bufo.png","displayName":"Senor bufo",'
        '"imageUrl":"https://cdn/se%C3%B1or-bufo.png"},'
        '{"id":"wrong-frog","fileName":"wrong-frog.png","displayName":"Wrong frog",'
        '"imageUrl":"https://cdn/wrong-frog.png"}]'
    )
    bundle = f"const ic=JSON.parse(`{catalog}`);".encode("utf-8")

    def fake_urlopen(request, timeout):  # pylint: disable=unused-argument
        url = getattr(request, "full_url", request)
        if url == bufo.BUFO_CATALOG_URL:
            return _FakeResponse(root)
        if url == "https://bufopedia.com/assets/index-test.js":
            return _FakeResponse(bundle)
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(bufo.urllib.request, "urlopen", fake_urlopen)

    assert bufo._fetch_bufo_emoji_names() == [  # pylint: disable=protected-access
        "bufo-excited",
        "awesomebufo",
        "party_bufo",
        "se_or_bufo",
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
