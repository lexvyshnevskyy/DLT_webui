from __future__ import annotations

import base64
import secrets
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from webui.i18n import COOKIE_NAME, _, bind_locale, get_locale, resolve_locale
from webui.web_paths import static_dir, templates_dir
from webui.routes import config, dashboard, experiment, programs

if TYPE_CHECKING:
    from webui.node import WebHMINode


def _path_exempt_from_auth(path: str) -> bool:
    path = path or ''
    if path.startswith('/static/'):
        return True
    if path.startswith('/ws/'):
        return True
    if path.startswith('/api/'):
        return True
    if path == '/dashboard/snapshot':
        return True
    return False


class BasicAuthMiddleware:
    """ASGI middleware — must not block WebSocket handshakes (HTTP Upgrade to /ws/*)."""

    def __init__(self, app: ASGIApp, username: str, password: str) -> None:
        self.app = app
        self._username = username
        self._password = password

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] == 'websocket':
            await self.app(scope, receive, send)
            return

        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        path = scope.get('path', '')
        if _path_exempt_from_auth(path):
            await self.app(scope, receive, send)
            return

        # WebSocket upgrade is an HTTP request first; do not require auth on /ws/*
        headers = {k.decode('latin-1').lower(): v.decode('latin-1') for k, v in scope.get('headers', [])}
        if headers.get('upgrade', '').lower() == 'websocket' and path.startswith('/ws'):
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Basic '):
            try:
                decoded = base64.b64decode(auth[6:]).decode('utf-8')
                user, _, pwd = decoded.partition(':')
                if secrets.compare_digest(user, self._username) and secrets.compare_digest(pwd, self._password):
                    await self.app(scope, receive, send)
                    return
            except Exception:
                pass

        response = Response(
            status_code=401,
            headers={'WWW-Authenticate': 'Basic realm="Delatometry"'},
            content='Authentication required',
        )
        await response(scope, receive, send)


class LocaleMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        bind_locale(resolve_locale(request))
        return await call_next(request)


def create_app(node: 'WebHMINode') -> FastAPI:
    app = FastAPI(title=node.title)
    templates = Jinja2Templates(directory=str(templates_dir()))
    templates.env.globals['_'] = _
    templates.env.globals['get_locale'] = get_locale
    app.state.node = node
    app.state.templates = templates

    app.add_middleware(LocaleMiddleware)

    static_path = static_dir()
    if static_path.is_dir():
        app.mount('/static', StaticFiles(directory=str(static_path)), name='static')

    if node.auth_enabled:
        app.add_middleware(BasicAuthMiddleware, username=node.auth_user, password=node.auth_password)

    app.include_router(dashboard.router)
    app.include_router(programs.router)
    app.include_router(experiment.router)
    app.include_router(config.router)

    # Live dashboard endpoints on the app factory (not only the router) so a stale
    # install/webui/.../routes/dashboard.py cannot drop them after partial sync.
    app.add_api_route(
        '/dashboard/snapshot',
        dashboard.dashboard_snapshot,
        methods=['GET'],
        name='dashboard_snapshot',
    )
    app.add_api_route(
        '/api/dashboard/snapshot',
        dashboard.dashboard_snapshot,
        methods=['GET'],
        name='dashboard_snapshot_api',
    )
    app.add_api_websocket_route('/ws/dashboard', dashboard.dashboard_ws, name='dashboard_ws')
    app.add_api_route(
        '/api/experiment/status',
        experiment.experiment_status_api,
        methods=['GET'],
        name='experiment_status_api',
    )

    @app.get('/')
    async def root() -> RedirectResponse:
        return RedirectResponse(url='/dashboard', status_code=302)

    @app.get('/set-locale/{locale_code}')
    async def set_locale(request: Request, locale_code: str) -> RedirectResponse:
        from webui.i18n import normalize_locale

        loc = normalize_locale(locale_code)
        target = request.query_params.get('next') or request.headers.get('referer') or '/dashboard'
        if target.startswith('/set-locale'):
            target = '/dashboard'
        response = RedirectResponse(url=target, status_code=303)
        response.set_cookie(COOKIE_NAME, loc, max_age=365 * 86400, path='/', samesite='lax')
        return response

    @app.on_event('startup')
    async def _log_routes() -> None:
        paths = sorted({getattr(r, 'path', None) for r in app.routes if getattr(r, 'path', None)})
        live = [p for p in paths if 'snapshot' in p or p.startswith('/ws/')]
        node._log(
            f'Web UI auth_enabled={node.auth_enabled}; live routes: {", ".join(live) or "(none)"}'
        )

    return app
