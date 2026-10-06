from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import toml
from pypdf import PdfReader


GUIDE_PROMPT_VERSION = "2026-09-11-v2-advanced-statistics"
MAX_REQUEST_CHARS = 180_000
# The advanced-statistics schema can legitimately exceed 8k output tokens for
# long methods papers. A hard 8k cap caused syntactically truncated JSON and
# left Zotero without a guide even though the model had completed most of it.
MAX_GUIDE_TOKENS = 16_000
MAX_PDF_BYTES = 100 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
GUIDE_ID_RE = re.compile(r"^[0-9a-f]{24}$")


def decode_pdf_content(value: Any) -> bytes:
    if not isinstance(value, str):
        raise ValueError("PDF content must be base64 text")
    prefix = "data:application/pdf;base64,"
    if value.startswith(prefix):
        value = value[len(prefix) :]
    try:
        data = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise ValueError("Invalid base64 PDF content") from exc
    if b"%PDF-" not in data[:1024] or b"%%EOF" not in data[-65_536:]:
        raise ValueError("Uploaded content is not a complete PDF")
    if len(data) > MAX_PDF_BYTES:
        raise ValueError("PDF exceeds the 100 MB reading-guide limit")
    return data


def guide_identifier(source_pdf: bytes, model: str) -> str:
    digest = hashlib.sha256()
    digest.update(GUIDE_PROMPT_VERSION.encode("utf-8"))
    digest.update(model.encode("utf-8"))
    digest.update(source_pdf)
    return digest.hexdigest()[:24]


def validate_guide_id(guide_id: str) -> str:
    value = str(guide_id or "").strip().lower()
    if not GUIDE_ID_RE.fullmatch(value):
        raise ValueError("Invalid guide id")
    return value


def extract_pdf_text(pdf_path: str | Path) -> tuple[str, dict[str, Any]]:
    reader = PdfReader(str(pdf_path))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    if not pages:
        raise ValueError("The PDF has no pages")
    extracted_chars = sum(len(page) for page in pages)
    meaningful_pages = sum(len(page) >= 80 for page in pages)
    source_exists = Path(pdf_path).is_file()
    if not any(pages) or (
        source_exists
        and (
            extracted_chars < max(500, len(pages) * 80)
            or meaningful_pages < max(1, len(pages) // 3)
        )
    ):
        helper = Path(__file__).resolve().parents[1] / "tools" / "ocr_pdf_text"
        if not helper.is_file():
            raise ValueError("No readable text was extracted from the PDF")
        try:
            completed = subprocess.run(
                [str(helper), str(pdf_path)],
                check=True,
                capture_output=True,
                text=True,
                timeout=max(300, len(pages) * 30),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("No readable text was extracted from the PDF and OCR failed") from exc
        joined = completed.stdout.strip()
        if not joined or "[PAGE 1]" not in joined:
            raise ValueError("No readable text was extracted from the PDF and OCR returned no text")
        return joined, {
            "pageCount": len(pages),
            "sourceChars": len(joined),
            "truncated": False,
            "ocr": "macOS Vision",
        }

    page_blocks = [f"[PAGE {index}]\n{text}" for index, text in enumerate(pages, 1)]
    joined = "\n\n".join(page_blocks)
    return joined, {
        "pageCount": len(pages),
        "sourceChars": len(joined),
        "truncated": False,
    }


def chunk_extracted_text(text: str, max_chars: int = MAX_REQUEST_CHARS) -> list[str]:
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    if len(text) <= max_chars:
        return [text]

    # Page markers remain attached to their page. An unusually large single
    # page is split without dropping characters, and every continuation keeps
    # the page number for evidence anchors.
    page_blocks = re.split(r"(?=\[PAGE \d+\]\n)", text)
    page_blocks = [block for block in page_blocks if block]
    chunks: list[str] = []
    current = ""
    for block in page_blocks:
        pieces: list[str] = []
        if len(block) <= max_chars:
            pieces = [block]
        else:
            pieces = [block[i : i + max_chars] for i in range(0, len(block), max_chars)]

        for piece in pieces:
            if current and len(current) + len(piece) > max_chars:
                chunks.append(current)
                current = piece
            else:
                current += piece
    if current:
        chunks.append(current)
    if not chunks:
        raise ValueError("PDF text chunking produced no content")
    if re.sub(r"\s+", "", "".join(chunks)) != re.sub(r"\s+", "", text):
        raise ValueError("PDF text chunking lost source content")
    return chunks


def build_guide_prompt(title: str, extracted_text: str, extraction: dict[str, Any]) -> str:
    truncation_note = "The complete extractable source text is included in this request."
    return f"""
Create a rigorous Chinese reading guide for the academic paper below.
Return one valid JSON object only. Do not wrap it in Markdown fences.

Rules:
- Write fluent, concise Simplified Chinese for a graduate researcher.
- Base every claim on the supplied paper. Never invent methods, samples, results, theories, or limitations.
- If information is absent, write "文中未明确说明".
- Preserve construct names, statistical notation, effect directions, uncertainty, and causal/associational distinctions.
- Add page anchors such as "p. 3" whenever the page marker supports them.
- Separate authors' claims from your own reading cautions.
- Avoid generic praise and literal machine-translated phrasing.
- Identify advanced statistical or computational methods that the paper actually uses, not methods it merely cites or discusses.
- Treat methods such as Bayesian models, SEM/CFA and other latent-variable models, multilevel or longitudinal models, causal estimators, survival models, network models, machine learning, and simulation as advanced when they are part of the analysis.
- For each advanced method, explain its paper-specific role, structure, variables, estimation, assumptions, diagnostics or fit criteria, interpretation, and limitations. Preserve priors, likelihoods, posterior quantities, estimators, fit indices, thresholds, and uncertainty intervals when reported.
- Do not inflate ordinary descriptive statistics or routine t tests into advanced methods. If the paper uses no advanced statistical method, set advancedStatistics.summary to "本文未使用需要单独拆解的高级统计方法" and return an empty methods array.

Required JSON schema:
{{
  "title": "paper title in Chinese",
  "originalTitle": "original title",
  "oneSentence": "one-sentence central argument",
  "questions": [{{"text": "research question", "page": "p. X"}}],
  "theory": [{{"name": "theory or construct", "role": "how it functions", "page": "p. X"}}],
  "method": {{
    "design": "design",
    "sample": "sample",
    "data": "data/material",
    "analysis": "analysis strategy",
    "page": "p. X"
  }},
  "advancedStatistics": {{
    "summary": "what advanced methods are used and why they matter; or the required no-advanced-method sentence",
    "methods": [
      {{
        "name": "method name, e.g. Bayesian hierarchical model or SEM",
        "role": "the research question this method answers in this paper",
        "modelStructure": "model equations or measurement/structural hierarchy in plain Chinese",
        "inputsAndVariables": "outcomes, predictors, latent variables, priors, levels, or features",
        "estimation": "estimator, algorithm, software, priors, sampling/optimization, and uncertainty",
        "assumptions": "identification and statistical assumptions actually relevant here",
        "diagnostics": "fit indices, convergence checks, robustness or validation reported",
        "interpretation": "how to read the paper's coefficients, posterior quantities, fit, or predictions",
        "limitations": "method-specific limitation or what the method cannot establish",
        "page": "p. X or pp. X-Y"
      }}
    ]
  }},
  "findings": [{{"claim": "finding", "evidence": "key evidence or statistic", "page": "p. X"}}],
  "contributions": [{{"text": "contribution", "page": "p. X"}}],
  "limitations": [{{"text": "author-stated or clearly labeled inferred limitation", "page": "p. X"}}],
  "concepts": [{{"term": "English term", "translation": "recommended Chinese", "meaning": "paper-specific meaning"}}],
  "readingPath": [{{"pages": "pp. X-Y", "focus": "what to read for"}}],
  "cautions": ["interpretive caution"],
  "takeaways": ["actionable takeaway for a communication or social-science researcher"]
}}

File label: {title or "Untitled paper"}
Extraction note: {truncation_note}

    SOURCE TEXT
    {extracted_text}
    """.strip()


def build_synthesis_prompt(title: str, partial_guides: list[dict[str, Any]]) -> str:
    notes = json.dumps(partial_guides, ensure_ascii=False)
    return f"""
Synthesize the following page-grounded partial reading guides into one rigorous Chinese reading guide.
Return one valid JSON object only, using exactly the same schema represented by the partial guides.

Rules:
- Cover the whole paper, not only the introduction.
- Merge duplicates but preserve disagreements, effect directions, statistics, sample details, and page anchors.
- Never invent information absent from the partial guides.
- Prefer fluent academic Chinese over literal phrasing.
- Keep "文中未明确说明" where evidence is unavailable.

File label: {title or "Untitled paper"}
PARTIAL GUIDES
{notes}
""".strip()


def _strip_json_fence(content: str) -> str:
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        return text[first : last + 1]
    return text


def parse_guide_json(content: str) -> dict[str, Any]:
    try:
        value = json.loads(_strip_json_fence(content))
    except json.JSONDecodeError as exc:
        raise ValueError("DeepSeek returned an invalid reading-guide JSON response") from exc
    if not isinstance(value, dict):
        raise ValueError("DeepSeek reading guide must be a JSON object")
    return validate_guide_schema(value)


def validate_guide_schema(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("DeepSeek reading guide must be a JSON object")
    required = (
        "title",
        "oneSentence",
        "questions",
        "theory",
        "method",
        "advancedStatistics",
        "findings",
        "contributions",
        "limitations",
        "concepts",
        "readingPath",
        "cautions",
        "takeaways",
    )
    missing = [key for key in required if key not in value]
    if missing:
        raise ValueError(f"DeepSeek reading guide is missing fields: {', '.join(missing)}")
    for key in ("title", "oneSentence"):
        if not isinstance(value[key], str) or not value[key].strip():
            raise ValueError(f"DeepSeek reading guide field {key} must be non-empty text")
    if "originalTitle" in value and not isinstance(value["originalTitle"], str):
        raise ValueError("DeepSeek reading guide field originalTitle must be text")
    if not isinstance(value["method"], dict):
        raise ValueError("DeepSeek reading guide field method must be an object")
    for key, item in value["method"].items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ValueError("DeepSeek reading guide method values must be text")
    advanced = value["advancedStatistics"]
    if not isinstance(advanced, dict):
        raise ValueError("DeepSeek reading guide field advancedStatistics must be an object")
    if not isinstance(advanced.get("summary"), str) or not advanced["summary"].strip():
        raise ValueError("DeepSeek reading guide advancedStatistics summary must be non-empty text")
    if not isinstance(advanced.get("methods"), list) or not all(
        isinstance(item, dict) for item in advanced["methods"]
    ):
        raise ValueError("DeepSeek reading guide advancedStatistics methods must be an array of objects")
    for item in advanced["methods"]:
        if not isinstance(item.get("name"), str) or not item["name"].strip():
            raise ValueError("DeepSeek reading guide advancedStatistics method name must be non-empty text")
        if not all(isinstance(name, str) and isinstance(field, str) for name, field in item.items()):
            raise ValueError("DeepSeek reading guide advancedStatistics methods contain non-text values")
    for key in (
        "questions",
        "theory",
        "findings",
        "contributions",
        "limitations",
        "concepts",
        "readingPath",
    ):
        if not isinstance(value[key], list) or not all(isinstance(item, dict) for item in value[key]):
            raise ValueError(f"DeepSeek reading guide field {key} must be an array of objects")
        for item in value[key]:
            if not all(isinstance(name, str) and isinstance(field, str) for name, field in item.items()):
                raise ValueError(f"DeepSeek reading guide field {key} contains non-text values")
    for key in ("cautions", "takeaways"):
        if not isinstance(value[key], list) or not all(isinstance(item, str) for item in value[key]):
            raise ValueError(f"DeepSeek reading guide field {key} must be an array of text")
    return value


def load_deepseek_settings(config_file: str | Path) -> dict[str, str]:
    config = toml.load(str(config_file))
    detail = config.get("deepseek_detail") or {}
    key = str(detail.get("deepseek_api_key") or "").strip()
    if not key:
        raise ValueError("DeepSeek API key is not configured for reading guides")
    return {
        "apiKey": key,
        "model": str(detail.get("deepseek_model") or "deepseek-v4-flash").strip(),
        "thinking": str(detail.get("deepseek_thinking_mode") or "disabled").strip(),
    }


def request_deepseek_guide(
    *,
    api_key: str,
    model: str,
    prompt: str,
    endpoint: str = "https://api.deepseek.com/v1/chat/completions",
    timeout: int = 180,
    retry_invalid_json: bool = True,
) -> tuple[dict[str, Any], str]:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You create evidence-grounded academic reading guides and return strict JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": MAX_GUIDE_TOKENS,
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("DeepSeek reading-guide response exceeded 4 MB")
            body = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Do not include the request or headers: they contain the API key.
        message = f"DeepSeek reading-guide request failed with HTTP {exc.code}"
        try:
            error_body = json.loads(exc.read().decode("utf-8"))
            remote = error_body.get("error", {}).get("message")
            if remote:
                message += f": {remote}"
        except Exception:
            pass
        raise ValueError(message) from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"DeepSeek reading-guide network error: {exc.reason}") from exc

    try:
        first_choice = body["choices"][0]
        choice = first_choice["message"]
        content = choice["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("DeepSeek returned an incomplete reading-guide response") from exc
    try:
        guide = parse_guide_json(content)
    except ValueError as exc:
        finish_reason = str(first_choice.get("finish_reason") or "")
        if finish_reason == "length":
            raise ValueError(
                "DeepSeek reading-guide JSON was truncated at the output-token limit"
            ) from exc
        if retry_invalid_json and str(exc) == "DeepSeek returned an invalid reading-guide JSON response":
            return request_deepseek_guide(
                api_key=api_key,
                model=model,
                prompt=prompt + "\n\nYour previous response was invalid JSON. Return one complete JSON object only.",
                endpoint=endpoint,
                timeout=timeout,
                retry_invalid_json=False,
            )
        raise
    return guide, str(body.get("model") or model)


def generate_reading_guide(
    *,
    source_pdf_path: str | Path,
    title: str,
    api_key: str,
    model: str,
    on_progress=None,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    extracted, metadata = extract_pdf_text(source_pdf_path)
    chunks = chunk_extracted_text(extracted)
    metadata = {**metadata, "chunkCount": len(chunks)}
    if len(chunks) == 1:
        if on_progress:
            on_progress(45, "正在生成导读...")
        guide, resolved_model = request_deepseek_guide(
            api_key=api_key,
            model=model,
            prompt=build_guide_prompt(title, chunks[0], metadata),
        )
        return guide, resolved_model, metadata

    partial_guides = []
    resolved_model = model
    for index, chunk in enumerate(chunks, 1):
        if on_progress:
            percent = 20 + int(index / len(chunks) * 55)
            on_progress(percent, f"正在阅读第 {index}/{len(chunks)} 个分块...")
        partial, resolved_model = request_deepseek_guide(
            api_key=api_key,
            model=model,
            prompt=build_guide_prompt(
                f"{title}（第 {index}/{len(chunks)} 个分块）",
                chunk,
                metadata,
            ),
        )
        partial_guides.append(partial)

    if on_progress:
        on_progress(85, "正在合并全文导读...")
    guide, resolved_model = request_deepseek_guide(
        api_key=api_key,
        model=model,
        prompt=build_synthesis_prompt(title, partial_guides),
    )
    return guide, resolved_model, metadata


class ReadingGuideStore:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def json_path(self, guide_id: str) -> Path:
        return self.directory / f"{validate_guide_id(guide_id)}.json"

    def load(self, guide_id: str) -> dict[str, Any] | None:
        path = self.json_path(guide_id)
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                return None
            if value.get("kind") != "guide" or value.get("status") != "success":
                return None
            validate_guide_schema(value.get("guide"))
            return value
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def save(self, guide_id: str, payload: dict[str, Any]) -> Path:
        path = self.json_path(guide_id)
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self._atomic_write(path, data)
        return path

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
