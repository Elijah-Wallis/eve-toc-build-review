#!/usr/bin/env python3
"""Consolidate many Retell knowledge bases into one and repoint active Retell LLMs.

Safety defaults:
- Dry run by default.
- Creates a JSON report under artifacts/.
- Deletes old KBs only with --delete-old.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

API_BASE = "https://api.retellai.com"


class RetellError(RuntimeError):
    pass


@dataclass
class SourceDoc:
    kb_id: str
    kb_name: str
    source_id: str
    filename: str
    file_url: str
    content: str
    sha256: str


class RetellClient:
    def __init__(self, api_key: str, timeout: float = 60.0) -> None:
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {api_key}"})

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        resp = self._session.request(method, f"{API_BASE}{path}", timeout=self._timeout, **kwargs)
        if resp.status_code >= 400:
            body = resp.text[:1000]
            raise RetellError(f"{method} {path} failed ({resp.status_code}): {body}")
        return resp

    def list_knowledge_bases(self) -> list[dict[str, Any]]:
        return self._request("GET", "/list-knowledge-bases").json()

    def get_knowledge_base(self, kb_id: str) -> dict[str, Any]:
        return self._request("GET", f"/get-knowledge-base/{kb_id}").json()

    def create_knowledge_base_from_markdown(self, name: str, filename: str, content: str) -> dict[str, Any]:
        files = [("knowledge_base_files", (filename, content.encode("utf-8"), "text/markdown"))]
        data = {"knowledge_base_name": name}
        return self._request("POST", "/create-knowledge-base", data=data, files=files).json()

    def delete_knowledge_base(self, kb_id: str) -> None:
        self._request("DELETE", f"/delete-knowledge-base/{kb_id}")

    def list_agents(self) -> list[dict[str, Any]]:
        return self._request("GET", "/list-agents").json()

    def get_retell_llm(self, llm_id: str) -> dict[str, Any]:
        return self._request("GET", f"/get-retell-llm/{llm_id}").json()

    def update_retell_llm(self, llm_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", f"/update-retell-llm/{llm_id}", json=payload).json()


def wait_for_kb_complete(client: RetellClient, kb_id: str, timeout_sec: int = 300) -> dict[str, Any]:
    started = time.time()
    while time.time() - started < timeout_sec:
        obj = client.get_knowledge_base(kb_id)
        status = (obj.get("status") or "").lower()
        if status == "complete":
            return obj
        if status == "failed":
            raise RetellError(f"Knowledge base {kb_id} failed to process: {obj.get('error_messages')}")
        time.sleep(3)
    raise RetellError(f"Timed out waiting for knowledge base {kb_id} to complete")


def choose_latest_agents(agents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for agent in agents:
        agent_id = agent.get("agent_id")
        if not agent_id:
            continue
        current = latest.get(agent_id)
        if current is None or int(agent.get("version") or 0) > int(current.get("version") or 0):
            latest[agent_id] = agent
    return latest


def collect_source_docs(kbs: list[dict[str, Any]]) -> list[SourceDoc]:
    docs: list[SourceDoc] = []
    for kb in kbs:
        kb_id = kb.get("knowledge_base_id", "")
        kb_name = kb.get("knowledge_base_name", "")
        for src in kb.get("knowledge_base_sources") or []:
            if src.get("type") != "document":
                continue
            file_url = src.get("file_url")
            if not file_url:
                continue
            resp = requests.get(file_url, timeout=60)
            resp.raise_for_status()
            content = resp.text
            sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
            docs.append(
                SourceDoc(
                    kb_id=kb_id,
                    kb_name=kb_name,
                    source_id=src.get("source_id", ""),
                    filename=src.get("filename") or f"{src.get('source_id', 'source')}.md",
                    file_url=file_url,
                    content=content,
                    sha256=sha,
                )
            )
    return docs


def dedupe_docs(docs: list[SourceDoc]) -> list[SourceDoc]:
    seen: set[str] = set()
    out: list[SourceDoc] = []
    for d in docs:
        if d.sha256 in seen:
            continue
        seen.add(d.sha256)
        out.append(d)
    return out


def build_combined_markdown(unique_docs: list[SourceDoc]) -> str:
    now = datetime.now(timezone.utc).isoformat()
    lines: list[str] = [
        "# Unified Retell Knowledge Base",
        "",
        f"Generated: {now}",
        "",
        "This file consolidates all legacy Retell KB document sources.",
        "",
    ]

    for idx, doc in enumerate(unique_docs, start=1):
        lines.extend(
            [
                f"## Source {idx}: {doc.filename}",
                "",
                f"- Original KB ID: `{doc.kb_id}`",
                f"- Original KB Name: {doc.kb_name}",
                f"- Original Source ID: `{doc.source_id}`",
                f"- SHA256: `{doc.sha256}`",
                "",
                "```markdown",
                doc.content.rstrip(),
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def write_report(report: dict[str, Any]) -> Path:
    out_dir = Path("artifacts")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"retell_kb_consolidation_{ts}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", default="MedSpa Unified Knowledge Base", help="Name for the new consolidated knowledge base")
    ap.add_argument("--apply", action="store_true", help="Apply changes (create KB + repoint LLMs)")
    ap.add_argument("--delete-old", action="store_true", help="Delete old KBs after successful repoint")
    ap.add_argument("--timeout", type=int, default=300, help="Processing wait timeout for new KB")
    args = ap.parse_args()

    api_key = (os.getenv("RETELL_API_KEY") or "").strip()
    if not api_key:
        print("RETELL_API_KEY is required", file=sys.stderr)
        return 2

    client = RetellClient(api_key=api_key)

    kbs = client.list_knowledge_bases()
    if not kbs:
        print("No knowledge bases found")
        return 0

    source_docs = collect_source_docs(kbs)
    if not source_docs:
        print("No document sources found in existing knowledge bases", file=sys.stderr)
        return 3

    unique_docs = dedupe_docs(source_docs)
    combined_markdown = build_combined_markdown(unique_docs)

    agents = client.list_agents()
    latest_agents = choose_latest_agents(agents)
    active_retell_llm_ids: list[str] = []
    for agent in latest_agents.values():
        resp_engine = agent.get("response_engine") or {}
        if resp_engine.get("type") == "retell-llm" and resp_engine.get("llm_id"):
            active_retell_llm_ids.append(resp_engine["llm_id"])
    active_retell_llm_ids = sorted(set(active_retell_llm_ids))

    pre_llm_state: dict[str, Any] = {}
    for llm_id in active_retell_llm_ids:
        obj = client.get_retell_llm(llm_id)
        pre_llm_state[llm_id] = {
            "knowledge_base_ids": obj.get("knowledge_base_ids"),
            "kb_config": obj.get("kb_config"),
            "model": obj.get("model"),
            "version": obj.get("version"),
        }

    report: dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "dry_run": not args.apply,
        "requested_name": args.name,
        "knowledge_base_count_before": len(kbs),
        "source_documents_total": len(source_docs),
        "source_documents_unique": len(unique_docs),
        "active_retell_llm_ids": active_retell_llm_ids,
        "pre_llm_state": pre_llm_state,
        "old_knowledge_bases": [
            {
                "knowledge_base_id": kb.get("knowledge_base_id"),
                "knowledge_base_name": kb.get("knowledge_base_name"),
                "status": kb.get("status"),
                "source_count": len(kb.get("knowledge_base_sources") or []),
            }
            for kb in kbs
        ],
        "unique_source_hashes": [
            {
                "sha256": d.sha256,
                "filename": d.filename,
                "kb_id": d.kb_id,
                "kb_name": d.kb_name,
            }
            for d in unique_docs
        ],
        "actions": [],
        "warnings": [],
    }

    if not args.apply:
        report["warnings"].append("Dry run only. Re-run with --apply to make changes.")
        report_path = write_report(report)
        print(json.dumps({
            "mode": "dry-run",
            "report": str(report_path),
            "knowledge_base_count_before": len(kbs),
            "source_documents_unique": len(unique_docs),
            "active_retell_llm_ids": active_retell_llm_ids,
        }, indent=2))
        return 0

    filename = f"retell_unified_kb_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.md"
    created = client.create_knowledge_base_from_markdown(args.name, filename=filename, content=combined_markdown)
    new_kb_id = created["knowledge_base_id"]
    report["new_knowledge_base"] = {
        "knowledge_base_id": new_kb_id,
        "knowledge_base_name": created.get("knowledge_base_name"),
        "filename": filename,
    }
    report["actions"].append({"create_knowledge_base": report["new_knowledge_base"]})

    completed = wait_for_kb_complete(client, new_kb_id, timeout_sec=args.timeout)
    report["new_knowledge_base"]["status"] = completed.get("status")
    report["new_knowledge_base"]["source_count"] = len(completed.get("knowledge_base_sources") or [])

    llm_updates: dict[str, Any] = {}
    for llm_id in active_retell_llm_ids:
        current = client.get_retell_llm(llm_id)
        payload = {
            "knowledge_base_ids": [new_kb_id],
            "kb_config": current.get("kb_config") or {"top_k": 8, "filter_score": 0.35},
        }
        updated = client.update_retell_llm(llm_id, payload)
        llm_updates[llm_id] = {
            "before_kb_ids": current.get("knowledge_base_ids"),
            "after_kb_ids": updated.get("knowledge_base_ids"),
            "kb_config": updated.get("kb_config"),
            "version": updated.get("version"),
        }
    report["llm_updates"] = llm_updates
    report["actions"].append({"update_retell_llms": llm_updates})

    deleted: list[str] = []
    delete_failures: dict[str, str] = {}
    if args.delete_old:
        for kb in kbs:
            kb_id = kb.get("knowledge_base_id")
            if not kb_id or kb_id == new_kb_id:
                continue
            try:
                client.delete_knowledge_base(kb_id)
                deleted.append(kb_id)
            except Exception as exc:  # noqa: BLE001
                delete_failures[kb_id] = str(exc)
    report["deleted_old_knowledge_bases"] = deleted
    report["delete_failures"] = delete_failures
    if args.delete_old:
        report["actions"].append({"delete_old_knowledge_bases": deleted})

    post_kbs = client.list_knowledge_bases()
    report["knowledge_base_count_after"] = len(post_kbs)
    report["remaining_knowledge_base_ids"] = [kb.get("knowledge_base_id") for kb in post_kbs]

    report_path = write_report(report)
    print(
        json.dumps(
            {
                "mode": "apply",
                "report": str(report_path),
                "new_knowledge_base_id": new_kb_id,
                "updated_llm_count": len(llm_updates),
                "deleted_old_kb_count": len(deleted),
                "delete_failures": delete_failures,
                "knowledge_base_count_after": len(post_kbs),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
