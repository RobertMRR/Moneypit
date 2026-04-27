"""Extract transaction data from receipt images using the Claude Vision API."""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass

import httpx

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

SUPPORTED_MEDIA = {
    "image/jpeg", "image/png", "image/gif", "image/webp", "application/pdf",
}

SYSTEM_PROMPT = """\
You are a receipt parser. Extract structured data from the receipt image.
Return ONLY a JSON object (no markdown fences) with these fields:
{
  "date": "YYYY-MM-DD",
  "total": 12.34,
  "currency": "PLN",
  "vendor": "Store Name",
  "items": "brief summary of purchased items"
}
Rules:
- "total" is the final amount paid (including tax). Use the largest total on the receipt.
- "total" must be a positive number.
- "currency" should be the 3-letter ISO code. Default to "PLN" if unclear.
- "date" must be ISO format. If the year is missing, assume the current year.
- "vendor" is the store/business name at the top of the receipt.
- "items" is a short comma-separated summary (max 120 chars).
- If you cannot read a field, set it to null.\
"""


@dataclass
class ReceiptData:
    date: str | None
    total: float | None
    currency: str
    vendor: str | None
    items: str | None


def scan_receipt(content: bytes, content_type: str) -> ReceiptData:
    if not ANTHROPIC_API_KEY:
        raise ValueError(
            "Set the ANTHROPIC_API_KEY environment variable to use receipt scanning"
        )

    media_type = content_type.split(";")[0].strip().lower()
    if media_type not in SUPPORTED_MEDIA:
        raise ValueError(
            f"Unsupported file type: {content_type}. "
            "Upload a JPEG, PNG, GIF, WebP, or PDF."
        )

    b64 = base64.standard_b64encode(content).decode("ascii")

    if media_type == "application/pdf":
        source = {"type": "base64", "media_type": "application/pdf", "data": b64}
        block = {"type": "document", "source": source}
    else:
        source = {"type": "base64", "media_type": media_type, "data": b64}
        block = {"type": "image", "source": source}

    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 512,
            "system": SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        block,
                        {"type": "text", "text": "Parse this receipt."},
                    ],
                }
            ],
        },
        timeout=30.0,
    )

    if resp.status_code != 200:
        detail = resp.text[:300]
        raise ValueError(f"Receipt scan failed ({resp.status_code}): {detail}")

    body = resp.json()
    raw_text = body["content"][0]["text"]
    cleaned = re.sub(r"```(?:json)?\s*", "", raw_text).strip().rstrip("`")
    data = json.loads(cleaned)

    return ReceiptData(
        date=data.get("date"),
        total=float(data["total"]) if data.get("total") else None,
        currency=(data.get("currency") or "PLN").strip().upper(),
        vendor=(data.get("vendor") or "").strip() or None,
        items=(data.get("items") or "").strip() or None,
    )
