from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from webui.web_paths import static_dir, templates_dir
from webui.routes import config, dashboard, experiment, programs

if TYPE_CHECKING:
    from webui.node import WebHMINode


class BasicAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, username: str, password: str) -> None:
        super().__init__(app)
        self._username = username
        self._password = password

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path.startswith('/static/'):
            return await call_next(request)
        if request.url.path == '/ws/experiment':
            return await call_next(request)
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Basic '):
            import base64

            try:
                decoded = base64.b64decode(auth[6:]).decode('utf-8')
                user, _, pwd = decoded.partition(':')
                if secrets.compare_digest(user, self._username) and secrets.compare_digest(pwd, self._password):
                    return await call_next(request)
            except Exception:
                pass
        return Response(
            status_code=401,
            headers={'WWW-Authenticate': 'Basic realm="Delatometry"'},
            content='Authentication required',
        )


def create_app(node: 'WebHMINode') -> FastAPI:
    app = FastAPI(title=node.title)
    templates = Jinja2Templates(directory=str(templates_dir()))
    app.state.node = node
    app.state.templates = templates

    static_path = static_dir()
    if static_path.is_dir():
        app.mount('/static', StaticFiles(directory=str(static_path)), name='static')

    if node.auth_enabled:
        app.add_middleware(BasicAuthMiddleware, username=node.auth_user, password=node.auth_password)

    app.include_router(dashboard.router)
    app.include_router(programs.router)
    app.include_router(experiment.router)
    app.include_router(config.router)

    @app.get('/')
    async def root() -> RedirectResponse:
        return RedirectResponse(url='/dashboard', status_code=302)

    return app
