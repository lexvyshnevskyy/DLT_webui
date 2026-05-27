from __future__ import annotations

import asyncio
import json
from urllib.parse import quote

from fastapi import APIRouter, Form, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from webui.render import template_response

router = APIRouter()


async def _dashboard_live_payload(conn: Request | WebSocket) -> dict:
    return conn.app.state.node.get_dashboard_snapshot()


def _tpl(request: Request):
    return request.app.state.templates, request.app.state.node


@router.get('/dashboard', response_class=HTMLResponse)
async def dashboard_page(request: Request, msg: str = Query('')) -> HTMLResponse:
    templates, node = _tpl(request)
    ctx = node.get_dashboard_context()
    ctx['message'] = msg
    return template_response(templates, request, 'dashboard.html', ctx)


@router.post('/dashboard/service')
async def service_control(request: Request, unit: str = Form(...), action: str = Form(...)) -> RedirectResponse:
    _, node = _tpl(request)
    result_msg = node.ui_service_control(unit, action)
    return RedirectResponse(url=f'/dashboard?msg={quote(result_msg)}', status_code=303)


async def dashboard_snapshot(request: Request) -> JSONResponse:
    """HTTP fallback for live dashboard (1 Hz polling) when WebSocket is blocked."""
    try:
        return JSONResponse(await _dashboard_live_payload(request))
    except Exception as exc:
        return JSONResponse({'error': str(exc)}, status_code=500)


async def dashboard_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    period = 1.0
    try:
        while True:
            try:
                payload = await _dashboard_live_payload(websocket)
                await websocket.send_text(json.dumps(payload, default=str))
            except Exception as exc:
                await websocket.send_text(json.dumps({'error': str(exc)}))
            await asyncio.sleep(period)
    except WebSocketDisconnect:
        return
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass
