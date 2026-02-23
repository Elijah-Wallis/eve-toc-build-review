from __future__ import annotations

import asyncio
import subprocess
import os
import json
import time
import signal
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .clock import RealClock
from .config import BrainConfig
from .dashboard_data import build_dashboard_summary, build_outbound_pipeline_status, build_repo_map, load_call_detail
from .metrics import CompositeMetrics, Metrics
from .orchestrator import Orchestrator
from .provider import build_llm_client
from .prom_export import GLOBAL_PROM
from .security import is_ip_allowed, resolve_client_ip, verify_query_token, verify_shared_secret
from .shell.executor import ShellExecutor
from .trace import TraceSink
from .transport_ws import GateRef, Transport, socket_reader, socket_writer
from .bounded_queue import BoundedDequeQueue
from .tools import ToolRegistry


class StarletteTransport(Transport):
    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws

    async def recv_text(self) -> str:
        return await self._ws.receive_text()

    async def send_text(self, text: str) -> None:
        await self._ws.send_text(text)

    async def close(self, *, code: int = 1000, reason: str = "") -> None:
        try:
            await self._ws.close(code=code, reason=reason)
        except Exception:
            return


app = FastAPI()
_REPO_ROOT = Path(__file__).resolve().parents[1]
_DASHBOARD_DIR = _REPO_ROOT / "dashboard"
if _DASHBOARD_DIR.exists():
    app.mount("/dashboard", StaticFiles(directory=str(_DASHBOARD_DIR), html=True), name="dashboard")
_LIVE_CONTROL_FILE = _REPO_ROOT / "data" / "leads" / ".live_dispatch_controls.json"
_OUTBOUND_START_STATE_FILE = _REPO_ROOT / "data" / "leads" / ".live_outbound_start_state.json"
_OUTBOUND_START_SCRIPT = _REPO_ROOT / "start_outbound_dialing"
_OUTBOUND_START_LOG = _REPO_ROOT / "logs" / "start_outbound_dialing.log"


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    value_str = str(value).strip().lower()
    if value_str in {"1", "true", "yes", "y", "on"}:
        return True
    if value_str in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _coerce_int(value: Any, default: int, min_value: int | None = None, max_value: int | None = None) -> int:
    try:
        coerced = int(value)
    except Exception:
        return default
    if min_value is not None and coerced < min_value:
        return min_value
    if max_value is not None and coerced > max_value:
        return max_value
    return coerced


def _normalize_phone_lookup(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _load_call_artifact(
    calls_dir: Path,
    *,
    call_id: str | None = None,
    clinic_id: str | None = None,
    to_number: str | None = None,
) -> tuple[dict[str, Any], str] | None:
    target_call_id = str(call_id or "").strip()
    target_clinic_id = str(clinic_id or "").strip()
    target_to = _normalize_phone_lookup(to_number)

    if not target_call_id and not target_clinic_id and not target_to:
        return None

    for p in sorted(calls_dir.glob("*/call.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue

        payload_call_id = str(rec.get("call_id") or "").strip()
        metadata = rec.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        payload_clinic_id = str(metadata.get("clinic_id") or rec.get("clinic_id") or rec.get("practice_id") or "").strip()
        payload_to = _normalize_phone_lookup(
            rec.get("to_number")
            or rec.get("to")
            or metadata.get("to_number")
            or ""
        )

        if target_call_id and target_call_id == payload_call_id:
            return rec, str(p)
        if target_clinic_id and target_clinic_id == payload_clinic_id:
            return rec, str(p)
        if target_to and target_to == payload_to:
            return rec, str(p)

    return None


def _control_file_path() -> Path:
    override = os.getenv("LIVE_DISPATCH_CONTROL_FILE", "").strip()
    if override:
        candidate = Path(override)
        if not candidate.is_absolute():
            return _REPO_ROOT / candidate
        return candidate
    return _LIVE_CONTROL_FILE


def _read_live_controls() -> dict[str, Any]:
    defaults = {
        "max_calls": _coerce_int(os.getenv("LIVE_MAX_CALLS", "5"), 5, min_value=0, max_value=2000),
        "concurrency": _coerce_int(os.getenv("LIVE_CONCURRENCY", "5"), 5, min_value=1, max_value=100),
        "stop_requested": False,
        "source": "env",
        "updated_utc": 0,
    }
    path = _control_file_path()
    if not path.exists():
        return defaults
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return defaults
    if not isinstance(raw, dict):
        return defaults
    return {
        **defaults,
        "max_calls": _coerce_int(raw.get("max_calls"), defaults["max_calls"], min_value=0, max_value=2000),
        "concurrency": _coerce_int(raw.get("concurrency"), defaults["concurrency"], min_value=1, max_value=100),
        "stop_requested": _coerce_bool(raw.get("stop_requested"), _coerce_bool(raw.get("stop"), False)),
        "source": str(raw.get("source", "dashboard")),
        "updated_utc": _coerce_int(raw.get("updated_utc"), 0, min_value=0),
    }


def _write_live_controls(controls: dict[str, Any]) -> dict[str, Any]:
    path = _control_file_path()
    payload = {
        "max_calls": _coerce_int(controls.get("max_calls"), 0, min_value=0, max_value=2000),
        "concurrency": _coerce_int(controls.get("concurrency"), 20, min_value=1, max_value=100),
        "stop_requested": _coerce_bool(controls.get("stop_requested"), False),
        "source": str(controls.get("source", "dashboard")),
        "updated_utc": int(controls.get("updated_utc", 0)),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_utc"] = int(time.time())
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def _is_process_running(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _load_outbound_start_state() -> dict[str, Any]:
    if not _OUTBOUND_START_STATE_FILE.exists():
        return {}
    try:
        raw = json.loads(_OUTBOUND_START_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(raw, dict):
        return raw
    return {}


def _launch_state_view() -> dict[str, Any]:
    state = _load_outbound_start_state()
    pid = int(state.get("pid", 0) or 0)
    is_running = _is_process_running(pid)
    status = str(state.get("status", "unknown")).strip().lower() or "unknown"
    if pid:
        if is_running and status not in {"running", "starting", "started"}:
            status = "running"
        if not is_running and status == "running":
            status = "not_running"
    elif status not in {"stopped", "not_running", "unknown"}:
        status = "unknown"

    return {
        "status": status,
        "pid": pid if pid else None,
        "running": bool(is_running and pid),
        "log_path": str(_OUTBOUND_START_LOG),
        "state_file": str(_OUTBOUND_START_STATE_FILE),
        "state": state,
    }


def _save_outbound_start_state(state: dict[str, Any]) -> None:
    _OUTBOUND_START_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _OUTBOUND_START_STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _required_outbound_env_missing() -> list[str]:
    required = ("RETELL_API_KEY", "RETELL_FROM_NUMBER", "B2B_AGENT_ID")
    return [key for key in required if not str(os.getenv(key, "")).strip()]


def _start_outbound_session() -> tuple[bool, dict[str, Any], str]:
    missing = _required_outbound_env_missing()
    if missing:
        return (
            False,
            {},
            f"Missing required environment variables: {', '.join(missing)}",
        )

    prior_state = _load_outbound_start_state()
    prior_pid = int(prior_state.get("pid", 0) or 0)
    if prior_pid and _is_process_running(prior_pid):
        return (
            True,
            {
                "status": "already_running",
                "pid": prior_pid,
                "log_path": str(_OUTBOUND_START_LOG),
                "state_file": str(_OUTBOUND_START_STATE_FILE),
                "message": "Outbound dialing already active.",
            },
            "",
        )

    if not _OUTBOUND_START_SCRIPT.exists():
        return False, {}, f"Missing launcher script: {_OUTBOUND_START_SCRIPT}"

    _OUTBOUND_START_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _OUTBOUND_START_LOG.open("a", encoding="utf-8") as log_fh:
        proc = subprocess.Popen(
            ["bash", str(_OUTBOUND_START_SCRIPT)],
            cwd=str(_REPO_ROOT),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    state = {
        "pid": proc.pid,
        "log_path": str(_OUTBOUND_START_LOG),
        "state_file": str(_OUTBOUND_START_STATE_FILE),
        "started_utc": int(time.time()),
        "status": "running",
    }
    _save_outbound_start_state(state)
    return (
        True,
        {
            "status": "started",
            "pid": proc.pid,
            "log_path": str(_OUTBOUND_START_LOG),
            "state_file": str(_OUTBOUND_START_STATE_FILE),
            "message": "Outbound dialing launcher started in background.",
        },
        "",
    )


def _stop_outbound_session() -> tuple[bool, dict[str, Any], str]:
    prior_state = _load_outbound_start_state()
    prior_pid = int(prior_state.get("pid", 0) or 0)
    if not prior_pid:
        return False, {}, "No outbound launcher session found."

    if not _is_process_running(prior_pid):
        _save_outbound_start_state(
            {
                **prior_state,
                "status": "not_running",
                "checked_utc": int(time.time()),
            }
        )
        return (
            True,
            {
                "status": "not_running",
                "pid": prior_pid,
                "message": f"Process {prior_pid} is no longer running.",
            },
            "",
        )

    stopped = False
    try:
        try:
            os.killpg(prior_pid, signal.SIGTERM)
            stopped = True
        except Exception:
            os.kill(prior_pid, signal.SIGTERM)
            stopped = True
    except Exception as e:
        return (
            False,
            {"pid": prior_pid},
            f"Failed to stop PID {prior_pid}: {e}",
        )

    if stopped:
        _save_outbound_start_state(
            {
                **prior_state,
                "status": "stopped",
                "stopped_utc": int(time.time()),
            }
        )
        return (
            True,
            {
                "status": "stopped",
                "pid": prior_pid,
                "message": "Outbound launcher stopped.",
            },
            "",
        )

    return False, {"pid": prior_pid}, f"Could not stop PID {prior_pid}."


@app.get("/healthz")
async def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/metrics")
async def metrics() -> PlainTextResponse:
    return PlainTextResponse(GLOBAL_PROM.render())


@app.get("/api/dashboard/summary")
async def dashboard_summary() -> JSONResponse:
    payload = build_dashboard_summary(GLOBAL_PROM.render())
    return JSONResponse(payload)


@app.get("/api/dashboard/repo-map")
async def dashboard_repo_map() -> JSONResponse:
    return JSONResponse(build_repo_map(_REPO_ROOT))


@app.get("/api/dashboard/sop")
async def dashboard_sop() -> JSONResponse:
    path = _REPO_ROOT / "docs" / "self_improve_sop.md"
    text = ""
    ok = False
    if path.exists():
        ok = True
        text = path.read_text(encoding="utf-8")
    return JSONResponse(
        {
            "ok": ok,
            "path": "docs/self_improve_sop.md",
            "markdown": text,
        }
    )


@app.get("/api/dashboard/outbound")
async def dashboard_outbound_pipeline() -> JSONResponse:
    payload = build_outbound_pipeline_status(
        _REPO_ROOT,
        campaign_id=os.getenv("LIVE_CAMPAIGN_ID", None),
        tenant=os.getenv("LIVE_TENANT", None),
    )
    return JSONResponse(payload)


@app.get("/api/dashboard/call-detail")
async def dashboard_call_detail(
    call_id: str | None = None,
    clinic_id: str | None = None,
    to_number: str | None = None,
) -> JSONResponse:
    calls_dir = _REPO_ROOT / "data" / "retell_calls"
    if not call_id and not clinic_id and not to_number:
        return JSONResponse(
            {
                "ok": False,
                "error": "one of call_id, clinic_id, or to_number is required",
            },
            status_code=400,
        )

    call = load_call_detail(calls_dir, call_id=call_id, clinic_id=clinic_id, to_number=to_number)
    if call is None:
        return JSONResponse(
            {
                "ok": False,
                "error": "call artifact not found",
            },
            status_code=404,
        )
    return JSONResponse({"ok": True, "call": call})


@app.get("/api/dashboard/call-artifact")
async def dashboard_call_artifact(
    call_id: str | None = None,
    clinic_id: str | None = None,
    to_number: str | None = None,
) -> JSONResponse:
    calls_dir = _REPO_ROOT / "data" / "retell_calls"
    if not call_id and not clinic_id and not to_number:
        return JSONResponse(
            {
                "ok": False,
                "error": "one of call_id, clinic_id, or to_number is required",
            },
            status_code=400,
        )

    artifact = _load_call_artifact(
        calls_dir,
        call_id=call_id,
        clinic_id=clinic_id,
        to_number=to_number,
    )
    if artifact is None:
        return JSONResponse(
            {
                "ok": False,
                "error": "call artifact not found",
            },
            status_code=404,
        )
    call_raw, _ = artifact
    return JSONResponse({"ok": True, "artifact": call_raw})


@app.get("/api/dashboard/readme")
async def dashboard_readme() -> JSONResponse:
    path = _REPO_ROOT / "README.md"
    text = ""
    ok = False
    if path.exists():
        ok = True
        text = path.read_text(encoding="utf-8")
    return JSONResponse(
        {
            "ok": ok,
            "path": "README.md",
            "markdown": text,
        }
    )


@app.get("/api/dashboard/outbound-controls")
async def dashboard_outbound_controls() -> JSONResponse:
    return JSONResponse({"ok": True, "controls": _read_live_controls()})


@app.post("/api/dashboard/outbound-controls")
async def dashboard_update_outbound_controls(request: Request) -> JSONResponse:
    try:
        payload_raw = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json body"}, status_code=400)
    if not isinstance(payload_raw, dict):
        return JSONResponse({"ok": False, "error": "expected object body"}, status_code=400)

    current = _read_live_controls()
    if "max_calls" in payload_raw:
        current["max_calls"] = _coerce_int(payload_raw.get("max_calls"), current["max_calls"], min_value=0, max_value=2000)
    if "concurrency" in payload_raw:
        current["concurrency"] = _coerce_int(payload_raw.get("concurrency"), current["concurrency"], min_value=1, max_value=100)
    if "stop" in payload_raw:
        current["stop_requested"] = _coerce_bool(payload_raw.get("stop"), False)
    if "stop_requested" in payload_raw:
        current["stop_requested"] = _coerce_bool(payload_raw.get("stop_requested"), False)
    if "resume" in payload_raw and _coerce_bool(payload_raw.get("resume"), False):
        current["stop_requested"] = False

    controls = _write_live_controls(current)
    return JSONResponse({"ok": True, "controls": controls})


@app.post("/api/dashboard/start-outbound")
async def dashboard_start_outbound() -> JSONResponse:
    ok, payload, err = _start_outbound_session()
    if not ok:
        return JSONResponse({"ok": False, "error": err}, status_code=500)
    return JSONResponse({"ok": True, "start": payload})


@app.post("/api/dashboard/stop-outbound")
async def dashboard_stop_outbound() -> JSONResponse:
    ok, payload, err = _stop_outbound_session()
    if not ok:
        return JSONResponse({"ok": False, "error": err}, status_code=400)
    return JSONResponse({"ok": True, "stop": payload})


@app.get("/api/dashboard/outbound-launch-state")
async def dashboard_outbound_launch_state() -> JSONResponse:
    return JSONResponse({"ok": True, "state": _launch_state_view()})

@app.websocket("/llm-websocket/{call_id}")
async def llm_websocket(ws: WebSocket, call_id: str) -> None:
    await _run_session(ws, call_id, route_name="llm-websocket")


def _normalize_route(route: str) -> str:
    return str(route or "").strip().strip("/").strip()


def _log_ws_event(cfg: "BrainConfig", *, route_name: str, call_id: str, event: str, **payload: object) -> None:
    if not cfg.ws_structured_logging:
        return
    base = {
        "component": "ws_session",
        "event": event,
        "route": f"/{_normalize_route(route_name)}",
        "call_id": call_id,
    }
    base.update(payload)
    print(json.dumps(base, sort_keys=True, separators=(",", ":")))


async def _run_session(ws: WebSocket, call_id: str, route_name: str) -> None:
    cfg = BrainConfig.from_env()
    route = _normalize_route(route_name)
    # Canonical contract: production route is fixed and must not drift.
    canonical_route = "llm-websocket"
    if route != canonical_route:
        await ws.accept()
        _log_ws_event(
            cfg,
            route_name=route,
            call_id=call_id,
            event="reject_noncanonical_route",
            canonical_route=f"/{canonical_route}",
        )
        await ws.close(code=1008, reason="non_canonical_route")
        return
    _log_ws_event(
        cfg,
        route_name=route,
        call_id=call_id,
        event="connect",
        canonical_route=f"/{canonical_route}",
        ip="",
    )
    # Optional WS handshake hardening. In production, prefer enforcing at the reverse proxy.
    headers = {str(k): str(v) for k, v in ws.headers.items()}
    remote_ip = ""
    try:
        if ws.client is not None:
            remote_ip = ws.client.host or ""
    except Exception:
        remote_ip = ""

    effective_ip = resolve_client_ip(
        remote_ip=remote_ip,
        headers=headers,
        trusted_proxy_enabled=cfg.ws_trusted_proxy_enabled,
        trusted_proxy_cidrs=cfg.ws_trusted_proxy_cidrs,
    )

    if cfg.ws_allowlist_enabled and not is_ip_allowed(
        remote_ip=effective_ip, cidrs=cfg.ws_allowlist_cidrs
    ):
        await ws.accept()
        await ws.close(code=1008, reason="forbidden")
        return

    if cfg.ws_shared_secret_enabled and not verify_shared_secret(
        headers=headers,
        header=cfg.ws_shared_secret_header,
        secret=cfg.ws_shared_secret,
    ):
        await ws.accept()
        await ws.close(code=1008, reason="forbidden")
        return

    if not verify_query_token(
        query_params=dict(ws.query_params),
        token_param=cfg.ws_query_token_param,
        expected_token=cfg.ws_query_token,
    ):
        await ws.accept()
        await ws.close(code=1008, reason="forbidden")
        return

    await ws.accept()
    clock = RealClock()
    session_metrics = Metrics()
    metrics = CompositeMetrics(session_metrics, GLOBAL_PROM)
    trace = TraceSink()

    inbound_q: BoundedDequeQueue = BoundedDequeQueue(maxsize=cfg.inbound_queue_max)
    outbound_q: BoundedDequeQueue = BoundedDequeQueue(maxsize=cfg.outbound_queue_max)
    shutdown_evt = asyncio.Event()
    gate = GateRef(epoch=0, speak_gen=0)
    shell_executor = ShellExecutor(
        mode=cfg.shell_mode,
        enable_hosted=cfg.shell_enable_hosted,
        allowed_commands=cfg.shell_allowed_commands,
        workdir=os.getcwd(),
    )
    tools = ToolRegistry(
        session_id=call_id,
        clock=clock,
        metrics=metrics,
        shell_executor=shell_executor,
        shell_tool_enabled=cfg.shell_tool_enabled,
        shell_tool_canary_enabled=cfg.shell_tool_canary_enabled,
        shell_tool_canary_percent=cfg.shell_tool_canary_percent,
    )
    llm = build_llm_client(cfg, session_id=call_id)

    transport = StarletteTransport(ws)
    orch = Orchestrator(
        session_id=call_id,
        call_id=call_id,
        config=cfg,
        clock=clock,
        metrics=metrics,
        trace=trace,
        inbound_q=inbound_q,
        outbound_q=outbound_q,
        shutdown_evt=shutdown_evt,
        gate=gate,
        tools=tools,
        llm=llm,
    )

    reader_task = asyncio.create_task(
        socket_reader(
            transport=transport,
            inbound_q=inbound_q,
            metrics=metrics,
            shutdown_evt=shutdown_evt,
            max_frame_bytes=cfg.ws_max_frame_bytes,
            structured_logs=cfg.ws_structured_logging,
            call_id=call_id,
        )
    )
    writer_task = asyncio.create_task(
        socket_writer(
            transport=transport,
            outbound_q=outbound_q,
            metrics=metrics,
            shutdown_evt=shutdown_evt,
            gate=gate,
            clock=clock,
            inbound_q=inbound_q,
            ws_write_timeout_ms=cfg.ws_write_timeout_ms,
            ws_close_on_write_timeout=cfg.ws_close_on_write_timeout,
            ws_max_consecutive_write_timeouts=cfg.ws_max_consecutive_write_timeouts,
        )
    )
    orch_task = asyncio.create_task(orch.run())

    try:
        await orch_task
    finally:
        shutdown_evt.set()
        reader_task.cancel()
        writer_task.cancel()
        if llm is not None:
            await llm.aclose()
        await transport.close(code=1000, reason="session_end")
