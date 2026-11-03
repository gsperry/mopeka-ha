"""mitmproxy add-on that records how the Mopeka Co-Pilot app talks to the cloud.

One-off research tool. Run it on a computer, point an iPhone's Wi-Fi proxy at
the computer, and use Co-Pilot. It writes copilot-capture.txt containing only:

- for Amazon Cognito: the operation, auth flow, app client ID, identity pool ID,
  and challenge names (all public identifiers), never usernames, passwords,
  SRP values, or tokens;
- for Mopeka and other AWS hosts: method, path (with IDs, MAC addresses, and
  emails replaced), query parameter names, request header names, status, and
  the response's structure with personal data removed.

Other hosts are listed by name only. Header values, token values, and request
bodies other than Cognito's are never written.

    uvx --from mitmproxy mitmdump -s scripts/capture_copilot.py

See the instructions that came with this script for the iPhone setup, and
remove the mitmproxy certificate from the phone when you are done.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from mitmproxy import http

OUTPUT = Path("copilot-capture.txt")

_KEEP_STRING_KEYS = re.compile(
    r"(unit|units|abbreviation|style|model|modelNumber|type|tankType|kind|status|"
    r"state|version|orientation|category|format|product|hardware|firmware|role|"
    r"token_use|scope|ChallengeName|AuthFlow|ClientId|IdentityPoolId|__type)$",
    re.IGNORECASE,
)
_SENSITIVE_KEYS = re.compile(
    r"(lat|lng|lon|latitude|longitude|coordinates|geo|location|point|phone|zip|"
    r"postal|postcode|address|street|city|email|owner|customer|contact|serial|esn|"
    r"imei|iccid|token|secret|password|session|srp|salt|signature|username|"
    r"name$|IdentityId|Credentials|AccessKey|SecretKey|Logins)",
    re.IGNORECASE,
)
# Non-personal protocol fields that are always kept, checked before the rest.
_ALWAYS_KEEP = frozenset(
    {"AuthFlow", "ChallengeName", "ClientId", "ExpiresIn", "IdentityPoolId", "__type"}
)
_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]?){5,7}[0-9A-Fa-f]{2}$")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?([eE][-+]?\d+)?$")
_ID_SEGMENT_RE = re.compile(
    r"^(\d{3,}|[0-9a-fA-F-]{16,}|([0-9A-Fa-f]{2}[:-]?){5,7}[0-9A-Fa-f]{2}|"
    r"us-east-\d:[0-9a-f-]+|[A-Za-z0-9_-]{24,})$"
)
_INTERESTING_HOSTS = re.compile(r"(\.|^)(amazonaws\.com|mopeka\.[a-z]+)$")


def shape(value: Any, key: str = "") -> Any:
    """Return value with personal data removed but structure and numbers kept."""
    if key in _ALWAYS_KEEP and isinstance(value, str | int | None):
        return value
    if key and _SENSITIVE_KEYS.search(key) and not isinstance(value, bool | None):
        return f"<{type(value).__name__} removed>"
    if isinstance(value, dict):
        return {k: shape(v, k) for k, v in value.items()}
    if isinstance(value, list):
        items = [shape(v, key) for v in value[:3]]
        return items + ([f"... {len(value) - 3} more"] if len(value) > 3 else [])
    if isinstance(value, str):
        if _MAC_RE.match(value) or _EMAIL_RE.search(value):
            return "<removed>"
        if _NUMBER_RE.match(value) and len(value) <= 20:
            return value
        if key and _KEEP_STRING_KEYS.search(key) and len(value) <= 60:
            return value
        return f"<str len {len(value)}>"
    return value


def scrub_path(path: str) -> str:
    """Replace IDs, MAC addresses, and emails in a URL path with placeholders."""
    parts = []
    for segment in path.split("/"):
        if _EMAIL_RE.search(segment):
            parts.append("{email}")
        elif _ID_SEGMENT_RE.match(segment):
            parts.append("{id}")
        else:
            parts.append(segment)
    return "/".join(parts)


def _json(content: bytes | None) -> Any:
    try:
        return json.loads(content or b"")
    except ValueError:
        return None


class CopilotCapture:
    """Record a scrubbed summary of each interesting request, once per route."""

    def __init__(self) -> None:
        self.seen: set[tuple[str, ...]] = set()
        self.other_hosts: set[str] = set()
        OUTPUT.write_text("Mopeka Co-Pilot capture (scrubbed)\n\n", encoding="utf-8")

    def _write(self, record: dict[str, Any]) -> None:
        with OUTPUT.open("a", encoding="utf-8") as out:
            out.write(json.dumps(record, indent=1) + "\n\n")

    def websocket_start(self, flow: http.HTTPFlow) -> None:
        """Note WebSocket connections, such as AWS IoT over MQTT."""
        host = flow.request.pretty_host
        if _INTERESTING_HOSTS.search(host):
            self._write(
                {"websocket": host, "path": scrub_path(flow.request.path.split("?")[0])}
            )

    def response(self, flow: http.HTTPFlow) -> None:
        """Summarize one completed request."""
        request, response = flow.request, flow.response
        host = request.pretty_host
        if not _INTERESTING_HOSTS.search(host):
            if host not in self.other_hosts:
                self.other_hosts.add(host)
                self._write({"other_host": host})
            return

        target = request.headers.get("X-Amz-Target", "")
        path = scrub_path(request.path.split("?")[0])
        key = (request.method, host, path, target)
        if key in self.seen:
            return
        self.seen.add(key)

        record: dict[str, Any] = {
            "request": f"{request.method} https://{host}{path}",
            "status": response.status_code if response else None,
            "query_names": sorted(request.query.keys()),
            "header_names": sorted({h.lower() for h in request.headers}),
        }
        if target:
            record["x_amz_target"] = target

        body = _json(request.content)
        if host.startswith("cognito-") and isinstance(body, dict):
            record["request_body"] = {
                "AuthFlow": body.get("AuthFlow"),
                "ClientId": body.get("ClientId"),
                "ChallengeName": body.get("ChallengeName"),
                "IdentityPoolId": body.get("IdentityPoolId"),
                "AuthParameters_names": sorted(body.get("AuthParameters", {}) or {}),
                "ChallengeResponses_names": sorted(
                    body.get("ChallengeResponses", {}) or {}
                ),
                "Logins_providers": sorted(body.get("Logins", {}) or {}),
            }
        elif body is not None:
            record["request_body_names"] = (
                sorted(body) if isinstance(body, dict) else type(body).__name__
            )

        if response is not None:
            payload = _json(response.content)
            record["response"] = (
                shape(payload)
                if payload is not None
                else f"<{response.headers.get('content-type', 'unknown')}, "
                f"{len(response.content or b'')} bytes>"
            )
        self._write(record)


addons = [CopilotCapture()]
