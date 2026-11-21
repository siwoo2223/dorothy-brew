import requests

from receivables import site_agent
from receivables.site_agent import SiteAPI, SiteError


class FakeResp:
    status_code = 200
    text = "{}"

    def json(self):
        return {"ok": True}


class FlakySession:
    def __init__(self, fails):
        self.fails = fails
        self.calls = 0
        self.closed = 0

    def request(self, *a, **k):
        self.calls += 1
        if self.calls <= self.fails:
            raise requests.ConnectionError("Connection aborted. 10054")
        return FakeResp()

    def close(self):
        self.closed += 1


def test_retries_once_after_reset(monkeypatch):
    monkeypatch.setattr(site_agent.time, "sleep", lambda s: None)
    s = FlakySession(1)
    assert SiteAPI("https://x", "t", session=s).pull() == {"ok": True}
    assert s.calls == 2 and s.closed == 1


def test_gives_up_after_second_failure(monkeypatch):
    monkeypatch.setattr(site_agent.time, "sleep", lambda s: None)
    s = FlakySession(5)
    try:
        SiteAPI("https://x", "t", session=s).pull()
        assert False
    except SiteError:
        pass
    assert s.calls == 2
