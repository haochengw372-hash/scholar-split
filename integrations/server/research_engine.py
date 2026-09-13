"""Deterministic, local-first helpers for ScholarSplit research intelligence.

This module deliberately has no network or model-SDK dependency.  It validates
the JSON boundary around a model, builds auditable prompts, and ranks local
recommendations.  Secrets are rejected rather than retained in normalized
objects, hashes, prompts, or cache keys.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Any


EVIDENCE_LABELS = frozenset({"evidence", "inference", "hypothesis", "unverified"})
ACQUISITION_STATUSES = frozenset(
    {"oa", "institution", "repository", "delivery", "unavailable"}
)
DEFAULT_SCORE_WEIGHTS = {
    "local_match": 0.40,
    "citation_proximity": 0.25,
    "recency": 0.15,
    "gap_match": 0.20,
}

_SECRET_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "accesstoken",
        "refreshtoken",
        "clientsecret",
        "password",
        "secret",
        "token",
        "credential",
    }
)
_SPACE_RE = re.compile(r"\s+")
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
_ARXIV_RE = re.compile(
    r"^(?:[a-z][a-z.\-]+/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?$", re.IGNORECASE
)
_TERM_RE = re.compile(r"[^\W_]+(?:['\N{RIGHT SINGLE QUOTATION MARK}-][^\W_]+)*", re.UNICODE)


class ResearchValidationError(ValueError):
    """Raised when research intelligence JSON violates its public contract."""


class AcquisitionTransitionError(ResearchValidationError):
    """Raised when an acquisition action is invalid or insufficiently confirmed."""


def _clean_text(value: Any, field: str, *, required: bool = True) -> str:
    if value is None:
        if required:
            raise ResearchValidationError(f"{field} is required")
        return ""
    if not isinstance(value, str):
        raise ResearchValidationError(f"{field} must be a string")
    cleaned = _SPACE_RE.sub(" ", unicodedata.normalize("NFKC", value)).strip()
    if required and not cleaned:
        raise ResearchValidationError(f"{field} must not be empty")
    return cleaned


def _normalized_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).casefold())


def _reject_secret_fields(value: Any, path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = _normalized_key(key)
            if normalized in _SECRET_KEYS or normalized.endswith(
                ("apikey", "accesstoken", "refreshtoken", "clientsecret")
            ):
                raise ResearchValidationError(
                    f"{path}.{key} is a secret field and must not be included or stored"
                )
            _reject_secret_fields(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_secret_fields(child, f"{path}[{index}]")


def _json_object(payload: Any, name: str) -> dict[str, Any]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ResearchValidationError(f"{name} is not valid JSON: {exc.msg}") from exc
    if not isinstance(payload, Mapping):
        raise ResearchValidationError(f"{name} must be a JSON object")
    _reject_secret_fields(payload, name)
    return dict(payload)


def _first(item: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in item and item[name] is not None:
            return item[name]
    return None


def normalize_doi(value: Any) -> str:
    """Return a canonical DOI, or an empty string when one is not supplied."""

    if value is None:
        return ""
    doi = _clean_text(str(value), "doi", required=False).casefold()
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi).strip()
    doi = doi.rstrip(".,;)")
    return doi if _DOI_RE.match(doi) else ""


def normalize_arxiv_id(value: Any) -> str:
    """Return a canonical, version-insensitive arXiv identifier."""

    if value is None:
        return ""
    arxiv_id = _clean_text(str(value), "arxiv_id", required=False).casefold()
    arxiv_id = re.sub(r"^(?:https?://arxiv\.org/(?:abs|pdf)/|arxiv:\s*)", "", arxiv_id)
    arxiv_id = re.sub(r"\.pdf$", "", arxiv_id).strip().rstrip(".,;)")
    if not _ARXIV_RE.match(arxiv_id):
        return ""
    return re.sub(r"v\d+$", "", arxiv_id)


def _normalize_year(value: Any, field: str = "year") -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ResearchValidationError(f"{field} must be a four-digit year")
    try:
        year = int(value)
    except (TypeError, ValueError) as exc:
        raise ResearchValidationError(f"{field} must be a four-digit year") from exc
    if year < 1000 or year > 9999:
        raise ResearchValidationError(f"{field} must be a four-digit year")
    return year


def _normalize_string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values: Sequence[Any] = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = value
    else:
        raise ResearchValidationError(f"{field} must be a string or list of strings")
    result: list[str] = []
    seen: set[str] = set()
    for index, entry in enumerate(values):
        text = _clean_text(entry, f"{field}[{index}]")
        marker = text.casefold()
        if marker not in seen:
            result.append(text)
            seen.add(marker)
    return result


def validate_and_normalize_evidence_matrix(payload: Any) -> list[dict[str, Any]]:
    """Validate evidence rows and return a deterministic, de-duplicated matrix.

    Accepted wrappers are ``{"rows": [...]}``, ``{"evidence": [...]}``, and
    ``{"items": [...]}``; a bare list is also accepted.  Aliases are accepted
    at input but output always uses ``source_id``, ``claim``, ``evidence``,
    ``location``, ``doi``, ``arxiv_id``, ``title``, ``year``, and ``label``.
    """

    _reject_secret_fields(payload, "evidence_matrix")
    if isinstance(payload, Mapping):
        rows = _first(payload, "rows", "evidence", "items")
    else:
        rows = payload
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        raise ResearchValidationError("evidence_matrix must be a list of rows")

    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            raise ResearchValidationError(f"evidence_matrix[{index}] must be an object")
        source_id = _clean_text(
            _first(raw, "source_id", "sourceId", "study_id", "studyId", "source", "id"),
            f"evidence_matrix[{index}].source_id",
        )
        claim = _clean_text(
            _first(raw, "claim", "finding", "statement"),
            f"evidence_matrix[{index}].claim",
        )
        evidence = _clean_text(
            _first(raw, "evidence", "evidence_text", "evidenceText", "excerpt", "quote", "support"),
            f"evidence_matrix[{index}].evidence",
        )
        raw_location = _first(raw, "location", "citation", "page", "locator")
        location = (
            _clean_text(str(raw_location), f"evidence_matrix[{index}].location", required=False)
            if raw_location is not None
            else ""
        )
        label = str(raw.get("label", "evidence")).strip().casefold()
        if label not in EVIDENCE_LABELS:
            raise ResearchValidationError(
                f"evidence_matrix[{index}].label must be one of {sorted(EVIDENCE_LABELS)}"
            )
        year = _normalize_year(raw.get("year"), f"evidence_matrix[{index}].year")
        row: dict[str, Any] = {
            "source_id": source_id,
            "claim": claim,
            "evidence": evidence,
            "label": label,
        }
        optional_text = {
            "location": location,
            "doi": normalize_doi(raw.get("doi")),
            "arxiv_id": normalize_arxiv_id(_first(raw, "arxiv_id", "arxivId", "arxiv")),
            "title": _clean_text(
                _first(raw, "title", "source_title", "sourceTitle"),
                "title",
                required=False,
            ),
            "method": _clean_text(raw.get("method"), "method", required=False),
            "sample": _clean_text(raw.get("sample"), "sample", required=False),
            "limitations": _clean_text(raw.get("limitations"), "limitations", required=False),
        }
        row.update({key: value for key, value in optional_text.items() if value})
        if year is not None:
            row["year"] = year
        marker = (source_id.casefold(), claim.casefold(), evidence.casefold(), location.casefold())
        if marker not in seen:
            normalized.append(row)
            seen.add(marker)
    return normalized


normalize_evidence_matrix = validate_and_normalize_evidence_matrix
validate_evidence_matrix = validate_and_normalize_evidence_matrix


def _validate_labeled_collection(
    payload: Any,
    *,
    collection_key: str,
    text_aliases: tuple[str, ...],
) -> dict[str, Any]:
    obj = _json_object(payload, f"{collection_key}_json")
    values = obj.get(collection_key)
    if not isinstance(values, list):
        raise ResearchValidationError(f"{collection_key} must be a list")

    result_items: list[dict[str, Any]] = []
    for index, raw in enumerate(values):
        if not isinstance(raw, Mapping):
            raise ResearchValidationError(f"{collection_key}[{index}] must be an object")
        label = str(raw.get("label", "")).strip().casefold()
        if label not in EVIDENCE_LABELS:
            raise ResearchValidationError(
                f"{collection_key}[{index}].label must be one of {sorted(EVIDENCE_LABELS)}"
            )
        text_value = _first(raw, *text_aliases)
        text = _clean_text(text_value, f"{collection_key}[{index}].text")
        refs = _normalize_string_list(
            _first(raw, "evidence_refs", "evidenceRefs", "source_ids", "sourceIds"),
            f"{collection_key}[{index}].evidence_refs",
        )
        if label == "evidence" and not refs:
            raise ResearchValidationError(
                f"{collection_key}[{index}] labeled evidence requires evidence_refs"
            )
        item = dict(raw)
        for alias in (*text_aliases, "evidenceRefs", "source_ids", "sourceIds"):
            item.pop(alias, None)
        item["text"] = text
        item["label"] = label
        item["evidence_refs"] = refs
        result_items.append(item)

    result = dict(obj)
    result[collection_key] = result_items
    result.setdefault("schema_version", "1")
    return result


def validate_synthesis_json(payload: Any) -> dict[str, Any]:
    """Validate a synthesis object and normalize its labeled claims."""

    return _validate_labeled_collection(
        payload, collection_key="claims", text_aliases=("text", "claim", "statement")
    )


def validate_gap_json(payload: Any) -> dict[str, Any]:
    """Validate a research-gap object and normalize its labeled gaps."""

    return _validate_labeled_collection(
        payload, collection_key="gaps", text_aliases=("text", "gap", "statement")
    )


validate_synthesis = validate_synthesis_json
validate_gaps = validate_gap_json


_LABEL_INSTRUCTIONS = """Every claim must use exactly one label:
- evidence: directly supported by cited evidence_refs (at least one required)
- inference: a reasoned interpretation grounded in the corpus
- hypothesis: a testable proposition for future research
- unverified: plausible but not established from the supplied corpus
Never invent a source, quotation, statistic, DOI, or evidence reference."""


def _prompt_context(value: Any, name: str) -> str:
    if value is None:
        return "none supplied"
    _reject_secret_fields(value, name)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_exploratory_prompt(
    research_question: str,
    evidence_matrix: Any = None,
    *,
    context: Any = None,
    max_items: int = 12,
) -> str:
    """Build a bounded discovery prompt that clearly marks uncertain claims."""

    question = _clean_text(research_question, "research_question")
    if not isinstance(max_items, int) or isinstance(max_items, bool) or max_items < 1:
        raise ResearchValidationError("max_items must be a positive integer")
    matrix = (
        validate_and_normalize_evidence_matrix(evidence_matrix)
        if evidence_matrix is not None
        else []
    )
    return (
        "You are conducting an exploratory, non-exhaustive literature analysis.\n"
        f"Research question: {question}\n"
        f"Context JSON: {_prompt_context(context, 'context')}\n"
        f"Evidence matrix JSON: {_prompt_context(matrix, 'evidence_matrix')}\n\n"
        f"{_LABEL_INSTRUCTIONS}\n"
        f"Return JSON only with at most {max_items} claims and at most {max_items} gaps: "
        '{"mode":"exploratory","claims":[{"text":"...","label":"evidence|inference|hypothesis|unverified","evidence_refs":[]}],'
        '"gaps":[{"text":"...","label":"evidence|inference|hypothesis|unverified","evidence_refs":[]}],'
        '"search_leads":["..."]}. Treat search leads as suggestions, not proof.'
    )


def build_systematic_prompt(
    research_question: str,
    evidence_matrix: Any,
    *,
    protocol: Any = None,
    inclusion_criteria: Sequence[str] | str | None = None,
    exclusion_criteria: Sequence[str] | str | None = None,
) -> str:
    """Build an audit-oriented prompt for a protocol-bounded synthesis."""

    question = _clean_text(research_question, "research_question")
    matrix = validate_and_normalize_evidence_matrix(evidence_matrix)
    include = _normalize_string_list(inclusion_criteria, "inclusion_criteria")
    exclude = _normalize_string_list(exclusion_criteria, "exclusion_criteria")
    return (
        "You are conducting a systematic, protocol-bounded evidence synthesis. "
        "Do not imply exhaustiveness beyond the supplied protocol and corpus.\n"
        f"Research question: {question}\n"
        f"Protocol JSON: {_prompt_context(protocol, 'protocol')}\n"
        f"Inclusion criteria JSON: {_prompt_context(include, 'inclusion_criteria')}\n"
        f"Exclusion criteria JSON: {_prompt_context(exclude, 'exclusion_criteria')}\n"
        f"Evidence matrix JSON: {_prompt_context(matrix, 'evidence_matrix')}\n\n"
        f"{_LABEL_INSTRUCTIONS}\n"
        "Preserve disagreements, negative findings, methodological limitations, and coverage "
        "boundaries. Return JSON only: "
        '{"mode":"systematic","claims":[{"text":"...","label":"evidence|inference|hypothesis|unverified","evidence_refs":[]}],'
        '"gaps":[{"text":"...","label":"evidence|inference|hypothesis|unverified","evidence_refs":[]}],'
        '"coverage":{"included_source_ids":[],"excluded_source_ids":[],"limitations":[]}}.'
    )


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ResearchValidationError("value must be JSON-serializable") from exc


def _order_insensitive_corpus(corpus: Any) -> Any:
    if isinstance(corpus, Mapping):
        result = dict(corpus)
        for key in ("documents", "sources", "records", "items"):
            if isinstance(result.get(key), list):
                result[key] = sorted(result[key], key=_canonical_json)
        return result
    if isinstance(corpus, Sequence) and not isinstance(corpus, (str, bytes, bytearray)):
        return sorted(list(corpus), key=_canonical_json)
    return corpus


def corpus_hash(corpus: Any) -> str:
    """Return a stable SHA-256 for a corpus, independent of record order."""

    _reject_secret_fields(corpus, "corpus")
    canonical = _canonical_json(_order_insensitive_corpus(corpus))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


compute_corpus_hash = corpus_hash


def make_cache_key(
    operation: str,
    corpus: Any,
    parameters: Any = None,
    *,
    version: str = "v1",
) -> str:
    """Build a deterministic cache key without retaining corpus content."""

    op = _clean_text(operation, "operation")
    cache_version = _clean_text(version, "version")
    _reject_secret_fields(parameters, "parameters")
    material = {
        "version": cache_version,
        "operation": op,
        "corpus_hash": corpus_hash(corpus),
        "parameters": parameters,
    }
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


build_cache_key = make_cache_key


def normalize_fts_terms(text: str, *, max_terms: int = 32) -> list[str]:
    """Extract safe Unicode terms, removing SQLite FTS operators and punctuation."""

    value = _clean_text(text, "query", required=False)
    if not isinstance(max_terms, int) or isinstance(max_terms, bool) or max_terms < 1:
        raise ResearchValidationError("max_terms must be a positive integer")
    result: list[str] = []
    seen: set[str] = set()
    operators = {"and", "or", "not", "near"}
    for match in _TERM_RE.finditer(value):
        term = match.group(0).casefold().strip("'-\N{RIGHT SINGLE QUOTATION MARK}")
        if term and term not in operators and term not in seen:
            result.append(term)
            seen.add(term)
        if len(result) >= max_terms:
            break
    return result


def build_fts_query(
    text: str,
    *,
    operator: str = "AND",
    prefix: bool = True,
    column: str | None = None,
    max_terms: int = 32,
) -> str:
    """Build a safe SQLite FTS5 MATCH expression from user text."""

    joiner = operator.upper()
    if joiner not in {"AND", "OR"}:
        raise ResearchValidationError("operator must be AND or OR")
    if column is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", column):
        raise ResearchValidationError("column must be a safe SQLite identifier")
    suffix = "*" if prefix else ""
    terms = normalize_fts_terms(text, max_terms=max_terms)
    expression = f" {joiner} ".join(f'"{term.replace(chr(34), chr(34) * 2)}"{suffix}' for term in terms)
    if expression and column:
        return f"{column} : ({expression})"
    return expression


fts_query = build_fts_query


def recommendation_identity(item: Mapping[str, Any]) -> tuple[str, str] | None:
    """Return the strongest available recommendation identity."""

    doi = normalize_doi(_first(item, "doi", "DOI"))
    if doi:
        return ("doi", doi)
    arxiv_id = normalize_arxiv_id(_first(item, "arxiv_id", "arxivId", "arxiv"))
    if arxiv_id:
        return ("arxiv", arxiv_id)
    title = _clean_text(item.get("title"), "title", required=False)
    year = _normalize_year(item.get("year"))
    if title and year is not None:
        canonical_title = "".join(
            character
            for character in unicodedata.normalize("NFKD", title).casefold()
            if character.isalnum()
        )
        if canonical_title:
            return ("title_year", f"{canonical_title}:{year}")
    return None


def _all_identities(item: Mapping[str, Any]) -> list[tuple[str, str]]:
    identities: list[tuple[str, str]] = []
    doi = normalize_doi(_first(item, "doi", "DOI"))
    arxiv_id = normalize_arxiv_id(_first(item, "arxiv_id", "arxivId", "arxiv"))
    if doi:
        identities.append(("doi", doi))
    if arxiv_id:
        identities.append(("arxiv", arxiv_id))
    title = _clean_text(item.get("title"), "title", required=False)
    year = _normalize_year(item.get("year"))
    if title and year is not None:
        title_key = "".join(
            char for char in unicodedata.normalize("NFKD", title).casefold() if char.isalnum()
        )
        if title_key:
            identities.append(("title_year", f"{title_key}:{year}"))
    return identities


def _quality_score(item: Mapping[str, Any]) -> float:
    for key in ("score", "total_score", "relevance_score"):
        if key in item:
            try:
                return float(item[key])
            except (TypeError, ValueError):
                pass
    return 0.0


def _merge_duplicate(preferred: Mapping[str, Any], other: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(preferred)
    for key, value in other.items():
        if key not in merged or merged[key] in (None, "", [], {}):
            merged[key] = value
        elif isinstance(merged[key], list) and isinstance(value, list):
            merged[key] = list(dict.fromkeys([*merged[key], *value]))
    merged["duplicate_count"] = int(preferred.get("duplicate_count", 1)) + int(
        other.get("duplicate_count", 1)
    )
    identifiers = [f"{kind}:{value}" for kind, value in _all_identities(merged)]
    merged["matched_identifiers"] = sorted(set(identifiers))
    return merged


def dedupe_recommendations(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """De-duplicate by any shared DOI, arXiv ID, or normalized title and year."""

    if isinstance(items, (str, bytes, Mapping)):
        raise ResearchValidationError("recommendations must be an iterable of objects")
    output: list[dict[str, Any]] = []
    identity_index: dict[tuple[str, str], int] = {}
    for position, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise ResearchValidationError(f"recommendations[{position}] must be an object")
        _reject_secret_fields(item, f"recommendations[{position}]")
        candidate = dict(item)
        identities = _all_identities(candidate)
        matched = {identity_index[key] for key in identities if key in identity_index}
        if not matched:
            index = len(output)
            candidate.setdefault("duplicate_count", 1)
            candidate["matched_identifiers"] = sorted(
                f"{kind}:{value}" for kind, value in identities
            )
            output.append(candidate)
        else:
            index = min(matched)
            current = output[index]
            if _quality_score(candidate) > _quality_score(current):
                output[index] = _merge_duplicate(candidate, current)
            else:
                output[index] = _merge_duplicate(current, candidate)
            candidate = output[index]
            identities = _all_identities(candidate)
            # A bridge record may connect two earlier partial records (for
            # example DOI-only and title/year-only metadata). Collapse those
            # components rather than leaving a transitive duplicate behind.
            for duplicate_index in sorted(matched - {index}, reverse=True):
                output[index] = _merge_duplicate(output[index], output[duplicate_index])
                output.pop(duplicate_index)
            if len(matched) > 1:
                identity_index.clear()
                for output_index, output_item in enumerate(output):
                    for identity in _all_identities(output_item):
                        identity_index[identity] = output_index
                index = next(
                    identity_index[key] for key in identities if key in identity_index
                )
        for identity in identities:
            identity_index[identity] = index
    return output


deduplicate_recommendations = dedupe_recommendations


def _unit_interval(value: Any, field: str) -> float:
    if value in (None, ""):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ResearchValidationError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise ResearchValidationError(f"{field} must be finite")
    if 1 < number <= 100:
        number /= 100
    return min(1.0, max(0.0, number))


def _keyword_overlap(item: Mapping[str, Any], terms: Sequence[str]) -> float:
    wanted = {
        _clean_text(str(term), "gap_term", required=False).casefold()
        for term in terms
        if str(term).strip()
    }
    if not wanted:
        return 0.0
    haystack = " ".join(
        str(item.get(field, "")) for field in ("title", "abstract", "keywords")
    )
    present = set(normalize_fts_terms(haystack, max_terms=256))
    return len(wanted & present) / len(wanted)


def _score_inputs(
    item: Mapping[str, Any], current_year: int, gap_terms: Sequence[str]
) -> dict[str, float]:
    local = _first(item, "local_match", "local_similarity", "relevance_score")
    local_match = _unit_interval(local, "local_match")

    proximity = _first(item, "citation_proximity", "citation_similarity")
    if proximity is not None:
        citation_proximity = _unit_interval(proximity, "citation_proximity")
    elif item.get("citation_distance") not in (None, ""):
        try:
            distance = max(0.0, float(item["citation_distance"]))
        except (TypeError, ValueError) as exc:
            raise ResearchValidationError("citation_distance must be numeric") from exc
        citation_proximity = 1.0 / (1.0 + distance)
    else:
        citation_proximity = 0.0

    year = _normalize_year(item.get("year"))
    recency = 0.0 if year is None else max(0.0, min(1.0, 1.0 - max(0, current_year - year) / 10))
    explicit_gap = _first(item, "gap_match", "gap_similarity")
    gap_match = (
        _unit_interval(explicit_gap, "gap_match")
        if explicit_gap is not None
        else _keyword_overlap(item, gap_terms)
    )
    return {
        "local_match": local_match,
        "citation_proximity": citation_proximity,
        "recency": recency,
        "gap_match": gap_match,
    }


def score_recommendation(
    item: Mapping[str, Any],
    *,
    current_year: int | None = None,
    gap_terms: Sequence[str] = (),
    weights: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Return the recommendation plus an auditable weighted score breakdown."""

    if not isinstance(item, Mapping):
        raise ResearchValidationError("recommendation must be an object")
    _reject_secret_fields(item, "recommendation")
    resolved_year = date.today().year if current_year is None else _normalize_year(current_year)
    assert resolved_year is not None
    resolved_weights = dict(DEFAULT_SCORE_WEIGHTS if weights is None else weights)
    if set(resolved_weights) != set(DEFAULT_SCORE_WEIGHTS):
        raise ResearchValidationError(
            f"weights must contain exactly {sorted(DEFAULT_SCORE_WEIGHTS)}"
        )
    try:
        numeric_weights = {
            name: float(value) for name, value in resolved_weights.items()
        }
    except (TypeError, ValueError) as exc:
        raise ResearchValidationError("weights must be numeric") from exc
    if any(not math.isfinite(value) or value < 0 for value in numeric_weights.values()):
        raise ResearchValidationError("weights must be finite non-negative numbers")
    weight_total = sum(numeric_weights.values())
    if weight_total <= 0:
        raise ResearchValidationError("at least one weight must be positive")
    numeric_weights = {key: value / weight_total for key, value in numeric_weights.items()}

    raw = _score_inputs(item, resolved_year, gap_terms)
    contributions = {key: raw[key] * numeric_weights[key] for key in raw}
    total = sum(contributions.values())
    result = dict(item)
    result["score"] = round(total, 6)
    result["total_score"] = result["score"]
    result["score_breakdown"] = {
        key: {
            "value": round(raw[key], 6),
            "weight": round(numeric_weights[key], 6),
            "contribution": round(contributions[key], 6),
        }
        for key in DEFAULT_SCORE_WEIGHTS
    }
    result["score_explanation"] = "; ".join(
        f"{key}={raw[key]:.3f} x {numeric_weights[key]:.2f}"
        for key in DEFAULT_SCORE_WEIGHTS
    )
    return result


def rank_recommendations(
    items: Iterable[Mapping[str, Any]],
    *,
    current_year: int | None = None,
    gap_terms: Sequence[str] = (),
    weights: Mapping[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Score, de-duplicate, and deterministically rank recommendations."""

    scored = [
        score_recommendation(
            item, current_year=current_year, gap_terms=gap_terms, weights=weights
        )
        for item in items
    ]
    deduped = dedupe_recommendations(scored)
    return sorted(
        deduped,
        key=lambda item: (
            -float(item.get("score", 0)),
            str(item.get("title", "")).casefold(),
            str(item.get("doi", "")),
        ),
    )


def validate_acquisition_transition(
    current_status: str | None,
    target_status: str,
    *,
    dry_run: bool,
    download: bool = False,
    batch_size: int = 1,
    batch_confirmed: bool = False,
    dry_run_completed: bool = False,
) -> dict[str, Any]:
    """Validate a lawful acquisition state change and download safety gates.

    A dry run never authorizes a download.  Execution of any download requires
    a completed dry run; execution of a batch (more than one item) additionally
    requires explicit batch confirmation.
    """

    current = None if current_status is None else str(current_status).strip().casefold()
    target = str(target_status).strip().casefold()
    if current is not None and current not in ACQUISITION_STATUSES:
        raise AcquisitionTransitionError(
            f"current_status must be one of {sorted(ACQUISITION_STATUSES)} or None"
        )
    if target not in ACQUISITION_STATUSES:
        raise AcquisitionTransitionError(
            f"target_status must be one of {sorted(ACQUISITION_STATUSES)}"
        )
    if not isinstance(dry_run, bool):
        raise AcquisitionTransitionError("dry_run must be explicitly true or false")
    if not isinstance(download, bool):
        raise AcquisitionTransitionError("download must be a boolean")
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
        raise AcquisitionTransitionError("batch_size must be a positive integer")
    if not isinstance(batch_confirmed, bool) or not isinstance(dry_run_completed, bool):
        raise AcquisitionTransitionError("confirmation flags must be booleans")

    if download and target == "unavailable":
        raise AcquisitionTransitionError("an unavailable item cannot be downloaded")
    if download and not dry_run:
        if not dry_run_completed:
            raise AcquisitionTransitionError(
                "download execution requires a completed dry run"
            )
        if batch_size > 1 and not batch_confirmed:
            raise AcquisitionTransitionError(
                "batch download requires explicit batch confirmation"
            )

    return {
        "from": current,
        "to": target,
        "status": target,
        "dry_run": dry_run,
        "download_requested": download,
        "batch_size": batch_size,
        "requires_batch_confirmation": bool(download and batch_size > 1),
        "can_download": bool(download and not dry_run),
        "action": "preview" if dry_run else "execute",
    }


validate_acquisition_status_transition = validate_acquisition_transition


__all__ = [
    "ACQUISITION_STATUSES",
    "DEFAULT_SCORE_WEIGHTS",
    "EVIDENCE_LABELS",
    "AcquisitionTransitionError",
    "ResearchValidationError",
    "build_cache_key",
    "build_exploratory_prompt",
    "build_fts_query",
    "build_systematic_prompt",
    "compute_corpus_hash",
    "corpus_hash",
    "dedupe_recommendations",
    "deduplicate_recommendations",
    "fts_query",
    "make_cache_key",
    "normalize_arxiv_id",
    "normalize_doi",
    "normalize_evidence_matrix",
    "normalize_fts_terms",
    "rank_recommendations",
    "recommendation_identity",
    "score_recommendation",
    "validate_acquisition_status_transition",
    "validate_acquisition_transition",
    "validate_evidence_matrix",
    "validate_gap_json",
    "validate_gaps",
    "validate_synthesis",
    "validate_synthesis_json",
]
