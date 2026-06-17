from __future__ import annotations

import asyncio
import json
from urllib.parse import quote

from fastapi import APIRouter, Form, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from webui.async_bridge import run_blocking
from webui.e720_view import measure_table_headers
from webui.i18n import translate_experiment_measure_stream, translate_experiment_measure_title
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
            'measure_source': node.measure_source,
            'measure_title': translate_experiment_measure_title(node.measure_source),
            'measure_stream_title': translate_experiment_measure_stream(node.measure_source),
            'measure_table_headers': measure_table_headers(node.measure_source),
        },
    )


@router.get('/experiment/status')
async def experiment_status(request: Request) -> JSONResponse:
    _, node = _tpl(request)
    return JSONResponse(await run_blocking(node.get_experiment_status))


@router.get('/api/experiment/status')
async def experiment_status_api(request: Request) -> JSONResponse:
    _, node = _tpl(request)
    return JSONResponse(await run_blocking(node.get_experiment_status))


@router.get('/api/experiment/snapshot')
async def experiment_snapshot_api(request: Request) -> JSONResponse:
    _, node = _tpl(request)
    try:
        return JSONResponse(await run_blocking(node.get_experiment_snapshot))
    except Exception as exc:
        return JSONResponse({'error': str(exc)}, status_code=500)


@router.post('/experiment/stop')
async def experiment_stop(request: Request) -> RedirectResponse:
    _, node = _tpl(request)
    _, result_msg = await run_blocking(node.ui_stop_program)
    return RedirectResponse(url=f'/experiment?msg={quote(result_msg)}', status_code=303)


@router.post('/experiment/stabilize')
async def experiment_stabilize(
    request: Request,
    target_k: float = Form(300.0),
) -> RedirectResponse:
    _, node = _tpl(request)
    result_msg = await run_blocking(node.ui_manual_target, float(target_k), True)
    return RedirectResponse(url=f'/experiment?msg={quote(result_msg)}', status_code=303)


@router.post('/experiment/manual')
async def experiment_manual(
    request: Request,
    target_k: float = Form(...),
    enabled: str = Form('false'),
) -> RedirectResponse:
    _, node = _tpl(request)
    manual_on = enabled.lower() in ('true', '1', 'on', 'yes')
    result_msg = await run_blocking(node.ui_manual_target, target_k, manual_on)
    return RedirectResponse(url=f'/experiment?msg={quote(result_msg)}', status_code=303)


@router.websocket('/ws/experiment')
async def experiment_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    node = websocket.app.state.node
    period = max(0.2, float(node.status_refresh_period_sec))
    try:
        while True:
            payload = await run_blocking(node.get_experiment_snapshot)
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
