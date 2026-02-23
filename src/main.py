from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from src.agent.orchestrator import SessionOrchestrator


app = FastAPI(title="Expressive Voice Prototype")


@app.get("/healthz")
async def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.websocket("/expressive/ws/{session_id}")
async def expressive_ws(ws: WebSocket, session_id: str) -> None:
    await ws.accept()
    orch = SessionOrchestrator(session_id=session_id)
    await orch.start()

    async def reader() -> None:
        while True:
            msg = await ws.receive()
            mtype = msg.get("type")
            if mtype == "websocket.disconnect":
                return
            text = msg.get("text")
            if text is not None:
                try:
                    obj = json.loads(text)
                except Exception:
                    await orch.submit_control({"type": "bad.json", "raw": text})
                    continue
                await orch.submit_control(obj)
                continue
            b = msg.get("bytes")
            if b is not None:
                await orch.submit_audio(bytes(b))

    async def writer() -> None:
        while True:
            out = await orch.next_outbound()
            if out.kind == "json":
                await ws.send_text(json.dumps(out.payload, separators=(",", ":"), sort_keys=True))
            else:
                await ws.send_bytes(out.payload)  # type: ignore[arg-type]

    reader_task = asyncio.create_task(reader())
    writer_task = asyncio.create_task(writer())

    try:
        done, pending = await asyncio.wait({reader_task, writer_task}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for t in done:
            exc = t.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                raise exc
    finally:
        await orch.stop()
        try:
            await ws.close()
        except Exception:
            pass
