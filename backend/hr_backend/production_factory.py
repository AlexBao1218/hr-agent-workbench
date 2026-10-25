"""Same-origin, password-protected deployment wrapper; local entry stays unchanged."""
from __future__ import annotations

import base64
import binascii
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Request
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.staticfiles import StaticFiles


def canonical_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in {'', '/'} or parsed.query or parsed.fragment):
        raise ValueError('Expected an HTTP(S) origin without a path')
    port = parsed.port
    host = parsed.hostname.lower()
    if ':' in host:
        host = f'[{host}]'
    if port and port != (443 if parsed.scheme == 'https' else 80):
        host += f':{port}'
    return f'{parsed.scheme}://{host}'


class FrontendFiles(StaticFiles):
    async def get_response(self, path, scope):
        # Never disguise an unknown API or missing asset as a successful SPA page.
        if path == 'api' or path.startswith('api/'):
            raise HTTPException(404)
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            accept = dict(scope['headers']).get(b'accept', b'').decode('latin1')
            if exc.status_code != 404 or scope['method'] not in {'GET', 'HEAD'}:
                raise
            if Path(path).suffix or 'text/html' not in accept:
                raise
            return await super().get_response('index.html', scope)


def create_production_app(*, environ=None, provider=None):
    env = os.environ if environ is None else environ
    username, password = env.get('HR_ACCESS_USER', ''), env.get('HR_ACCESS_PASSWORD', '')
    if not username.strip() or not password.strip() or ':' in username:
        raise RuntimeError('Production requires HR_ACCESS_USER and HR_ACCESS_PASSWORD; username cannot contain a colon')
    remote = env.get('HR_STORAGE_BACKEND', 'local') == 'supabase'
    data_dir = env.get('HR_CACHE_DIR', '/tmp/hr-ai') if remote else env.get('HR_DATA_DIR', '')
    if not data_dir or not Path(data_dir).is_absolute():
        raise RuntimeError('Production requires an absolute HR_DATA_DIR on persistent storage')
    root = Path(__file__).resolve().parents[2]
    dist = Path(env.get('HR_FRONTEND_DIST', str(root / 'frontend' / 'dist'))).resolve()
    if not (dist / 'index.html').is_file():
        raise RuntimeError('Build the frontend first or set HR_FRONTEND_DIST to its dist directory')
    try:
        allowed = {canonical_origin(item.strip()) for item in env.get('HR_ALLOWED_ORIGINS', '').split(',') if item.strip()}
    except ValueError:
        raise RuntimeError('HR_ALLOWED_ORIGINS must contain comma-separated HTTP(S) origins') from None

    # Import only after deployment configuration is validated; no local data fallback.
    from .app import create_app
    app = create_app(data_dir=data_dir, provider=provider, storage_config=env)
    expected_user, expected_password = username.encode('utf-8'), password.encode('utf-8')

    @app.middleware('http')
    async def access_control(request: Request, call_next):
        if request.url.path == '/health' and request.method in {'GET', 'HEAD'}:
            return JSONResponse({'status': 'ok'})
        credentials = request.headers.get('authorization', '').split(' ', 1)
        user, secret = b'', b''
        if len(credentials) == 2 and credentials[0].lower() == 'basic':
            try:
                decoded = base64.b64decode(credentials[1], validate=True)
                user, separator, secret = decoded.partition(b':')
                if not separator:
                    user, secret = b'', b''
            except (ValueError, binascii.Error):
                pass
        user_ok = secrets.compare_digest(user, expected_user)
        password_ok = secrets.compare_digest(secret, expected_password)
        if not (user_ok & password_ok):
            return JSONResponse({'detail': 'Authentication required'}, status_code=401,
                                headers={'WWW-Authenticate': 'Basic realm="HR AI", charset="UTF-8"', 'Cache-Control': 'no-store'})
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            try:
                origin = canonical_origin(request.headers.get('origin', ''))
                current = canonical_origin(str(request.base_url))
            except ValueError:
                return JSONResponse({'detail': 'Valid Origin required'}, status_code=403)
            if origin not in allowed and origin != current:
                return JSONResponse({'detail': 'Origin not allowed'}, status_code=403)
            if request.headers.get('sec-fetch-site') == 'cross-site':
                return JSONResponse({'detail': 'Cross-site writes are not allowed'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'DENY'
        return response

    app.mount('/', FrontendFiles(directory=str(dist), html=True), name='frontend')
    return app
