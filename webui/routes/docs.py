from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from webui.docs_reader import (
    list_pages,
    normalize_lang,
    page_markdown_path,
    page_title,
    render_markdown,
)
from webui.i18n import _, get_locale

router = APIRouter(tags=['docs'])


def _templates(request: Request):
    return request.app.state.templates


@router.get('/docs', include_in_schema=False)
async def docs_root(request: Request) -> RedirectResponse:
    lang = normalize_lang(request.query_params.get('lang') or get_locale())
    return RedirectResponse(url=f'/docs/{lang}', status_code=302)


@router.get('/docs/{lang}', response_class=HTMLResponse, include_in_schema=False)
async def docs_index(request: Request, lang: str) -> HTMLResponse:
    lang = normalize_lang(lang)
    pages = list_pages(lang)
    if not pages:
        raise HTTPException(status_code=404, detail='Documentation not installed')
    other = 'uk' if lang == 'en' else 'en'
    tpl = _templates(request)
    return tpl.TemplateResponse(
        request,
        'docs/index.html',
        {
            'title': _('nav.docs'),
            'active': 'docs',
            'doc_lang': lang,
            'other_lang': other,
            'pages': pages,
            'message': request.query_params.get('msg'),
        },
    )


@router.get('/docs/{lang}/{slug}', response_class=HTMLResponse, include_in_schema=False)
async def docs_page(request: Request, lang: str, slug: str) -> HTMLResponse:
    lang = normalize_lang(lang)
    md_path = page_markdown_path(lang, slug)
    if md_path is None:
        raise HTTPException(status_code=404, detail='Page not found')
    body_html = render_markdown(md_path.read_text(encoding='utf-8'))
    pages = list_pages(lang)
    other = 'uk' if lang == 'en' else 'en'
    tpl = _templates(request)
    return tpl.TemplateResponse(
        request,
        'docs/page.html',
        {
            'title': page_title(lang, slug),
            'active': 'docs',
            'doc_lang': lang,
            'other_lang': other,
            'slug': slug,
            'pages': pages,
            'body_html': body_html,
            'message': request.query_params.get('msg'),
        },
    )
