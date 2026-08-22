import importlib
import sys


class FakePage:
    def __init__(self, url, title="", body=""):
        self.url = url
        self._title = title
        self._body = body

    def title(self):
        return self._title

    def inner_text(self, _selector):
        return self._body


def _worker(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["worker_linkedin.py", "li-test"])
    import worker_linkedin
    return importlib.reload(worker_linkedin)


def test_linkedin_authwall_fails_closed(monkeypatch):
    worker = _worker(monkeypatch)
    page = FakePage("https://www.linkedin.com/authwall?sessionRedirect=x", "Sign Up | LinkedIn")
    assert worker.auth_required(page)


def test_linkedin_authenticated_job_page_is_allowed(monkeypatch):
    worker = _worker(monkeypatch)
    page = FakePage("https://www.linkedin.com/jobs/view/123", "Software Engineer | LinkedIn", "Job details")
    assert not worker.auth_required(page)
