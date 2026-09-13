"""Async review synthesis and explainable literature discovery for ScholarSplit."""

from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    import tomllib
except ImportError:  # pragma: no cover
    tomllib = None

from .research_engine import (
    build_exploratory_prompt,
    build_systematic_prompt,
    rank_recommendations,
    validate_and_normalize_evidence_matrix,
    validate_gap_json,
    validate_synthesis_json,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ResearchService:
    def __init__(self, store: Any, server_root: str | Path):
        self.store = store
        self.root = Path(server_root).resolve()
        self.profile_path = self.root / "data" / "research-profile.json"

    def profile(self) -> dict[str, str]:
        configured = {}
        if self.profile_path.is_file():
            try:
                configured = json.loads(self.profile_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                configured = {}
        detail = {}
        config_path = self.root / "config" / "config.toml"
        if tomllib and config_path.is_file():
            with config_path.open("rb") as stream:
                detail = (tomllib.load(stream).get("deepseek_detail") or {})
        return {
            "provider": "deepseek",
            "model": str(configured.get("model") or detail.get("deepseek_model") or "deepseek-v4-flash"),
            "apiKey": str(detail.get("deepseek_api_key") or ""),
        }

    def start_analysis(self, project_id: str) -> dict[str, Any]:
        project = self.store.get_review_project(project_id)
        if not project:
            raise ValueError("Review project not found")
        job = self.store.create_job(
            {
                "kind": "review_analysis",
                "project_id": project_id,
                "status": "queued",
                "payload": {"projectId": project_id},
            }
        )
        threading.Thread(target=self._run_analysis, args=(job["id"], project), daemon=True).start()
        return job

    def _run_analysis(self, job_id: str, project: Mapping[str, Any]) -> None:
        self.store.update_job(job_id, {"status": "running", "progress": 5, "started_at": _now()})
        try:
            matrix = self.build_project_evidence(str(project["id"]))
            if not matrix:
                raise ValueError("项目中没有可引用的导读、摘要或证据")
            protocol = project.get("protocol") if isinstance(project.get("protocol"), Mapping) else {}
            question = str(protocol.get("question") or protocol.get("research_question") or project.get("description") or project.get("name"))
            mode = str(protocol.get("mode") or "exploratory")
            prompt = (
                build_systematic_prompt(
                    question,
                    matrix,
                    protocol=protocol,
                    inclusion_criteria=protocol.get("inclusion_criteria"),
                    exclusion_criteria=protocol.get("exclusion_criteria"),
                )
                if mode == "systematic"
                else build_exploratory_prompt(question, matrix, context={"project": project.get("name")})
            )
            self.store.update_job(job_id, {"progress": 45})
            result, model = self._request_json(prompt)
            synthesis = validate_synthesis_json({"claims": result.get("claims", [])})
            gaps = validate_gap_json({"gaps": result.get("gaps", [])})
            for claim in synthesis["claims"]:
                self.store.insert(
                    "syntheses",
                    {
                        "project_id": project["id"],
                        "kind": claim["label"],
                        "title": claim["text"][:120],
                        "body": claim["text"],
                        "source_ids": claim["evidence_refs"],
                        "metadata": {"model": model, "generated": True},
                    },
                )
            for gap in gaps["gaps"]:
                self.store.insert(
                    "gaps",
                    {
                        "project_id": project["id"],
                        "title": gap["text"][:160],
                        "description": gap["text"],
                        "evidence_ids": gap["evidence_refs"],
                        "metadata": {"label": gap["label"], "model": model, "generated": True},
                    },
                )
            for lead in result.get("search_leads", []):
                if isinstance(lead, str) and lead.strip():
                    self.store.insert(
                        "recommendations",
                        {
                            "project_id": project["id"],
                            "title": lead.strip(),
                            "body": "由综述中的未闭合问题生成的检索线索",
                            "kind": "search_lead",
                            "priority": 5,
                            "metadata": {"source": "review_analysis"},
                        },
                    )
            self.store.update_job(
                job_id,
                {
                    "status": "completed",
                    "progress": 100,
                    "result": {"claims": len(synthesis["claims"]), "gaps": len(gaps["gaps"]), "model": model},
                    "finished_at": _now(),
                },
            )
        except Exception as exc:
            self.store.update_job(
                job_id,
                {"status": "failed", "error": str(exc), "finished_at": _now()},
            )

    def build_project_evidence(self, project_id: str) -> list[dict[str, Any]]:
        members = self.store.list("review_members", filters={"project_id": project_id}, limit=10_000)
        existing = self.store.list("evidence", filters={"project_id": project_id}, limit=10_000)
        rows = [
            {
                "source_id": str(item.get("paper_id") or item["id"]),
                "claim": item.get("claim") or item.get("theme") or "已编码证据",
                "evidence": item.get("quote") or item.get("claim") or "",
                "location": item.get("locator") or "",
                "label": "evidence",
            }
            for item in existing
            if item.get("quote") or item.get("claim")
        ]
        for member in members:
            paper = self.store.get_paper(str(member["paper_id"]))
            if not paper:
                continue
            guides = [item for item in paper.get("artifacts", []) if item.get("kind") == "reading_guide"]
            for artifact in guides[:1]:
                try:
                    payload = json.loads(Path(artifact["path"]).read_text(encoding="utf-8"))
                    guide = payload.get("guide") or {}
                except (OSError, json.JSONDecodeError):
                    continue
                for index, finding in enumerate(guide.get("findings") or []):
                    if not isinstance(finding, Mapping):
                        continue
                    rows.append(
                        {
                            "source_id": str(paper["id"]),
                            "claim": str(finding.get("claim") or finding.get("text") or ""),
                            "evidence": str(finding.get("evidence") or finding.get("claim") or ""),
                            "location": str(finding.get("page") or ""),
                            "title": paper.get("title"),
                            "year": paper.get("year"),
                            "doi": paper.get("doi"),
                            "method": (guide.get("method") or {}).get("analysis", ""),
                            "sample": (guide.get("method") or {}).get("sample", ""),
                            "label": "evidence",
                        }
                    )
            if not guides and paper.get("abstract"):
                rows.append(
                    {
                        "source_id": str(paper["id"]),
                        "claim": str(paper["abstract"]),
                        "evidence": str(paper["abstract"]),
                        "location": "abstract",
                        "title": paper.get("title"),
                        "year": paper.get("year"),
                        "doi": paper.get("doi"),
                        "label": "evidence",
                    }
                )
        normalized = validate_and_normalize_evidence_matrix(rows)
        for row in normalized:
            if not any(item.get("claim") == row["claim"] and item.get("paper_id") == row["source_id"] for item in existing):
                self.store.insert(
                    "evidence",
                    {
                        "project_id": project_id,
                        "paper_id": row["source_id"],
                        "claim": row["claim"],
                        "quote": row["evidence"],
                        "locator": row.get("location"),
                        "theme": row.get("title"),
                        "metadata": {"label": row["label"], "method": row.get("method"), "sample": row.get("sample")},
                    },
                )
        return normalized

    def discover(self, project_id: str, query: str, limit: int = 12) -> list[dict[str, Any]]:
        params = urllib.parse.urlencode({"search": query, "per-page": min(max(limit, 1), 25)})
        request = urllib.request.Request(
            f"https://api.openalex.org/works?{params}",
            headers={"User-Agent": "ScholarSplit/0.2 (local research workbench)"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read(4 * 1024 * 1024).decode("utf-8"))
        candidates = []
        for item in payload.get("results", []):
            primary = item.get("primary_location") or {}
            best_oa = item.get("best_oa_location") or {}
            candidates.append(
                {
                    "title": item.get("display_name") or "",
                    "doi": item.get("doi"),
                    "year": item.get("publication_year"),
                    "authors": [author.get("author", {}).get("display_name") for author in item.get("authorships", []) if author.get("author")],
                    "citation_proximity": min(float(item.get("cited_by_count") or 0) / 500, 1),
                    "gap_match": 1,
                    "local_match": 0,
                    "recency": max(0, min(1, (int(item.get("publication_year") or 2000) - 2000) / 30)),
                    "reason": f"与检索线索“{query}”相关，来自 OpenAlex",
                    "url": best_oa.get("pdf_url") or primary.get("landing_page_url") or item.get("doi"),
                    "oa_url": best_oa.get("pdf_url"),
                    "source": "openalex",
                }
            )
        ranked = rank_recommendations(candidates, gap_terms=query.split())
        stored = []
        for item in ranked[:limit]:
            stored.append(
                self.store.insert(
                    "recommendations",
                    {
                        "project_id": project_id,
                        "title": item["title"],
                        "body": item.get("reason", ""),
                        "kind": "external_paper",
                        "priority": int(round(float(item.get("score", 0)) * 100)),
                        "metadata": item,
                    },
                )
            )
        return stored

    def _request_json(self, prompt: str) -> tuple[dict[str, Any], str]:
        profile = self.profile()
        if not profile["apiKey"]:
            raise ValueError("研究模型 API Key 尚未配置")
        body = {
            "model": profile["model"],
            "messages": [
                {"role": "system", "content": "You synthesize academic evidence and return strict JSON."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 12000,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            "https://api.deepseek.com/v1/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {profile['apiKey']}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=240) as response:
                payload = json.loads(response.read(4 * 1024 * 1024).decode("utf-8"))
        except Exception as exc:
            code = getattr(exc, "code", None)
            raise ValueError(f"研究模型请求失败{f'（HTTP {code}）' if code else ''}") from exc
        content = payload.get("choices", [{}])[0].get("message", {}).get("content")
        if not isinstance(content, str):
            raise ValueError("研究模型返回内容不完整")
        return json.loads(content), str(payload.get("model") or profile["model"])
