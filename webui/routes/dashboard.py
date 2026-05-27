from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter()


def _tpl(request: Request):
    return request.app.state.templates, request.app.state.node


@router.get('/dashboard', response_class=HTMLResponse)
async def dashboard_page(request: Request, msg: str = Query('')) -> HTMLResponse:
    templates, node = _tpl(request)
    ctx = node.get_dashboard_context()
    ctx['message'] = msg
    return templates.TemplateResponse(request, 'dashboard.html', ctx)


@router.post('/dashboard/service')
async def service_control(request: Request, unit: str = Form(...), action: str = Form(...)) -> RedirectResponse:
    _, node = _tpl(request)
    result_msg = node.ui_service_control(unit, action)
    return RedirectResponse(url=f'/dashboard?msg={quote(result_msg)}', status_code=303)
