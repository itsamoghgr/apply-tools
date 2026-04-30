"""Email open/click URL generation for outbound reach-out mail.

The encoding side lives here on the local backend (so we can embed metadata
like the reach_out_id at send time). The decoding + event-recording side
runs in the deployed [tracking-sidecar/](../tracking-sidecar/) service so
mail clients have a stable public URL to hit without ngrok-style abuse
interstitials.

Two env vars must match between this process and the sidecar:

  TRACKING_FERNET_KEY  — base64 Fernet key. Encoded URLs are decoded by the
                        sidecar; mismatched keys mean clicks redirect to /
                        and opens silently fail.
  TRACKING_API_TOKEN   — bearer token for the dashboard read endpoints
                        (/events, /aggregates). Used by `server.py` when it
                        proxies dashboard requests to the sidecar.

  TRACKING_BASE_URL    — the sidecar's public origin, e.g.
                        https://apply-tools-tracker.onrender.com
"""

from __future__ import annotations

import html as _html
import logging
import os
import re
from typing import Tuple

from cryptography.fernet import Fernet
from lxml import html as lxml_html

import pytracking
from pytracking import Configuration

logger = logging.getLogger("coverletter.tracking")

OPEN_PATH = "track/open/"
CLICK_PATH = "track/click/"


class TrackingNotConfigured(RuntimeError):
    """Raised when env vars are missing — surfaced as 400 to the UI."""


def _env(key: str) -> str | None:
    value = os.getenv(key)
    return value.strip() if value else None


def get_base_url() -> str | None:
    return _env("TRACKING_BASE_URL")


def get_api_token() -> str | None:
    return _env("TRACKING_API_TOKEN")


def is_ready() -> bool:
    return bool(get_base_url() and _env("TRACKING_FERNET_KEY") and get_api_token())


def _build_configuration() -> Configuration:
    base = get_base_url()
    if not base:
        raise TrackingNotConfigured(
            "TRACKING_BASE_URL is unset. Deploy the tracking sidecar (see "
            "tracking-sidecar/README.md) and set TRACKING_BASE_URL in "
            "backend/.env to its public URL."
        )
    fernet_key = _env("TRACKING_FERNET_KEY")
    if not fernet_key:
        raise TrackingNotConfigured(
            "TRACKING_FERNET_KEY is unset. Generate one with "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` and set it in "
            "backend/.env (and matching it in the sidecar's env)."
        )
    base = base.rstrip("/") + "/"
    return Configuration(
        base_open_tracking_url=base + OPEN_PATH,
        base_click_tracking_url=base + CLICK_PATH,
        encryption_bytestring_key=fernet_key.encode("utf-8"),
        append_slash=False,
    )


# ---------------------------------------------------------------------------
# Plain text -> minimal HTML, with autolinking. Email clients are
# conservative renderers; we keep the structure simple: escape entities,
# autolink URLs, and turn newlines into <br>. The surrounding <html><body>
# skeleton is what lxml needs so we can append the tracking pixel to <body>.
# ---------------------------------------------------------------------------


# Matches bare URLs (http/https). Trailing punctuation is excluded so a
# sentence like "see https://x.com." doesn't include the period.
_URL_RE = re.compile(r"https?://[^\s<>\"']+[^\s<>\"'.,;:!?)\]]")


def _plain_to_html(text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        url = match.group(0)
        return f'<a href="{_html.escape(url, quote=True)}">{_html.escape(url)}</a>'

    escaped = _html.escape(text)
    linked_lines = [_URL_RE.sub(_replace, line) for line in escaped.split("\n")]
    body_html = "<br>\n".join(linked_lines)
    return (
        "<!DOCTYPE html>"
        '<html><body style="font-family: -apple-system, BlinkMacSystemFont, '
        "'Segoe UI', sans-serif; font-size: 14px; line-height: 1.5;\">"
        f"{body_html}"
        "</body></html>"
    )


# Pixel: invisible by construction so a failed load never reveals a broken-
# image icon to the recipient. pytracking's own `_add_tracking_pixel` ships
# a bare <img src=...> with no width/height/alt, which is why a missed
# fetch shows up at full broken-image size — we sidestep it by inlining
# the pixel ourselves.
_PIXEL_ATTRIBUTES: dict[str, str] = {
    "width": "1",
    "height": "1",
    "border": "0",
    "alt": "",
    "style": "display:block; max-height:1px; max-width:1px; opacity:0;",
}


def prepare_html(body_text: str, reach_out_id: str) -> Tuple[str, str]:
    """Build (plain_text, tracking_html) bodies for a multipart email.

    The plain part is unchanged so non-HTML clients see exactly what the
    user composed. The HTML part has every link rewritten through the
    sidecar's click-tracking proxy and a 1x1 invisible open pixel
    appended.
    """
    cfg = _build_configuration()
    raw_html = _plain_to_html(body_text)
    extra_metadata = {"reach_out_id": reach_out_id}

    tree = lxml_html.fromstring(raw_html)

    for element, attribute, link, _pos in tree.iterlinks():
        if (
            element.tag == "a"
            and attribute == "href"
            and (link.startswith("http://") or link.startswith("https://"))
        ):
            new_link = pytracking.get_click_tracking_url(
                link, extra_metadata, configuration=cfg
            )
            element.attrib["href"] = new_link

    pixel_url = pytracking.get_open_tracking_url(extra_metadata, configuration=cfg)
    pixel_attrs = {"src": pixel_url, **_PIXEL_ATTRIBUTES}
    body_el = tree.body if tree.body is not None else tree
    body_el.append(lxml_html.Element("img", pixel_attrs))

    return body_text, lxml_html.tostring(tree, encoding="unicode")
