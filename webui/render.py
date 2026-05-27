from __future__ import annotations

from typing import Any, Dict

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from webui.i18n import _, bind_locale, i18n_template_context, resolve_locale


def template_response(
    templates: Jinja2Templates,
    request: Request,
    name: str,
    context: Dict[str, Any],
    **kwargs: Any,
) -> Response:
    bind_locale(resolve_locale(request))
    ctx = dict(context)
    ctx.update(i18n_template_context())
    ctx['_'] = _
    return templates.TemplateResponse(request, name, ctx, **kwargs)
