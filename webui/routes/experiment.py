from __future__ import annotations

import asyncio
import json
from urllib.parse import quote

from fastapi import APIRouter, Form, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse

from webui.render import template_response

router = APIRouter()


def _tpl(request: Request):
    return request.app.state.templates, request.app.state.node


@router.get('/experiment', response_class=HTMLResponse)
async def experiment_page(request: Request, msg: str = Query('')) -> HTMLResponse:
    templates, node = _tpl(request)
    return template_response(
        templates,
        request,
        'experiment.html',
        {
            'title': node.title,
            'refresh_sec': node.status_refresh_period_sec,
            'message': msg,
        },
    )


@router.post('/experiment/manual')
async def experiment_manual(
    request: Request,
    target_k: float = Form(...),
    enabled: str = Form('false'),
) -> RedirectResponse:
    _, node = _tpl(request)
    manual_on = enabled.lower() in ('true', '1', 'on', 'yes')
    result_msg = node.ui_manual_target(target_k, manual_on)
    return RedirectResponse(url=f'/experiment?msg={quote(result_msg)}', status_code=303)


@router.websocket('/ws/experiment')
async def experiment_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    node = websocket.app.state.node
    period = max(0.2, float(node.status_refresh_period_sec))
    try:
        while True:
            payload = node.get_experiment_snapshot()
            await websocket.send_text(json.dumps(payload))
            await asyncio.sleep(period)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        try:
            await websocket.send_text(json.dumps({'error': str(exc)}))
        except Exception:
            pass
        await websocket.close()
