from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from webui.e720_sweep import STANDARD_FREQUENCIES, SWEEP_MODE_LABELS
from webui.program_steps import DEFAULT_NEW_STEP, parse_step_field_updates

router = APIRouter()


def _tpl(request: Request):
    return request.app.state.templates, request.app.state.node


def _e720_choices():
    return {
        'modes': [(label, key) for key, label in sorted(SWEEP_MODE_LABELS.items())],
        'frequencies': [str(f) for f in STANDARD_FREQUENCIES],
    }


@router.get('/programs', response_class=HTMLResponse)
async def programs_list(request: Request, msg: str = Query('')) -> HTMLResponse:
    templates, node = _tpl(request)
    rows = node._programs_table()
    return templates.TemplateResponse(
        request,
        'programs/list.html',
        {'programs': rows, 'message': msg, 'title': node.title},
    )


@router.get('/program-new', response_class=HTMLResponse)
async def program_new_form(request: Request, msg: str = Query(''), new: int = Query(0)) -> HTMLResponse:
    templates, node = _tpl(request)
    if new:
        node.clear_new_program_draft()
    return templates.TemplateResponse(
        request,
        'programs/new.html',
        {
            'title': node.title,
            'description': '',
            'steps': node.get_new_program_draft(),
            'message': msg,
            'e720': _e720_choices(),
            'default_step': DEFAULT_NEW_STEP,
        },
    )


@router.post('/program-new/steps/add')
async def program_new_add_step(
    request: Request,
    t_start: float = Form(...),
    t_stop: float = Form(...),
    minutes: float = Form(...),
) -> RedirectResponse:
    _, node = _tpl(request)
    node.add_new_program_draft_step(t_start, t_stop, minutes)
    return RedirectResponse(url='/program-new?msg=Step+added', status_code=303)


@router.post('/program-new/steps/remove')
async def program_new_remove_step(request: Request, step_id: int = Form(...)) -> RedirectResponse:
    _, node = _tpl(request)
    node.remove_new_program_draft_step(step_id)
    return RedirectResponse(url='/program-new?msg=Step+removed', status_code=303)


@router.post('/program-new')
async def program_new_create(
    request: Request,
    description: str = Form(''),
    sweep_mode: int = Form(0),
    enabled_freqs: List[str] = Form(default=[]),
    range_max: float = Form(10000),
) -> RedirectResponse:
    _, node = _tpl(request)
    form = await request.form()
    node.update_new_program_draft_from_form(parse_step_field_updates(form))
    msg = node.ui_program_create_from_draft(description, sweep_mode, enabled_freqs, range_max)
    if msg.startswith('ERROR') or msg.startswith('Add at least'):
        return RedirectResponse(url=f'/program-new?msg={msg}', status_code=303)
    return RedirectResponse(url=f'/programs?msg={msg}', status_code=303)


@router.get('/program-view', response_class=HTMLResponse)
async def program_view(request: Request, id: int = Query(0)) -> HTMLResponse:
    templates, node = _tpl(request)
    fields = node.program_view_fields(id)
    return templates.TemplateResponse(
        request,
        'programs/view.html',
        {'title': node.title, 'program_id': id, **fields},
    )


@router.get('/program-edit', response_class=HTMLResponse)
async def program_edit_form(request: Request, id: int = Query(0), msg: str = Query('')) -> HTMLResponse:
    templates, node = _tpl(request)
    node.ui_programs_set_nav(id)
    fields = node.program_edit_fields(id)
    return templates.TemplateResponse(
        request,
        'programs/edit.html',
        {
            'title': node.title,
            'program_id': id,
            'message': msg,
            'e720': _e720_choices(),
            **fields,
        },
    )


@router.post('/program-edit')
async def program_edit_save(
    request: Request,
    id: int = Form(...),
    description: str = Form(''),
    sweep_mode: int = Form(0),
    enabled_freqs: List[str] = Form(default=[]),
    range_max: float = Form(10000),
) -> RedirectResponse:
    _, node = _tpl(request)
    node.ui_programs_set_nav(id)
    form = await request.form()
    step_updates = parse_step_field_updates(form)
    msg = node.ui_program_edit_save(description, sweep_mode, enabled_freqs, range_max, step_updates)
    return RedirectResponse(url=f'/program-edit?id={id}&msg={msg}', status_code=303)


@router.post('/program-edit/steps/save-one')
async def program_edit_save_one_step(
    request: Request,
    id: int = Form(...),
    step_id: int = Form(...),
    t_start: float = Form(...),
    t_stop: float = Form(...),
    minutes: float = Form(...),
) -> JSONResponse:
    _, node = _tpl(request)
    node.ui_programs_set_nav(id)
    ok, msg = node.ui_program_update_single_step(id, step_id, t_start, t_stop, minutes)
    return JSONResponse({'ok': ok, 'message': msg, 'step_id': step_id})


@router.post('/program-new/steps/save-one')
async def program_new_save_one_step(
    request: Request,
    step_id: int = Form(...),
    t_start: float = Form(...),
    t_stop: float = Form(...),
    minutes: float = Form(...),
) -> JSONResponse:
    _, node = _tpl(request)
    node.update_new_program_draft_step(int(step_id), float(t_start), float(t_stop), float(minutes))
    return JSONResponse({'ok': True, 'message': f'Step {step_id} updated.', 'step_id': step_id})


@router.post('/program-edit/steps/add')
async def program_edit_add_step(
    request: Request,
    id: int = Form(...),
    t_start: float = Form(...),
    t_stop: float = Form(...),
    minutes: float = Form(...),
) -> RedirectResponse:
    _, node = _tpl(request)
    node.ui_programs_set_nav(id)
    node.ui_program_edit_add_step(t_start, t_stop, minutes)
    return RedirectResponse(url=f'/program-edit?id={id}', status_code=303)


@router.post('/program-edit/steps/remove')
async def program_edit_remove_step(request: Request, id: int = Form(...), step_id: int = Form(...)) -> RedirectResponse:
    _, node = _tpl(request)
    node.ui_programs_set_nav(id)
    node.ui_program_edit_delete_step(step_id)
    return RedirectResponse(url=f'/program-edit?id={id}', status_code=303)


@router.post('/program-edit/run')
async def program_edit_run(request: Request, id: int = Form(...)) -> RedirectResponse:
    _, node = _tpl(request)
    node.ui_programs_set_nav(id)
    node.ui_start_program(float(id))
    return RedirectResponse(url=f'/program-edit?id={id}', status_code=303)


@router.post('/program-edit/stop')
async def program_edit_stop(request: Request, id: int = Form(...)) -> RedirectResponse:
    _, node = _tpl(request)
    node.ui_stop_program()
    return RedirectResponse(url=f'/program-edit?id={id}', status_code=303)


@router.post('/programs/delete')
async def programs_delete(request: Request, id: int = Form(...)) -> RedirectResponse:
    _, node = _tpl(request)
    _, msg = node.ui_programs_action_delete(id)
    return RedirectResponse(url=f'/programs?msg={msg}', status_code=303)


@router.get('/programs/export')
async def programs_export(request: Request, id: int = Query(...)):
    _, node = _tpl(request)
    path, msg = node.ui_programs_action_export(id)
    if not path:
        return RedirectResponse(url=f'/programs?msg={msg or "Export failed"}', status_code=303)
    return FileResponse(path, filename=path.split('/')[-1], media_type='application/zip')
