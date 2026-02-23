from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCAN_TARGETS = [
    ROOT / "README.md",
    ROOT / "docs",
    ROOT / "scripts",
    ROOT / "tests",
]
FORBIDDEN_PATTERNS = [
    "/" + "Users" + "/",
    "/" + "home" + "/",
    "/" + "Volumes" + "/",
    "C:" + "\\" + "Users" + "\\",
]
SKIP_DIRS = {"__pycache__", ".pytest_cache"}
SCAN_SUFFIXES = {".md", ".py", ".json", ".toml", ".sh"}


def _iter_scan_files() -> list[Path]:
    files: list[Path] = []
    for target in SCAN_TARGETS:
        if not target.exists():
            continue
        if target.is_file():
            files.append(target)
            continue
        for p in target.rglob("*"):
            if not p.is_file():
                continue
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            if p.suffix not in SCAN_SUFFIXES:
                continue
            files.append(p)
    return files


def test_no_machine_specific_absolute_paths_in_docs_scripts_and_tests() -> None:
    offenders: list[str] = []
    for p in _iter_scan_files():
        text = p.read_text(encoding="utf-8", errors="ignore")
        for pat in FORBIDDEN_PATTERNS:
            if pat in text:
                offenders.append(f"{p.relative_to(ROOT)} :: {pat}")
    assert not offenders, "forbidden absolute path patterns found:\n" + "\n".join(offenders)


def test_path_hygiene_scans_fixtures() -> None:
    files = _iter_scan_files()
    assert any("tests" in p.parts and "fixtures" in p.parts for p in files)
