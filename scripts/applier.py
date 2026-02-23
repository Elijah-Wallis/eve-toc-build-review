#!/usr/bin/env python3
"""Automate native quick/easy job applications from a CSV lead list."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright


REDIRECT_PATTERNS = [
    re.compile(r"\bapply\s+on\s+company\s+site\b", re.I),
    re.compile(r"\bapply\s+at\s+company\s+site\b", re.I),
    re.compile(r"\bcontinue\s+with\s+company\s+site\b", re.I),
]

CREATE_ACCOUNT_PATTERNS = [
    re.compile(r"\bcreate\s+an?\s+account\b", re.I),
    re.compile(r"\bcreate\s+your\s+account\b", re.I),
    re.compile(r"\bsign\s+in\s+or\s+create\s+an\s+account\b", re.I),
    re.compile(r"\blog\s+in\s+to\s+continue\b", re.I),
    re.compile(r"\baccount\s+required\b", re.I),
]

APPLY_BUTTON_PATTERNS = [
    re.compile(r"\beasy\s+apply\b", re.I),
    re.compile(r"\bquick\s+apply\b", re.I),
]

SUBMIT_PATTERNS = [
    re.compile(r"\bsubmit\s+application\b", re.I),
    re.compile(r"\breview\b", re.I),
    re.compile(r"\bnext\b", re.I),
]

FIELD_RULES: List[tuple[str, List[re.Pattern[str]], str]] = [
    (
        "first_name",
        [
            re.compile(r"\bfirst\s+name\b", re.I),
            re.compile(r"\bgiven\s+name\b", re.I),
            re.compile(r"\bfname\b", re.I),
        ],
        "",
    ),
    (
        "last_name",
        [
            re.compile(r"\blast\s+name\b", re.I),
            re.compile(r"\bsurname\b", re.I),
            re.compile(r"\bfamily\s+name\b", re.I),
        ],
        "",
    ),
    (
        "full_name",
        [
            re.compile(r"\bfull\s+name\b", re.I),
            re.compile(r"\byour\s+name\b", re.I),
        ],
        "",
    ),
    (
        "email",
        [
            re.compile(r"\bemail\b", re.I),
            re.compile(r"\be\-mail\b", re.I),
            re.compile(r"\bwork\s+email\b", re.I),
        ],
        "",
    ),
    (
        "phone",
        [
            re.compile(r"\bphone\b", re.I),
            re.compile(r"\bmobile\b", re.I),
            re.compile(r"\bphone\s+number\b", re.I),
            re.compile(r"\bbest\s+contact\s+number\b", re.I),
        ],
        "",
    ),
    (
        "resume",
        [
            re.compile(r"\bresume\b", re.I),
            re.compile(r"\bcv\b", re.I),
            re.compile(r"\bcurriculum\s+vitae\b", re.I),
            re.compile(r"\battachment\b", re.I),
            re.compile(r"\bupload\b", re.I),
        ],
        "",
    ),
]


class Applier:
    def __init__(
        self,
        leads_path: Path,
        profile_path: Path,
        redirects_path: Path,
        headful: bool = False,
        timeout_ms: int = 60000,
    ) -> None:
        self.leads_path = leads_path
        self.profile_path = profile_path
        self.redirects_path = redirects_path
        self.headful = headful
        self.timeout_ms = timeout_ms

        self.profile = self.load_profile()
        self.leads = self.load_leads()

    def load_leads(self) -> pd.DataFrame:
        if not self.leads_path.exists():
            raise FileNotFoundError(f"Missing leads CSV: {self.leads_path}")

        df = pd.read_csv(self.leads_path, keep_default_na=False)
        df.columns = [str(c).strip() for c in df.columns]
        return df

    def load_profile(self) -> Dict[str, str]:
        if not self.profile_path.exists():
            raise FileNotFoundError(f"Missing profile JSON: {self.profile_path}")

        with self.profile_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)

        normalized = {}
        for key, value in raw.items():
            normalized_key = re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower())
            if isinstance(value, str):
                normalized[normalized_key] = value.strip()
            else:
                normalized[normalized_key] = value

        full_name = normalized.get("name") or normalized.get("full_name") or ""
        first_name = normalized.get("first_name") or normalized.get("firstname")
        last_name = normalized.get("last_name") or normalized.get("lastname")

        if not first_name and full_name:
            parts = [p for p in full_name.replace("\n", " ").split() if p]
            first_name = parts[0] if parts else ""
            if len(parts) > 1 and not last_name:
                last_name = parts[-1]

        resume_src = (
            normalized.get("resume")
            or normalized.get("resume_path")
            or normalized.get("cv")
            or "resume.pdf"
        )

        resume_path = Path(resume_src)
        if not resume_path.is_absolute():
            resume_path = (self.profile_path.parent / resume_path).resolve()

        return {
            "full_name": str(full_name),
            "first_name": str(first_name or ""),
            "last_name": str(last_name or ""),
            "email": str(normalized.get("email", "")),
            "phone": str(normalized.get("phone", "")),
            "resume_path": str(resume_path),
        }

    @staticmethod
    async def random_delay() -> None:
        await asyncio.sleep(random.uniform(2.0, 5.0))

    @staticmethod
    def now_ts() -> str:
        return datetime.now(timezone.utc).isoformat()

    def column_lookup(self, possible: Iterable[str]) -> str | None:
        lowered = {str(c).strip().lower(): c for c in self.leads.columns}
        for name in possible:
            key = name.strip().lower()
            if key in lowered:
                return lowered[key]
        return None

    def get_row_value(self, row: pd.Series, possible: Iterable[str]) -> str:
        col = self.column_lookup(possible)
        if not col:
            return ""
        val = row.get(col, "")
        if pd.isna(val):
            return ""
        return str(val).strip()

    def log_redirect(self, role: str, company: str, link: str, reason: str) -> None:
        file_exists = self.redirects_path.exists()
        self.redirects_path.parent.mkdir(parents=True, exist_ok=True)
        with self.redirects_path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["timestamp", "role", "company", "link", "reason"],
            )
            if not file_exists:
                writer.writeheader()
            writer.writerow(
                {
                    "timestamp": self.now_ts(),
                    "role": role,
                    "company": company,
                    "link": link,
                    "reason": reason,
                }
            )

    async def page_has_text(self, page, patterns: Iterable[re.Pattern[str]]) -> bool:
        try:
            text = await page.locator("body").inner_text()
        except Exception:
            return False

        lowered = text.lower() if isinstance(text, str) else ""
        for pattern in patterns:
            if pattern.search(lowered):
                return True
        return False

    async def first_visible(self, locator):
        for i in range(await locator.count()):
            candidate = locator.nth(i)
            try:
                if await candidate.is_visible():
                    return candidate
            except Exception:
                continue
        return None

    async def find_button(self, page, patterns: Iterable[re.Pattern[str]]):
        selector = "button, [role='button'], a, input[type='submit'], input[type='button']"
        for pattern in patterns:
            try:
                locator = page.locator(selector).filter(has_text=pattern)
            except Exception:
                continue

            button = await self.first_visible(locator)
            if button is not None:
                return button

        return None

    async def safe_click(self, locator) -> None:
        await locator.scroll_into_view_if_needed()
        await locator.click(timeout=self.timeout_ms)
        await self.random_delay()

    async def detect_form_field_key(self, descriptor: Dict[str, str]) -> Optional[str]:
        haystack = descriptor["haystack"]

        for key, patterns, _ in FIELD_RULES:
            for pattern in patterns:
                if pattern.search(haystack):
                    if key == "resume":
                        return "resume"
                    if key == "first_name" and self.profile["first_name"]:
                        return key
                    if key == "last_name" and self.profile["last_name"]:
                        return key
                    if key == "full_name" and self.profile["full_name"]:
                        return key
                    if key in ("email", "phone") and self.profile[key]:
                        return key

        return None

    async def apply_to_row(
        self,
        row: pd.Series,
        page,
    ) -> str:
        role = self.get_row_value(row, ["Role"])
        company = self.get_row_value(row, ["Company"])
        link = self.get_row_value(row, ["Link", "URL", "Job Link"])

        if not link:
            return "skip_no_link"

        await page.goto(link, wait_until="domcontentloaded", timeout=self.timeout_ms)
        await page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
        await self.random_delay()

        if await self.page_has_text(page, REDIRECT_PATTERNS):
            self.log_redirect(role, company, link, "Apply on Company Site")
            return "skip_redirect"

        if await self.page_has_text(page, CREATE_ACCOUNT_PATTERNS):
            self.log_redirect(role, company, link, "Create Account Wall")
            return "skip_create_account"

        apply_button = await self.find_button(page, APPLY_BUTTON_PATTERNS)
        if apply_button is None:
            return "skip_no_native_apply"

        await self.safe_click(apply_button)

        if await self.page_has_text(page, CREATE_ACCOUNT_PATTERNS):
            self.log_redirect(role, company, link, "Create Account Wall")
            return "skip_create_account_after_click"

        await self.smart_fill(page)

        submit_btn = await self.find_button(page, SUBMIT_PATTERNS)
        if submit_btn is not None:
            # Keep this conservative: only attempt to continue one step.
            # Avoid hard-submit unless you add '--auto-submit'.
            pass

        return "attempted"

    async def smart_fill(self, page) -> None:
        js = """
        (el) => {
            if (!el || !el.isConnected) {
                return null;
            }

            const labelFor = (node) => {
                const id = node.getAttribute('id');
                if (!id) {
                    return '';
                }
                const byFor = document.querySelector(`label[for="${CSS.escape(id)}"]`);
                return byFor ? (byFor.innerText || '') : '';
            };

            const textParts = [];
            textParts.push((el.getAttribute('aria-label') || '').toLowerCase());
            textParts.push((el.getAttribute('name') || '').toLowerCase());
            textParts.push((el.getAttribute('placeholder') || '').toLowerCase());
            textParts.push((el.getAttribute('title') || '').toLowerCase());
            textParts.push((el.getAttribute('id') || '').toLowerCase());
            textParts.push((el.getAttribute('autocomplete') || '').toLowerCase());
            textParts.push(labelFor(el).toLowerCase());

            const parentLabel = el.closest('label');
            if (parentLabel) {
                textParts.push((parentLabel.innerText || '').toLowerCase());
            }

            return {
                id: (el.getAttribute('id') || '').toLowerCase(),
                tag: (el.tagName || '').toLowerCase(),
                type: (el.getAttribute('type') || '').toLowerCase(),
                name: (el.getAttribute('name') || '').toLowerCase(),
                placeholder: (el.getAttribute('placeholder') || '').toLowerCase(),
                haystack: textParts.join(' ').replace(/\s+/g, ' ').trim(),
            };
        }
        """

        controls = page.locator("input, textarea, select")
        control_count = await controls.count()
        for idx in range(control_count):
            control = controls.nth(idx)
            try:
                if not await control.is_visible():
                    continue
                if not await control.is_enabled():
                    continue

                raw = await control.element_handle()
                if raw is None:
                    continue

                descriptor = await page.evaluate(js, raw)
                if descriptor is None:
                    continue

                key = await self.detect_field_key(descriptor)
                if not key:
                    continue

                field_type = descriptor["type"]
                field_tag = descriptor["tag"]

                if field_type == "file" or key == "resume":
                    resume_path = Path(self.profile["resume_path"])
                    if resume_path.exists():
                        try:
                            await control.set_input_files(str(resume_path))
                            await self.random_delay()
                        except Exception:
                            pass
                    continue

                if field_tag in {"input", "textarea"}:
                    value = self.profile.get(key, "")
                    if value:
                        try:
                            await control.fill(value)
                            await self.random_delay()
                        except Exception:
                            try:
                                await control.evaluate("(el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); }", value)
                            except Exception:
                                pass
                    continue

                if field_tag == "select":
                    value = self.profile.get(key, "")
                    if not value:
                        continue
                    try:
                        await control.select_option(label=value)
                        await self.random_delay()
                    except Exception:
                        try:
                            await control.select_option(value=value)
                            await self.random_delay()
                        except Exception:
                            pass
            except Exception:
                continue

    async def run(self) -> None:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=not self.headful)
            page = await browser.new_page()
            page.set_default_timeout(self.timeout_ms)

            results = {
                "attempted": 0,
                "skip_redirect": 0,
                "skip_create_account": 0,
                "skip_create_account_after_click": 0,
                "skip_no_native_apply": 0,
                "skip_no_link": 0,
                "errors": 0,
            }

            print(f"Total rows: {len(self.leads)}")

            for _, row in self.leads.iterrows():
                role = self.get_row_value(row, ["Role"])
                company = self.get_row_value(row, ["Company"])
                link = self.get_row_value(row, ["Link", "URL", "Job Link"])
                lead_id = f"{company or 'Unknown'} | {role or 'Unknown'}"

                try:
                    status = await self.apply_to_row(row, page)
                    if status not in results:
                        results[status] = 0
                    results[status] += 1
                    print(f"[{status}] {lead_id}")
                except PlaywrightTimeoutError as e:
                    results["errors"] += 1
                    print(f"[timeout_error] {lead_id}: {e}")
                except Exception as e:
                    results["errors"] += 1
                    print(f"[error] {lead_id}: {e}")

                await self.random_delay()

            await browser.close()

            print("Done.")
            for key, value in sorted(results.items()):
                print(f"  {key}: {value}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply jobs via native quick/easy apply flows.")
    parser.add_argument(
        "--leads",
        dest="leads_path",
        default=str(Path("data/leads.csv")),
        help="Path to leads CSV (default: data/leads.csv)",
    )
    parser.add_argument(
        "--profile",
        dest="profile_path",
        default=str(Path("data/profile.json")),
        help="Path to profile JSON (default: data/profile.json)",
    )
    parser.add_argument(
        "--redirects",
        dest="redirects_path",
        default=str(Path("data/redirects.csv")),
        help="Path to redirects log CSV (default: data/redirects.csv)",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="Run browser in headful mode for debugging",
    )
    parser.add_argument(
        "--timeout-ms",
        dest="timeout_ms",
        type=int,
        default=60000,
        help="Timeout in milliseconds for Playwright waits",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    applier = Applier(
        leads_path=Path(args.leads_path),
        profile_path=Path(args.profile_path),
        redirects_path=Path(args.redirects_path),
        headful=args.headful,
        timeout_ms=args.timeout_ms,
    )

    asyncio.run(applier.run())


if __name__ == "__main__":
    main()
