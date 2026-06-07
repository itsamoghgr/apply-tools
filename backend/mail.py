"""Tiny SMTP helper for sending mail through Gmail with an app password.

Gmail app passwords require 2-Step Verification on the Google account; the
user generates one at https://myaccount.google.com/apppasswords. We connect
over implicit TLS on port 465 (smtplib.SMTP_SSL) which is the simplest path
that doesn't require STARTTLS negotiation.
"""

from __future__ import annotations

import email
import html
import imaplib
import quopri
import re
import smtplib
import threading
import time
from contextlib import contextmanager
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formataddr, getaddresses, parsedate_to_datetime


class GmailAuthError(RuntimeError):
    """Raised when Gmail rejects the credentials. Surface to the user as 401-ish."""


class GmailSendError(RuntimeError):
    """Raised for any other SMTP failure (network, recipient refused, etc)."""


class GmailReadError(RuntimeError):
    """Raised when IMAP read fails for any reason other than auth."""


# ---------------------------------------------------------------------------
# Persistent IMAP connection pool.
#
# Cold IMAP cycles (TLS handshake + LOGIN + SELECT) cost ~1–2s with Gmail.
# Doing that on every per-message body fetch is what made the Mail page feel
# sluggish. The pool keeps one warm IMAP4_SSL per Gmail address; subsequent
# fetches skip the handshake and just issue UID FETCH. Per-address lock
# serialises commands on a single connection (imaplib isn't safe for
# concurrent use of the same socket).
# ---------------------------------------------------------------------------

_IMAP_HOST = "imap.gmail.com"
_IMAP_PORT = 993
# After this much inactivity we proactively reconnect rather than NOOP first.
# Gmail's IMAP idle timeout is ~30 min, so we stay well below it.
_POOL_STALE_SECONDS = 20 * 60


class _PooledConn:
    __slots__ = ("conn", "last_used", "lock")

    def __init__(self, conn: imaplib.IMAP4_SSL) -> None:
        self.conn = conn
        self.last_used = time.monotonic()
        self.lock = threading.Lock()


# Address-keyed because if multi-account ever lands the keys keep us correct.
_pool: dict[str, _PooledConn] = {}
# Guards _pool itself (creating a new entry); per-connection ops use the
# entry's own lock.
_pool_dict_lock = threading.Lock()


def _open_imap(address: str, app_password: str) -> imaplib.IMAP4_SSL:
    try:
        conn = imaplib.IMAP4_SSL(_IMAP_HOST, _IMAP_PORT, timeout=30)
    except OSError as exc:
        raise GmailReadError(f"Could not reach Gmail IMAP: {exc}") from exc
    try:
        conn.login(address, app_password)
    except imaplib.IMAP4.error as exc:
        raise GmailAuthError(
            "Gmail rejected the app password for IMAP. Make sure IMAP is "
            "enabled in Gmail settings and the app password is current."
        ) from exc
    # readonly=False so /mail/{uid} can flip the \Seen flag (mark-as-read)
    # without re-selecting. The inbox listing also runs through this path
    # but only issues read-only commands.
    status, _ = conn.select("INBOX", readonly=False)
    if status != "OK":
        try:
            conn.logout()
        except Exception:
            pass
        raise GmailReadError("Could not open INBOX.")
    return conn


def _is_alive(conn: imaplib.IMAP4_SSL) -> bool:
    """Cheap liveness probe — Gmail will idle-close after ~30 min."""
    try:
        status, _ = conn.noop()
        return status == "OK"
    except (imaplib.IMAP4.error, OSError):
        return False


@contextmanager
def imap_session(address: str, app_password: str):
    """Yield a logged-in, INBOX-selected IMAP connection from the pool.

    Reuses an existing pooled connection when possible, validating with a
    NOOP first. Reconnects transparently on failure. Holds the per-address
    lock for the duration of the `with` block so concurrent /mail and
    /mail/{uid} requests don't interleave commands on the same socket.
    """
    if not address or not app_password:
        raise GmailReadError("Gmail credentials missing.")

    with _pool_dict_lock:
        entry = _pool.get(address)

    if entry is not None:
        entry.lock.acquire()
        # Stale or dead → drop the connection and rebuild.
        if (
            time.monotonic() - entry.last_used > _POOL_STALE_SECONDS
            or not _is_alive(entry.conn)
        ):
            try:
                entry.conn.logout()
            except Exception:
                pass
            try:
                entry.conn = _open_imap(address, app_password)
            except Exception:
                entry.lock.release()
                with _pool_dict_lock:
                    _pool.pop(address, None)
                raise
        entry.last_used = time.monotonic()
        try:
            yield entry.conn
            entry.last_used = time.monotonic()
        except (imaplib.IMAP4.abort, OSError, imaplib.IMAP4.error):
            # Connection went bad mid-command — evict so the next caller
            # rebuilds.
            try:
                entry.conn.logout()
            except Exception:
                pass
            with _pool_dict_lock:
                _pool.pop(address, None)
            raise
        finally:
            entry.lock.release()
        return

    # First-time use for this address: build, install, then yield.
    conn = _open_imap(address, app_password)
    new_entry = _PooledConn(conn)
    new_entry.lock.acquire()
    with _pool_dict_lock:
        # Race: another thread might have built one too. Prefer keeping
        # the one already in the pool and discard ours.
        existing = _pool.get(address)
        if existing is not None:
            new_entry.lock.release()
            try:
                conn.logout()
            except Exception:
                pass
            entry = existing
        else:
            _pool[address] = new_entry
            entry = new_entry

    if entry is not new_entry:
        # Recurse once: we couldn't install ours, so use the existing entry
        # via the normal path. (Acquire its lock; then yield.)
        entry.lock.acquire()
        try:
            entry.last_used = time.monotonic()
            yield entry.conn
            entry.last_used = time.monotonic()
        finally:
            entry.lock.release()
        return

    try:
        new_entry.last_used = time.monotonic()
        yield new_entry.conn
        new_entry.last_used = time.monotonic()
    except (imaplib.IMAP4.abort, OSError, imaplib.IMAP4.error):
        try:
            new_entry.conn.logout()
        except Exception:
            pass
        with _pool_dict_lock:
            _pool.pop(address, None)
        raise
    finally:
        new_entry.lock.release()


def send_gmail(
    from_addr: str,
    app_password: str,
    to_addr: str,
    subject: str,
    body: str,
    from_name: str | None = None,
    html_body: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> None:
    """Send a plain-text email, with an optional HTML alternative.

    When `html_body` is provided we send a multipart/alternative message so
    HTML-capable clients render the formatted version (and load any embedded
    tracking pixel) while clients in plain-text mode still see `body`.

    `in_reply_to` and `references` set the matching RFC 5322 headers so the
    recipient's mail client groups the reply into the same thread as the
    original message. Pass the original Message-ID (with angle brackets).
    """
    msg = EmailMessage()
    msg["From"] = formataddr((from_name or "", from_addr))
    msg["To"] = to_addr
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(body)
    if html_body:
        # `add_alternative` automatically promotes the message to
        # multipart/alternative with the plain-text part listed first, which
        # is what RFC 2046 recommends so non-HTML clients fall through.
        msg.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(from_addr, app_password)
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise GmailAuthError(
            "Gmail rejected the app password. Double-check the address and "
            "regenerate an app password at "
            "https://myaccount.google.com/apppasswords."
        ) from exc
    except smtplib.SMTPException as exc:
        raise GmailSendError(f"SMTP send failed: {exc}") from exc


def _decode_subject(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw


def _split_address(raw: str | None) -> tuple[str, str]:
    """Return (display_name, email) from an RFC 5322 address header."""
    if not raw:
        return "", ""
    decoded = _decode_subject(raw)
    pairs = getaddresses([decoded])
    if not pairs:
        return "", decoded
    name, addr = pairs[0]
    return name or "", addr or ""


_HTML_TAG_RE = re.compile(r"<[^>]+>")
# Matches an HTML tag that was cut off by our partial fetch (opens with `<`
# but never closes within the window). We strip from `<` to end-of-string.
_TRAILING_OPEN_TAG_RE = re.compile(r"<[^>]*$")
# <style>/<script> blocks contain CSS/JS we don't want bleeding into snippets.
_STYLE_SCRIPT_RE = re.compile(
    r"<(style|script)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
# Tags that should produce a paragraph break when stripped, so the text body
# doesn't collapse into a single line. Order matters only for readability.
_PARAGRAPH_TAGS_RE = re.compile(
    r"</(?:p|div|li|tr|h[1-6]|blockquote)>|<br\s*/?>|<hr\s*/?>",
    re.IGNORECASE,
)
# Zero-width / invisible unicode chars marketers stuff into preheaders to
# pad the inbox preview line. Strip them so they don't visually dominate
# the body view. Covers ZWSP/ZWNJ/ZWJ/LRM/RLM (U+200B-200F), word joiner
# and friends (U+2060-206F), BOM (U+FEFF), soft hyphen (U+00AD), and the
# Mongolian vowel separator (U+180E).
_INVISIBLE_CHARS_RE = re.compile(
    "[\u200B-\u200F\u2060-\u206F\uFEFF\u00AD\u180E]"
)
_MIME_HEADER_RE = re.compile(
    r"^(content-type|content-transfer-encoding|content-disposition|mime-version|x-[a-z0-9-]+):",
    re.IGNORECASE,
)


def _clean_snippet_text(text: str, limit: int = 240) -> str:
    """Strip MIME boundaries, headers, and HTML to leave a readable snippet.

    We can't always parse the snippet bytes as a structured message (we may
    have only a 2 KB window into a larger multipart body), so fall back to
    line-level heuristics: drop boundary markers, drop MIME-ish headers,
    decode entities, and strip tags.
    """
    if not text:
        return ""
    cleaned_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # MIME boundary delimiters look like "--<token>" or "--<token>--".
        if line.startswith("--") and len(line) > 2 and not line.startswith("---"):
            continue
        if _MIME_HEADER_RE.match(line):
            continue
        cleaned_lines.append(line)
    joined = " ".join(cleaned_lines)
    # Drop CSS/JS blocks before generic tag stripping so their inner text
    # doesn't leak through.
    no_blocks = _STYLE_SCRIPT_RE.sub(" ", joined)
    no_tags = _HTML_TAG_RE.sub(" ", no_blocks)
    # Drop any half-open tag the partial fetch left behind at the end.
    no_tags = _TRAILING_OPEN_TAG_RE.sub("", no_tags)
    # Decode named + numeric HTML entities (&amp;, &#x27;, &#8202;, etc.).
    decoded = html.unescape(no_tags)
    snippet = " ".join(decoded.split())
    return snippet[:limit] + ("…" if len(snippet) > limit else "")


def _html_to_text(raw_html: str) -> str:
    """Convert HTML to plaintext while preserving paragraph structure.

    Different from `_clean_snippet_text` which collapses everything onto a
    single line for a snippet preview — this one is for the full message
    body so the reader sees real paragraphs.
    """
    if not raw_html:
        return ""
    no_blocks = _STYLE_SCRIPT_RE.sub(" ", raw_html)
    # Insert newlines where block-level tags close so paragraphs survive.
    with_breaks = _PARAGRAPH_TAGS_RE.sub("\n", no_blocks)
    no_tags = _HTML_TAG_RE.sub("", with_breaks)
    decoded = html.unescape(no_tags)
    no_invisibles = _INVISIBLE_CHARS_RE.sub("", decoded)
    # Collapse runs of horizontal whitespace inside lines, but preserve
    # newlines. Then collapse 3+ blank lines to 2.
    cleaned_lines = [re.sub(r"[ \t]+", " ", line).strip() for line in no_invisibles.splitlines()]
    joined = "\n".join(cleaned_lines)
    return re.sub(r"\n{3,}", "\n\n", joined).strip()


def _extract_snippet(msg: email.message.Message, limit: int = 240) -> str:
    """Pull a short text snippet out of a (possibly multipart) message."""
    candidate: str = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                try:
                    candidate = payload.decode(charset, errors="replace")
                except LookupError:
                    candidate = payload.decode("utf-8", errors="replace")
                if candidate.strip():
                    break
    else:
        payload = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        try:
            candidate = payload.decode(charset, errors="replace")
        except LookupError:
            candidate = payload.decode("utf-8", errors="replace")

    return _clean_snippet_text(candidate, limit=limit)


def fetch_inbox(
    address: str,
    app_password: str,
    limit: int = 50,
) -> list[dict]:
    """Read the latest `limit` messages from the user's Gmail INBOX over IMAP.

    Uses BODY.PEEK on the listing so we don't accidentally mark every
    message read just by enumerating the inbox. Returns newest-first.
    """
    if limit <= 0:
        return []

    try:
        with imap_session(address, app_password) as conn:
            # UID SEARCH gives us stable identifiers (UIDs are per-mailbox
            # and don't shift when other messages are deleted, unlike
            # sequence numbers). The frontend uses these UIDs to request
            # full bodies later via /mail/{uid}.
            status, data = conn.uid("SEARCH", None, "ALL")
            if status != "OK" or not data or not data[0]:
                return []

            uids = data[0].split()
            tail = uids[-limit:]
            tail.reverse()

            messages: list[dict] = []
            for uid in tail:
                uid_str = uid.decode("ascii", errors="replace")
                # Two fetches per message keeps the IMAP command syntax
                # simple and avoids server-specific quirks around
                # combining a partial BODY.PEEK[TEXT] with other items in
                # one parenthesised list.
                status, hdr_data = conn.uid(
                    "FETCH",
                    uid,
                    "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID)])",
                )
                if status != "OK" or not hdr_data:
                    continue

                header_bytes = b""
                flags = ""
                for part in hdr_data:
                    if isinstance(part, tuple) and len(part) >= 2:
                        descriptor = part[0].decode("utf-8", errors="replace") if isinstance(part[0], (bytes, bytearray)) else str(part[0])
                        payload = part[1] if isinstance(part[1], (bytes, bytearray)) else b""
                        if "HEADER" in descriptor:
                            header_bytes = payload
                        if "FLAGS" in descriptor:
                            flags = descriptor
                    elif isinstance(part, (bytes, bytearray)):
                        descriptor = part.decode("utf-8", errors="replace")
                        if "FLAGS" in descriptor:
                            flags = descriptor

                if not header_bytes:
                    continue

                text_bytes = b""
                # 8 KB window: enough to skip past a typical HTML <head> +
                # <style> block on marketing emails and reach actual
                # visible body text.
                status, body_data = conn.uid("FETCH", uid, "(BODY.PEEK[TEXT]<0.8192>)")
                if status == "OK" and body_data:
                    for part in body_data:
                        if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
                            text_bytes = part[1]
                            break

                header_msg = email.message_from_bytes(header_bytes)
                from_name, from_email = _split_address(header_msg.get("From"))
                _, to_email = _split_address(header_msg.get("To"))
                subject = _decode_subject(header_msg.get("Subject"))
                message_id = (header_msg.get("Message-ID") or "").strip()

                date_iso: str | None = None
                raw_date = header_msg.get("Date")
                if raw_date:
                    try:
                        date_iso = parsedate_to_datetime(raw_date).isoformat()
                    except (TypeError, ValueError):
                        date_iso = None

                snippet = ""
                if text_bytes:
                    # Many bodies arrive quoted-printable encoded (=3D for
                    # "=", trailing "=" for soft line breaks). Decode
                    # best-effort — if decoding fails (e.g. base64 part),
                    # fall back to the raw bytes and let the cleaner do
                    # what it can.
                    try:
                        decoded_bytes = quopri.decodestring(text_bytes)
                        raw_text = decoded_bytes.decode("utf-8", errors="replace")
                    except Exception:
                        raw_text = text_bytes.decode("utf-8", errors="replace")
                    snippet = _clean_snippet_text(raw_text)

                unread = "\\Seen" not in flags

                messages.append(
                    {
                        "id": uid_str,
                        "messageId": message_id,
                        "fromName": from_name,
                        "fromEmail": from_email,
                        "to": to_email,
                        "subject": subject,
                        "date": date_iso,
                        "snippet": snippet,
                        "unread": unread,
                    }
                )

            return messages
    except (GmailAuthError, GmailReadError):
        raise
    except imaplib.IMAP4.error as exc:
        raise GmailReadError(f"IMAP error: {exc}") from exc


def _best_body_parts(msg: email.message.Message) -> tuple[str, str]:
    """Return (text_plain, text_html) — either may be empty."""
    text_plain = ""
    text_html = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart() or part.get_filename():
                continue
            ctype = part.get_content_type()
            if ctype not in ("text/plain", "text/html"):
                continue
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            try:
                decoded = payload.decode(charset, errors="replace")
            except LookupError:
                decoded = payload.decode("utf-8", errors="replace")
            if ctype == "text/plain" and not text_plain:
                text_plain = decoded
            elif ctype == "text/html" and not text_html:
                text_html = decoded
    else:
        payload = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        try:
            decoded = payload.decode(charset, errors="replace")
        except LookupError:
            decoded = payload.decode("utf-8", errors="replace")
        if msg.get_content_type() == "text/html":
            text_html = decoded
        else:
            text_plain = decoded
    return text_plain, text_html


def fetch_message(address: str, app_password: str, uid: str) -> dict | None:
    """Fetch a single message's headers + full body by UID.

    Returns None if the UID isn't found in INBOX. Uses BODY[] (not
    BODY.PEEK[]) so opening a message via this endpoint also marks it as
    read in Gmail — same behavior the user gets in Gmail's web UI.
    """
    if not uid or not uid.isdigit():
        raise GmailReadError(f"Invalid UID: {uid!r}")

    try:
        with imap_session(address, app_password) as conn:
            # BODY[] (no PEEK) tells the IMAP server to flip the \\Seen
            # flag for this UID transactionally as part of the fetch. The
            # mailbox is selected read-write by the pool so this is
            # accepted.
            status, data = conn.uid("FETCH", uid.encode("ascii"), "(BODY[])")
            if status != "OK" or not data or data[0] is None:
                return None

            raw_bytes = b""
            for part in data:
                if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
                    raw_bytes = part[1]
                    break
            if not raw_bytes:
                return None

            msg = email.message_from_bytes(raw_bytes)
            from_name, from_email = _split_address(msg.get("From"))
            _, to_email = _split_address(msg.get("To"))
            subject = _decode_subject(msg.get("Subject"))
            message_id = (msg.get("Message-ID") or "").strip()
            date_iso: str | None = None
            raw_date = msg.get("Date")
            if raw_date:
                try:
                    date_iso = parsedate_to_datetime(raw_date).isoformat()
                except (TypeError, ValueError):
                    date_iso = None

            text_plain, text_html = _best_body_parts(msg)
            # Prefer text/plain; fall back to a paragraph-preserving HTML
            # strip. In both cases unescape entities so &#x27;, &#8202;,
            # &amp; etc. render as real characters in the UI, and drop
            # invisible preheader padding chars used by marketing senders.
            if text_plain.strip():
                text_view = _INVISIBLE_CHARS_RE.sub("", html.unescape(text_plain))
            elif text_html.strip():
                text_view = _html_to_text(text_html)
            else:
                text_view = ""

            return {
                "id": uid,
                "messageId": message_id,
                "fromName": from_name,
                "fromEmail": from_email,
                "to": to_email,
                "subject": subject,
                "date": date_iso,
                "text": text_view,
                "html": text_html,
            }
    except (GmailAuthError, GmailReadError):
        raise
    except imaplib.IMAP4.error as exc:
        raise GmailReadError(f"IMAP error: {exc}") from exc
