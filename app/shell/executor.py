from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .policy import parse_allowed_commands, validate_command


@dataclass(frozen=True, slots=True)
class ShellResult:
    ok: bool
    runtime: str
    command: str
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int
    reason: str
    output_sha256: str


class ShellExecutor:
    def __init__(
        self,
        *,
        mode: str = "local",
        enable_hosted: bool = False,
        allowed_commands: str = "",
        workdir: str | None = None,
        log_path: str = "docs/self_improve/history/shell_exec.jsonl",
    ) -> None:
        m = str(mode or "local").strip().lower()
        self.mode = m if m in {"local", "hosted", "hybrid"} else "local"
        self.enable_hosted = bool(enable_hosted)
        self.allowed_commands = parse_allowed_commands(allowed_commands)
        self.workdir = workdir or os.getcwd()
        self.log_path = Path(log_path)

    def _sha(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()

    def _record(self, result: ShellResult) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": int(time.time()),
            "ok": result.ok,
            "runtime": result.runtime,
            "command": result.command,
            "returncode": result.returncode,
            "duration_ms": result.duration_ms,
            "reason": result.reason,
            "output_sha256": result.output_sha256,
        }
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")

    async def execute(self, command: str, *, timeout_s: int = 20, prefer_hosted: bool = False) -> ShellResult:
        decision = validate_command(command, allowed_commands=self.allowed_commands)
        if not decision.allowed:
            res = ShellResult(
                ok=False,
                runtime="policy",
                command=command,
                returncode=126,
                stdout="",
                stderr="",
                duration_ms=0,
                reason=decision.reason,
                output_sha256=self._sha(""),
            )
            self._record(res)
            return res

        runtime = "local"
        if self.mode == "hosted":
            runtime = "hosted"
        elif self.mode == "hybrid":
            runtime = "hosted" if prefer_hosted else "local"

        if runtime == "hosted":
            if not self.enable_hosted:
                res = ShellResult(
                    ok=False,
                    runtime="hosted",
                    command=command,
                    returncode=125,
                    stdout="",
                    stderr="",
                    duration_ms=0,
                    reason="hosted_disabled",
                    output_sha256=self._sha(""),
                )
                self._record(res)
                return res
            # Hosted integration stub: safe by default; can be replaced with real remote runner.
            res = ShellResult(
                ok=False,
                runtime="hosted",
                command=command,
                returncode=125,
                stdout="",
                stderr="",
                duration_ms=0,
                reason="hosted_not_configured",
                output_sha256=self._sha(""),
            )
            self._record(res)
            return res

        t0 = time.monotonic()

        def _run() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["bash", "-lc", command],
                cwd=self.workdir,
                text=True,
                capture_output=True,
                timeout=max(1, int(timeout_s)),
                check=False,
            )

        try:
            cp = await asyncio.to_thread(_run)
            out = cp.stdout or ""
            err = cp.stderr or ""
            dur = int((time.monotonic() - t0) * 1000)
            res = ShellResult(
                ok=(cp.returncode == 0),
                runtime="local",
                command=command,
                returncode=int(cp.returncode),
                stdout=out,
                stderr=err,
                duration_ms=dur,
                reason="ok" if cp.returncode == 0 else "nonzero_exit",
                output_sha256=self._sha(out + "\n" + err),
            )
            self._record(res)
            return res
        except subprocess.TimeoutExpired:
            dur = int((time.monotonic() - t0) * 1000)
            res = ShellResult(
                ok=False,
                runtime="local",
                command=command,
                returncode=124,
                stdout="",
                stderr="",
                duration_ms=dur,
                reason="timeout",
                output_sha256=self._sha(""),
            )
            self._record(res)
            return res
