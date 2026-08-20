"""Digest + template + mailer tests.

The watermark logic is what keeps the inbox honest, so the cases that matter are:
a failed send must not lose postings, and a successful one must not report them
twice.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_server.jobboard import digest as dg
from agent_server.jobboard import mailer, templates

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


def posting(pid="p1", company="Acme", title="AI Engineer", role="AI Engineer",
            domain="acme.com", logo=None, loc="NYC", posted=None, url="https://x/1"):
    return {"id": pid, "companyName": company, "companyDomain": domain,
            "companyLogoUrl": logo, "title": title, "matchedRole": role,
            "location": loc, "url": url, "postedAt": posted}


class FakePlatform:
    def __init__(self, postings=None, watermark=None):
        self.postings = postings if postings is not None else []
        self.watermark = watermark
        self.runs: dict[str, dict] = {}
        self.closed: list[tuple[str, dict]] = []
        self.fail_create = False

    def digest_watermark(self):
        return self.watermark

    def undigested_postings(self, since=None):
        return self.postings

    def create_digest_run(self, ws, we):
        if self.fail_create:
            raise dg.pc.PlatformError("create boom")
        run_id = f"dr_{len(self.runs)}"
        self.runs[run_id] = {"window_start": ws, "window_end": we}
        return run_id

    def close_digest_run(self, run_id, **kw):
        self.closed.append((run_id, kw))


@pytest.fixture
def env(monkeypatch):
    plat = FakePlatform()
    monkeypatch.setattr(dg.pc, "digest_watermark", plat.digest_watermark)
    monkeypatch.setattr(dg.pc, "undigested_postings", plat.undigested_postings)
    monkeypatch.setattr(dg.pc, "create_digest_run", plat.create_digest_run)
    monkeypatch.setattr(dg.pc, "close_digest_run", plat.close_digest_run)
    monkeypatch.setattr(dg.jb_db, "start_run", lambda *a, **k: "bk_1")
    monkeypatch.setattr(dg.jb_db, "finish_run", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(dg.mailer, "send_mail",
                        lambda subj, html, text, **k: sent.append((subj, html, text)))
    plat.sent = sent
    return plat


# ---------------------------------------------------------------------------
# The behaviour that protects the inbox
# ---------------------------------------------------------------------------

def test_sends_and_stamps_postings(env):
    env.postings = [posting("p1"), posting("p2", company="Ramp")]

    res = dg.send_digest()

    assert res.status == "sent"
    assert res.new_count == 2
    assert res.company_count == 2
    assert len(env.sent) == 1
    # Stamped with exactly the reported ids, so they can never be sent twice.
    _, kwargs = env.closed[-1]
    assert kwargs["status"] == "sent"
    assert kwargs["posting_ids"] == ["p1", "p2"]


def test_failed_send_leaves_postings_unstamped(env, monkeypatch):
    """A transient SMTP error must not swallow a role."""
    env.postings = [posting("p1")]
    monkeypatch.setattr(dg.mailer, "send_mail",
                        lambda *a, **k: (_ for _ in ()).throw(mailer.MailSendError("smtp down")))

    res = dg.send_digest()

    assert res.status == "failed"
    _, kwargs = env.closed[-1]
    assert kwargs["status"] == "failed"
    # THE point: no ids stamped -> they roll into the next digest.
    assert "posting_ids" not in kwargs or not kwargs.get("posting_ids")


def test_unconfigured_smtp_fails_without_losing_postings(env, monkeypatch):
    env.postings = [posting("p1")]
    monkeypatch.setattr(dg.mailer, "send_mail",
                        lambda *a, **k: (_ for _ in ()).throw(
                            mailer.MailNotConfigured("missing SMTP config")))

    res = dg.send_digest()

    assert res.status == "failed"
    assert "missing SMTP config" in res.error
    _, kwargs = env.closed[-1]
    assert not kwargs.get("posting_ids")


def test_empty_digest_is_skipped_not_sent(env):
    env.postings = []
    res = dg.send_digest()
    assert res.status == "skipped"
    assert env.sent == []
    _, kwargs = env.closed[-1]
    assert kwargs["status"] == "skipped"


def test_force_sends_heartbeat_when_empty(env):
    env.postings = []
    res = dg.send_digest(force=True)
    assert res.status == "sent"
    assert len(env.sent) == 1
    assert "nothing new" in env.sent[0][0].lower()


def test_watermark_is_passed_as_window_start(env):
    env.watermark = NOW
    env.postings = [posting("p1")]
    dg.send_digest()
    run = env.runs["dr_0"]
    assert run["window_start"] == NOW


def test_stamp_failure_after_send_is_reported(env, monkeypatch):
    """Mail delivered but stamping failed: a duplicate next time beats a drop."""
    env.postings = [posting("p1")]
    def boom(run_id, **kw):
        raise dg.pc.PlatformError("stamp boom")
    monkeypatch.setattr(dg.pc, "close_digest_run", boom)

    res = dg.send_digest()

    assert res.status == "failed"
    assert "stamp boom" in res.error
    assert len(env.sent) == 1          # the mail DID go out


def test_platform_read_failure_is_clean(env, monkeypatch):
    monkeypatch.setattr(dg.pc, "digest_watermark",
                        lambda: (_ for _ in ()).throw(dg.pc.PlatformError("down")))
    res = dg.send_digest()          # must not raise
    assert res.status == "failed"
    assert env.sent == []


def test_run_create_failure_is_clean(env):
    env.postings = [posting("p1")]
    env.fail_create = True
    res = dg.send_digest()
    assert res.status == "failed"
    assert env.sent == []


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def test_subject_line_shapes():
    assert templates.subject_line([]) == "Job Board — nothing new"
    assert templates.subject_line([posting()]) == "1 new role — Acme"
    two = [posting("p1"), posting("p2", company="Ramp")]
    assert templates.subject_line(two) == "2 new roles — Acme, Ramp"


def test_subject_line_truncates_many_companies():
    ps = [posting(f"p{i}", company=f"Co{i}") for i in range(5)]
    assert templates.subject_line(ps) == "5 new roles — Co0, Co1, Co2 +2"


def test_html_contains_total_and_roles():
    ps = [posting("p1", title="Forward Deployed Engineer"), posting("p2", company="Ramp")]
    html = templates.render_digest_html(ps)
    assert "2 new roles" in html
    assert "Forward Deployed Engineer" in html
    assert "Acme" in html and "Ramp" in html
    assert "across 2 companies" in html


def test_html_escapes_hostile_content():
    """A malicious job title must not inject markup into the email."""
    ps = [posting(title='<script>alert(1)</script>', company='<b>Evil</b>')]
    html = templates.render_digest_html(ps)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<b>Evil</b>" not in html


def test_logo_url_resolution():
    """Clearbit's logo API was retired and its host no longer resolves, so a
    favicon service is used instead — verified reachable before switching."""
    assert templates.logo_url(posting(domain="acme.com")) == (
        "https://icons.duckduckgo.com/ip3/acme.com.ico"
    )
    assert templates.logo_url(posting(logo="https://x/l.png")) == "https://x/l.png"
    assert templates.logo_url(posting(domain=None)) is None


def test_no_dead_logo_host_anywhere():
    """Regression: clearbit must not creep back into the rendered email."""
    html = templates.render_digest_html([posting(domain="acme.com")])
    assert "clearbit" not in html


def test_lettermark_used_when_no_logo():
    html = templates.render_digest_html([posting(domain=None, company="Zeta")])
    assert "Z" in html
    assert "clearbit" not in html


def test_lettermark_colour_is_stable_per_company():
    a = templates.render_digest_html([posting(domain=None, company="Acme")])
    b = templates.render_digest_html([posting(domain=None, company="Acme")])
    assert a == b


def test_postings_grouped_under_one_company_card():
    ps = [posting("p1", title="AI Engineer"), posting("p2", title="Data Scientist")]
    html = templates.render_digest_html(ps)
    assert html.count(">Acme<") == 1          # one card, two rows
    assert "2 new roles" in html


def test_date_rendering_is_human():
    today = datetime.now(timezone.utc).isoformat()
    html = templates.render_digest_html([posting(posted=today)])
    assert "posted today" in html
    # An unparseable date must not crash or leak a raw value.
    assert templates._fmt_date("garbage") is None
    assert templates._fmt_date(None) is None


def test_text_alternative_has_every_url():
    ps = [posting("p1", url="https://x/1"), posting("p2", company="Ramp", url="https://x/2")]
    text = templates.render_digest_text(ps)
    assert "https://x/1" in text and "https://x/2" in text
    assert "2 new roles" in text


def test_empty_renders_do_not_crash():
    assert "No new roles" in templates.render_digest_html([])
    assert "No new roles" in templates.render_digest_text([])


# ---------------------------------------------------------------------------
# Mailer config
# ---------------------------------------------------------------------------

# CONFIG is a frozen dataclass (by design — it is read-only at runtime), so
# these swap in a replacement instance rather than mutating fields in place.
import dataclasses


def _config_with(**overrides):
    return dataclasses.replace(mailer.CONFIG, **overrides)


def test_send_mail_raises_when_unconfigured(monkeypatch):
    monkeypatch.setattr(mailer, "CONFIG", _config_with(
        jobboard_smtp_host="smtp.x.com", jobboard_smtp_user=None,
        jobboard_smtp_app_password="p", jobboard_digest_to="to@x.com"))
    with pytest.raises(mailer.MailNotConfigured, match="JOBBOARD_SMTP_USER"):
        mailer.send_mail("s", "<p>h</p>", "t")


def test_is_configured_true_when_all_present(monkeypatch):
    monkeypatch.setattr(mailer, "CONFIG", _config_with(
        jobboard_smtp_host="smtp.x.com", jobboard_smtp_user="u",
        jobboard_smtp_app_password="p", jobboard_digest_to="to@x.com"))
    assert mailer.is_configured() is True


@pytest.mark.parametrize("missing", [
    "jobboard_smtp_host", "jobboard_smtp_user",
    "jobboard_smtp_app_password", "jobboard_digest_to",
])
def test_is_configured_requires_every_field(monkeypatch, missing):
    full = {"jobboard_smtp_host": "smtp.x.com", "jobboard_smtp_user": "u",
            "jobboard_smtp_app_password": "p", "jobboard_digest_to": "to@x.com"}
    full[missing] = None
    monkeypatch.setattr(mailer, "CONFIG", _config_with(**full))
    assert mailer.is_configured() is False


def test_send_mail_builds_multipart_alternative(monkeypatch):
    """text/plain must come first — html-only mail scores badly with filters."""
    captured = {}

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def login(self, u, p): captured["login"] = u
        def send_message(self, msg): captured["msg"] = msg

    monkeypatch.setattr(mailer, "CONFIG", _config_with(
        jobboard_smtp_host="smtp.x.com", jobboard_smtp_port=465,
        jobboard_smtp_user="me@x.com", jobboard_smtp_app_password="p",
        jobboard_digest_to="to@x.com"))
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTP)

    mailer.send_mail("Subject here", "<p>hi</p>", "hi")

    msg = captured["msg"]
    assert msg["Subject"] == "Subject here"
    assert msg["To"] == "to@x.com"
    types = [part.get_content_type() for part in msg.walk()]
    assert "text/plain" in types and "text/html" in types
    assert types.index("text/plain") < types.index("text/html")
