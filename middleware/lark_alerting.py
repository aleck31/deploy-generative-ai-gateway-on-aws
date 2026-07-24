"""Lark (Feishu) alerting bridge.

LiteLLM only ships Slack-family alerting (Slack / Discord / MS Teams via
Slack-compatible webhooks). Lark is NOT Slack-compatible, so this module exposes
an internal endpoint that LiteLLM posts Slack-formatted alerts to (configured via
``alerting: ["slack"]`` + ``SLACK_WEBHOOK_URL=http://localhost:3000/webhook/slack-to-lark``),
translates them into a Lark interactive card, and forwards them to ``LARK_WEBHOOK_URL``.

The endpoint is only reachable in-cluster (localhost between the LiteLLM and
middleware containers of the same task/pod). It is NOT exposed via the ALB /
ingress (not under ``/plus``, and ``/*`` routes to LiteLLM).

Set ``LARK_WEBHOOK_URL`` to enable; leave it empty to disable the feature.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import time
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

LARK_WEBHOOK_URL = os.environ.get("LARK_WEBHOOK_URL", "").strip()
LARK_WEBHOOK_SECRET = os.environ.get("LARK_WEBHOOK_SECRET", "").strip()

router = APIRouter()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Map alert level to Lark card header template color
_LEVEL_COLORS = {
    "high": "red",
    "medium": "orange",
    "low": "blue",
}


def _sign(timestamp: str, secret: str) -> str:
    """Lark custom-bot signature: base64(HMAC-SHA256(key=f'{ts}\\n{secret}', msg=''))."""
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _extract_text(payload: Dict[str, Any]) -> str:
    """Extract a human-readable string from a LiteLLM Slack-format alert payload."""
    if not isinstance(payload, dict):
        return str(payload)
    if payload.get("text"):
        return str(payload["text"])
    parts = []
    for block in payload.get("blocks", []) or []:
        txt = block.get("text") or {}
        if isinstance(txt, dict) and txt.get("text"):
            parts.append(txt["text"])
    if parts:
        return "\n".join(parts)
    return json.dumps(payload, ensure_ascii=False)


def _parse_alert_metadata(text: str) -> Dict[str, str]:
    """Try to extract structured fields (Alert type, Level, Timestamp, Message)
    from the text that LiteLLM formats for Slack alerts."""
    meta = {}
    lines = text.split("\n")
    message_lines: List[str] = []
    in_message = False

    for line in lines:
        if in_message:
            message_lines.append(line)
            continue
        # LiteLLM formats: "Alert type: `budget_alerts`"
        m = re.match(r"^Alert type:\s*`?([^`]+)`?", line)
        if m:
            meta["alert_type"] = m.group(1).strip()
            continue
        m = re.match(r"^Level:\s*`?([^`]+)`?", line)
        if m:
            meta["level"] = m.group(1).strip()
            continue
        m = re.match(r"^Timestamp:\s*`?([^`]+)`?", line)
        if m:
            meta["timestamp"] = m.group(1).strip()
            continue
        if line.startswith("Message:"):
            in_message = True
            # The rest of this line is also part of the message
            msg_start = line[len("Message:"):].strip()
            if msg_start:
                message_lines.append(msg_start)
            continue
        # Unrecognized line before Message: — include in message
        message_lines.append(line)

    meta["message"] = "\n".join(message_lines).strip() or text
    return meta


def _build_card(text: str) -> Dict[str, Any]:
    """Build a rich Lark interactive card from alert text."""
    meta = _parse_alert_metadata(text)
    alert_type = meta.get("alert_type", "alert")
    level = meta.get("level", "low").lower()
    timestamp = meta.get("timestamp", "")
    message = meta.get("message", text)

    color = _LEVEL_COLORS.get(level, "blue")

    # Header with icon based on level
    level_icons = {"high": "🔴", "medium": "🟠", "low": "🔵"}
    icon = level_icons.get(level, "ℹ️")
    header_text = f"{icon} {alert_type}"

    elements: List[Dict[str, Any]] = []

    # Metadata fields row (compact)
    fields = []
    if level:
        fields.append({"tag": "lark_md", "content": f"**Level:** {level.capitalize()}"})
    if timestamp:
        fields.append({"tag": "lark_md", "content": f"**Time:** {timestamp}"})
    if fields:
        elements.append({"tag": "column_set", "flex_mode": "bisect", "columns": [
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [{"tag": "div", "text": f}]}
            for f in fields
        ]})

    # Divider
    elements.append({"tag": "hr"})

    # Message body
    # Truncate very long messages to avoid Lark API size limits (30KB card limit)
    if len(message) > 2500:
        message = message[:2500] + "\n\n... (truncated)"
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": message}})

    card: Dict[str, Any] = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": header_text},
            "template": color,
        },
        "elements": elements,
    }
    return card


def _build_message(text: str) -> Dict[str, Any]:
    """Build the complete Lark webhook body with card + optional signature."""
    body: Dict[str, Any] = {
        "msg_type": "interactive",
        "card": _build_card(text),
    }
    if LARK_WEBHOOK_SECRET:
        timestamp = str(int(time.time()))
        body["timestamp"] = timestamp
        body["sign"] = _sign(timestamp, LARK_WEBHOOK_SECRET)
    return body


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/webhook/slack-to-lark")
async def slack_to_lark(request: Request):
    """Receive a LiteLLM Slack-format alert and forward it to Lark."""
    if not LARK_WEBHOOK_URL:
        return Response(status_code=204)
    try:
        payload = await request.json()
    except Exception:
        payload = {"text": (await request.body()).decode("utf-8", "replace")}

    lark_body = _build_message(_extract_text(payload))

    async with httpx.AsyncClient() as client:
        resp = await client.post(LARK_WEBHOOK_URL, json=lark_body, timeout=10)
    if resp.status_code >= 300:
        print(f"[lark] forward failed: {resp.status_code} {resp.text[:300]}")
        return JSONResponse(
            status_code=502,
            content={"error": "lark forward failed", "status": resp.status_code},
        )
    return {"status": "ok"}
