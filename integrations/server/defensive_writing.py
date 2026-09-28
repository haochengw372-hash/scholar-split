"""DeepSeek review of defensive prose with verified Zotero highlight positions."""

from __future__ import annotations

import base64
import json
import math
import re
import threading
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

PROMPT_VERSION = "academic-review-github-2026-09-v1"
MAX_PDF_BYTES = 100 * 1024 * 1024
MAX_CHUNK_CHARS = 11000
MAX_FLAGS = 30

REVIEW_PROMPT = """Review the following academic article text for unnecessarily defensive writing.
This is an editorial review, not an authorship or AI detection test. Follow the
review-first principles of academic manuscript review: point to a specific
sentence, explain why its wording buries or weakens the claim, and suggest a
direction for revision without changing the paper's actual evidence.

Flag only clear examples of repeated generic caveats, stacked hedges, preemptive
apologies, repeated statements about what the paper does not claim, or a finding
buried behind unnecessary qualifications. Do not flag a single may/might,
material study limitations, sample/design/measurement boundaries, null results,
uncertainty intervals, or justified causal restraint. Skip references and quoted
passages. If uncertain, return no flag.

Return one JSON object with a `flags` array, at most 4 entries. Each entry must
contain pageIndex (the integer given below), quote (20-500 characters copied
verbatim from that page), reason (concise Chinese), and suggestion (concise
Chinese direction; do not invent claims or results). No other text.

PAGES:
{pages}
"""


def decode_pdf(value: str) -> bytes:
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("PDF content must be base64") from exc
    if not 4000 <= len(data) <= MAX_PDF_BYTES or b"%PDF-" not in data[:1024] or b"%%EOF" not in data[-65536:]:
        raise ValueError("A complete PDF under 100 MB is required")
    return data


def _chunks(pages: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    chunks: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    current_size = 0
    for page_index, text in pages:
        for start in range(0, len(text), MAX_CHUNK_CHARS):
            piece = text[start : start + MAX_CHUNK_CHARS]
            if current and current_size + len(piece) > MAX_CHUNK_CHARS:
                chunks.append(current)
                current = []
                current_size = 0
            current.append((page_index, piece))
            current_size += len(piece)
    if current:
        chunks.append(current)
    return chunks


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _validate_flags(payload: Any, allowed_pages: set[int]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("flags"), list):
        raise ValueError("DeepSeek returned an invalid writing-review JSON response")
    flags: list[dict[str, Any]] = []
    for item in payload["flags"][:4]:
        if not isinstance(item, dict) or type(item.get("pageIndex")) is not int:
            continue
        quote = item.get("quote")
        reason = item.get("reason")
        suggestion = item.get("suggestion")
        if (
            item["pageIndex"] not in allowed_pages
            or not isinstance(quote, str)
            or not 20 <= len(quote.strip()) <= 500
            or not isinstance(reason, str)
            or not reason.strip()
            or not isinstance(suggestion, str)
            or not suggestion.strip()
        ):
            continue
        flags.append({
            "pageIndex": item["pageIndex"],
            "quote": quote.strip(),
            "reason": reason.strip()[:500],
            "suggestion": suggestion.strip()[:500],
        })
    return flags


def _deepseek_review(prompt: str, config_path: Path) -> dict[str, Any]:
    with config_path.open("rb") as stream:
        profile = tomllib.load(stream).get("deepseek_detail") or {}
    api_key = str(profile.get("deepseek_api_key") or "").strip()
    model = str(profile.get("deepseek_model") or "deepseek-chat").strip()
    if not api_key:
        raise ValueError("DeepSeek is not configured for writing review")
    request_body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You review scholarly prose and return strict JSON."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 3000,
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
    }
    request = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = json.loads(response.read(2_000_001).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ValueError(f"DeepSeek writing review failed with HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ValueError("DeepSeek writing review network error") from exc
    try:
        message = body["choices"][0]
        content = message["message"]["content"]
        if message.get("finish_reason") == "length":
            raise ValueError("DeepSeek writing review was truncated")
        return json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("DeepSeek returned an invalid writing-review JSON response") from exc


def _locate(page: Any, item: dict[str, Any]) -> dict[str, Any] | None:
    if page.rotation:
        return None
    quote = item["quote"]
    page_text = page.get_text(sort=True)
    if _normalized(page_text).count(_normalized(quote)) != 1:
        return None
    matches = page.search_for(quote)
    if not 1 <= len(matches) <= 12:
        return None
    height = page.rect.height
    rects = [
        [round(rect.x0, 3), round(height - rect.y1, 3), round(rect.x1, 3), round(height - rect.y0, 3)]
        for rect in matches
        if rect.x0 < rect.x1 and rect.y0 < rect.y1
    ]
    if not rects:
        return None
    text_offset = _normalized(page_text).find(_normalized(quote))
    top = max(0, math.floor(height - rects[0][3]))
    return {
        **item,
        "position": {"pageIndex": item["pageIndex"], "rects": rects},
        "pageLabel": str(item["pageIndex"] + 1),
        "sortIndex": f"{item['pageIndex']:05d}|{text_offset:06d}|{top:05d}",
    }


def review_pdf(
    pdf_data: bytes,
    config_path: Path,
    on_progress: Callable[[float], None] | None = None,
    review: Callable[[str, Path], dict[str, Any]] = _deepseek_review,
) -> dict[str, Any]:
    import fitz

    with fitz.open(stream=pdf_data, filetype="pdf") as document:
        pages = [(index, page.get_text(sort=True)) for index, page in enumerate(document)]
        chunks = _chunks([(index, text) for index, text in pages if text.strip()])
        if not chunks:
            raise ValueError("PDF has no searchable text; OCR is required")
        located: list[dict[str, Any]] = []
        seen: set[tuple[int, str]] = set()
        unlocated = 0
        for chunk_index, chunk in enumerate(chunks, 1):
            prompt_pages = "\n\n".join(f"[pageIndex={index}]\n{text}" for index, text in chunk)
            payload = review(REVIEW_PROMPT.format(pages=prompt_pages), config_path)
            source_by_page: dict[int, str] = {}
            for page_index, text in chunk:
                source_by_page[page_index] = source_by_page.get(page_index, "") + text
            for item in _validate_flags(payload, {index for index, _ in chunk}):
                if _normalized(item["quote"]) not in _normalized(source_by_page[item["pageIndex"]]):
                    unlocated += 1
                    continue
                key = (item["pageIndex"], _normalized(item["quote"]))
                if key in seen:
                    continue
                seen.add(key)
                position = _locate(document[item["pageIndex"]], item)
                if position is None:
                    unlocated += 1
                elif len(located) < MAX_FLAGS:
                    located.append(position)
            if on_progress:
                on_progress(chunk_index / len(chunks) * 100)
        return {
            "flags": located,
            "pageCount": len(document),
            "unlocatedCount": unlocated,
            "promptVersion": PROMPT_VERSION,
        }


class DefensiveWritingService:
    def __init__(self, store: Any, root: Path, reviewer: Callable[..., dict[str, Any]] = review_pdf):
        self.store = store
        self.config_path = root / "config" / "config.toml"
        self.reviewer = reviewer

    def start(self, encoded_pdf: str, filename: str) -> dict[str, Any]:
        pdf_data = decode_pdf(encoded_pdf)
        job = self.store.create_job({
            "kind": "defensive_writing",
            "status": "queued",
            "payload": {"filename": filename[:250], "promptVersion": PROMPT_VERSION},
        })
        thread = threading.Thread(target=self._run, args=(job["id"], pdf_data), daemon=True)
        thread.start()
        return {"id": job["id"], "status": job["status"]}

    def _run(self, job_id: str, pdf_data: bytes) -> None:
        self.store.update_job(job_id, {"status": "running", "progress": 0})
        try:
            result = self.reviewer(
                pdf_data,
                self.config_path,
                on_progress=lambda progress: self.store.update_job(job_id, {"progress": progress}),
            )
            self.store.update_job(job_id, {"status": "completed", "progress": 100, "result": result})
        except Exception as exc:
            message = str(exc)
            if "sk-" in message or "api_key" in message.lower():
                message = "Writing review failed"
            self.store.update_job(job_id, {"status": "failed", "error": message})
