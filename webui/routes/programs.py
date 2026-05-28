from __future__ import annotations

from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from webui.e720_sweep import STANDARD_FREQUENCIES
from webui.i18n import translate_e720_modes, translate_validation_result
from webui.program_steps import parse_step_field_updates
from webui.render import template_response
from webui.temperature_validation import (
    T_MAX_K,
    T_MIN_K,
    suggest_next_step,
    validate_new_program,
    validate_temperature_steps,
)

router = APIRouter()


def _tpl(request: Request):
    return request.app.state.templates, request.app.state.node


def _e720_choices():
    return {
        'modes': translate_e720_modes(),
        'frequencies': [str(f) for f in STANDARD_FREQUENCIES],
    }


@router.get('/programs', response_class=HTMLResponse)
async def programs_list(request: Request, msg: str = Query(''), refresh: int = Query(0)) -> HTMLResponse:
    templates, node = _tpl(request)
    rows, table_err = node._programs_table(force_refresh=bool(refresh))
    banner = table_err or msg
    return template_response(
        templates,
        request,
        'programs/list.html',
        {'programs': rows, 'message': banner, 'title': node.title},
    )


@router.get('/program-new', response_class=HTMLResponse)
async def program_new_form(request: Request, msg: str = Query(''), new: int = Query(0)) -> HTMLResponse:
    templates, node = _tpl(request)
    if new:
        node.clear_new_program_draft()
    meta = node.get_new_program_draft_meta()
    default_step = node.suggest_new_program_step_defaults()
    return template_response(
        templates,
        request,
        'programs/new.html',
        {
            'title': node.title,
            'description': meta['description'],
            'steps': node.get_new_program_draft(),
            'message': msg,
            'e720': _e720_choices(),
            'sweep_mode': meta['sweep_mode'],
            'enabled_freqs': meta['enabled_freqs'],
            'range_max': meta['range_max'],
            'default_step': default_step,
            't_limits': {'t_min_k': T_MIN_K, 't_max_k': T_MAX_K},
        },
    )


@router.post('/program-new/validate')
async def program_new_validate(request: Request) -> JSONResponse:
    _, node = _tpl(request)
    form = await request.form()
    node.sync_new_program_draft_from_form(form)
    draft = node.get_new_program_draft()
    enabled = form.getlist('enabled_freqs') if hasattr(form, 'getlist') else []
    result = validate_new_program(
        str(form.get('description', '') or ''),
        draft,
        int(form.get('sweep_mode', 0) or 0),
        enabled,
        float(form.get('range_max', 10000) or 10000),
    )
    return JSONResponse(translate_validation_result(result))


@router.post('/program-new/steps/add')
async def program_new_add_step(
    request: Request,
    t_start: float = Form(...),
    t_stop: float = Form(...),
    minutes: float = Form(...),
    description: str = Form(''),
) -> RedirectResponse:
    _, node = _tpl(request)
    node.set_new_program_draft_meta(description=description)
    err = node.add_new_program_draft_step(t_start, t_stop, minutes)
    if err:
        return RedirectResponse(url=f'/program-new?msg={quote(err)}', status_code=303)
    return RedirectResponse(url='/program-new?msg=Step+added', status_code=303)


@router.post('/program-new/steps/remove')
async def program_new_remove_step(request: Request, step_id: int = Form(...)) -> RedirectResponse:
    _, node = _tpl(request)
    form = await request.form()
    node.sync_new_program_draft_from_form(form)
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
    node.sync_new_program_draft_from_form(form)
    msg = node.ui_program_create_from_draft(description, sweep_mode, enabled_freqs, range_max)
    if not msg.startswith('Program '):
        return RedirectResponse(url=f'/program-new?msg={quote(msg)}', status_code=303)
    return RedirectResponse(url=f'/programs?msg={quote(msg)}&refresh=1', status_code=303)


@router.get('/program-view', response_class=HTMLResponse)
async def program_view(request: Request, id: int = Query(0), msg: str = Query('')) -> HTMLResponse:
    templates, node = _tpl(request)
    fields = node.program_view_fields(id)
    return template_response(
        templates,
        request,
        'programs/view.html',
        {
            'title': node.title,
            'program_id': id,
            **fields,
            'message': msg or fields.get('message', ''),
        },
    )


@router.post('/program-view/run')
async def program_view_run(request: Request, id: int = Form(...)) -> RedirectResponse:
    _, node = _tpl(request)
    msg, _banner = node.ui_start_program(float(id))
    return RedirectResponse(url=f'/program-view?id={id}&msg={quote(msg)}', status_code=303)


@router.post('/program-view/stop')
async def program_view_stop(request: Request, id: int = Form(...)) -> RedirectResponse:
    _, node = _tpl(request)
    msg, _banner = node.ui_stop_program_by_id(float(id))
    return RedirectResponse(url=f'/program-view?id={id}&msg={quote(msg)}', status_code=303)


@router.get('/program-view/export')
async def program_view_export(
    request: Request,
    program_id: int = Query(...),
    run_id: int = Query(...),
):
    _, node = _tpl(request)
    path, msg = node.ui_export_program_run(program_id, run_id)
    if not path:
        return RedirectResponse(
            url=f'/program-view?id={program_id}&msg={quote(msg or "Export failed")}',
            status_code=303,
        )
    return FileResponse(path, filename=path.split('/')[-1], media_type='application/zip')


@router.post('/program-view/run/delete')
async def program_view_run_delete(
    request: Request,
    program_id: int = Form(...),
    run_id: int = Form(...),
) -> RedirectResponse:
    _, node = _tpl(request)
    msg = node.ui_delete_program_run(program_id, run_id)
    return RedirectResponse(url=f'/program-view?id={program_id}&msg={quote(msg)}', status_code=303)


@router.get('/program-run', response_class=HTMLResponse)
async def program_run_view(
    request: Request,
    program_id: int = Query(0),
    run_id: int = Query(0),
    msg: str = Query(''),
) -> HTMLResponse:
    templates, node = _tpl(request)
    fields = node.program_run_view_fields(program_id, run_id)
    return template_response(
        templates,
        request,
        'programs/run_view.html',
        {
            'title': node.title,
            'program_id': program_id,
            'run_id': run_id,
            'message': msg or fields.get('message', ''),
            **fields,
        },
    )


@router.get('/program-run/chart')
async def program_run_chart(
    request: Request,
    program_id: int = Query(...),
    run_id: int = Query(...),
    name: str = Query(...),
):
    _, node = _tpl(request)
    path = node.run_chart_file_path(program_id, run_id, name)
    if path is None:
        return RedirectResponse(url=f'/program-run?program_id={program_id}&run_id={run_id}', status_code=303)
    return FileResponse(path, media_type='image/png')


@router.post('/program-run/regenerate-charts')
async def program_run_regenerate_charts(
    request: Request,
    program_id: int = Form(...),
    run_id: int = Form(...),
) -> RedirectResponse:
    _, node = _tpl(request)
    node._schedule_run_charts(int(run_id), int(program_id))
    return RedirectResponse(
        url=f'/program-run?program_id={program_id}&run_id={run_id}&msg=Chart+generation+started',
        status_code=303,
    )


@router.get('/api/program-run/measurements')
async def program_run_measurements_api(
    request: Request,
    run_id: int = Query(...),
    program_id: int = Query(0),
    offset: int = Query(0),
    limit: int = Query(100),
) -> JSONResponse:
    _, node = _tpl(request)
    if not node._db_available():
        return JSONResponse({'result': 'False', 'row': [], 'total': 0, 'error': 'database unavailable'})
    response = node._db_query({
        'cmd': 'measurement_list_page',
        'run_id': run_id,
        'program_id': program_id,
        'offset': max(0, offset),
        'limit': min(500, max(1, limit)),
    })
    return JSONResponse(response)


@router.get('/program-edit', response_class=HTMLResponse)
async def program_edit_form(request: Request, id: int = Query(0), msg: str = Query('')) -> HTMLResponse:
    templates, node = _tpl(request)
    node.ui_programs_set_nav(id)
    fields = node.program_edit_fields(id)
    default_step = suggest_next_step(fields.get('steps', []))
    return template_response(
        templates,
        request,
        'programs/edit.html',
        {
            'title': node.title,
            'program_id': id,
            'message': msg,
            'e720': _e720_choices(),
            'default_step': default_step,
            't_limits': {'t_min_k': T_MIN_K, 't_max_k': T_MAX_K},
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
    draft = node.get_new_program_draft()
    steps_ok, issues = validate_temperature_steps(draft)
    if not steps_ok:
        msg = issues[0].message if issues else 'Invalid step.'
        return JSONResponse({'ok': False, 'message': msg, 'step_id': step_id})
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
    _, text = node.ui_program_edit_add_step(t_start, t_stop, minutes)
    return RedirectResponse(url=f'/program-edit?id={id}&msg={quote(text)}', status_code=303)


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
    msg, _banner = node.ui_start_program(float(id))
    return RedirectResponse(url=f'/program-edit?id={id}&msg={quote(msg)}', status_code=303)


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
