from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


REQUIRED_SECTIONS = ("opener", "diagnosis", "hook", "objections", "closing")
SECTION_ALIASES = {
    "diagnosis": ("discovery",),
    "hook": ("pain_admitted", "pain_denied", "send_package_prompt"),
    "objections": (
        "objection_answering_service",
        "objection_info_email",
        "objection_sales",
    ),
    "closing": ("done",),
}
REQUIRED_PLACEHOLDERS = {
    "business_name",
    "city",
    "clinic_name",
    "test_timestamp",
    "evidence_type",
    "emr_system",
    "contact_number",
}
REQUIRED_TOOLS = {"send_evidence_package", "mark_dnc_compliant"}


@dataclass(frozen=True, slots=True)
class EVEV7PromptBundle:
    path: str
    rendered_script: str
    sections: dict[str, str]


def _read_file(script_path: str) -> str:
    p = Path(script_path)
    if not p.exists():
        raise FileNotFoundError(f"EVE v7 script not found: {script_path}")
    return p.read_text(encoding="utf-8")


def _state_exists(script_text: str, state: str) -> bool:
    return re.search(rf"(?m)^[ \t]*{re.escape(state)}:\s*$", script_text) is not None


def _resolve_state_name(script_text: str, canonical: str) -> str:
    candidates = (canonical, *SECTION_ALIASES.get(canonical, ()))
    for candidate in candidates:
        if _state_exists(script_text, candidate):
            return candidate
    raise ValueError(f"Missing required flow section: {canonical} (checked aliases: {', '.join(candidates)})")


def _validate_structure(script_text: str) -> None:
    missing = []
    for section in REQUIRED_SECTIONS:
        try:
            _resolve_state_name(script_text, section)
        except ValueError:
            missing.append(section)
    if missing:
        raise ValueError(f"Missing required flow sections: {', '.join(missing)}")

    missing_placeholders = [p for p in REQUIRED_PLACEHOLDERS if f"{{{{{p}}}}}" not in script_text]
    if missing_placeholders:
        raise ValueError(f"Missing required placeholders: {', '.join(missing_placeholders)}")

    # Canonical tool names.
    for tool in REQUIRED_TOOLS:
        if f"name: {tool}" not in script_text:
            raise ValueError(f"Missing required tool contract definition: {tool}")
    if "name: mark_dnc" in script_text:
        # Legacy fallback must be normalized at orchestration layer.
        pass


def _render_placeholders(script_text: str, placeholders: Mapping[str, str]) -> str:
    rendered = script_text
    for key, value in placeholders.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", str(value))
    return rendered


def _extract_state_block(script_text: str, state: str) -> str:
    # Capture indented body until next top-level key.
    pattern = rf"(?ms)^[ \\t]*{re.escape(state)}:\\n(?P<body>(?:^[ \\t]+.*\\n?)*)"
    match = re.search(pattern, script_text)
    if not match:
        return ""
    body = match.group("body") or ""

    # Prefer literal spoken blocks first.
    say_match = re.search(r"(?ms)^\\s*say:\\s*\\|\\n(?P<say>.*?)(?:\\n^\\s*\\w|\\Z)", body)
    if say_match:
        return textwrap.dedent(say_match.group("say")).strip("\n")

    ask_match = re.search(r"(?ms)^\\s*ask:\\s*\\|\\n(?P<ask>.*?)(?:\\n^\\s*\\w|\\Z)", body)
    if ask_match:
        return textwrap.dedent(ask_match.group("ask")).strip("\n")

    return textwrap.dedent(body).strip("\n")


def _build_section_payload(sections: dict[str, str]) -> str:
    rendered = []
    for name in REQUIRED_SECTIONS:
        rendered.append(f"{name}:\\n{textwrap.indent(sections[name], '  ')}")
    return "\\n\\n".join(rendered)


def load_eve_v7_prompt_bundle(
    *,
    script_path: str,
    placeholders: Mapping[str, str] | None = None,
) -> EVEV7PromptBundle:
    raw = _read_file(script_path)
    _validate_structure(raw)

    rendered = _render_placeholders(raw, placeholders or {})
    sections: dict[str, str] = {}
    for canonical in REQUIRED_SECTIONS:
        resolved = _resolve_state_name(rendered, canonical)
        sections[canonical] = _extract_state_block(rendered, resolved)
        if not sections[canonical].strip():
            raise ValueError(f"Flow section '{canonical}' is empty after parse/render in {script_path}")

    prompt = (
        "You are Cassidy, the MedSpa EVE v7 outbound voice workflow orchestrator.\\n"
        "Run the script exactly as authored with strict interruption control and no out-of-flow improvisation.\\n\\n"
        "SYSTEM FLOW:\\n"
        f"{_build_section_payload(sections)}\\n\\n"
        "Tool contracts:\\n"
        "  - send_evidence_package\\n"
        "  - mark_dnc_compliant\\n"
        "Never emit or request tool name `mark_dnc`; rewrite that branch to `mark_dnc_compliant` "
        "(reasons: USER_REQUEST, WRONG_NUMBER, HOSTILE).\\n"
        "Keep script variables intact and only fill observed placeholders."
    )

    return EVEV7PromptBundle(
        path=str(script_path),
        rendered_script=prompt,
        sections=sections,
    )


def load_eve_v7_system_prompt(
    *,
    script_path: str,
    placeholders: Mapping[str, str] | None = None,
) -> str:
    return load_eve_v7_prompt_bundle(
        script_path=script_path,
        placeholders=placeholders or {},
    ).rendered_script


def load_eve_v7_opener(
    *,
    script_path: str,
    placeholders: Mapping[str, str] | None = None,
) -> str:
    sections = load_eve_v7_prompt_bundle(
        script_path=script_path,
        placeholders=placeholders or {},
    ).sections
    return sections.get("opener", "").strip()
