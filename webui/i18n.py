from __future__ import annotations

import json
import re
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from starlette.requests import Request

DEFAULT_LOCALE = 'en'
SUPPORTED_LOCALES = ('en', 'uk')
COOKIE_NAME = 'delatometry_lang'
_LOCALE_ALIASES = {'ua': 'uk', 'uk-ua': 'uk', 'en-us': 'en', 'en-gb': 'en'}

_current_locale: ContextVar[str] = ContextVar('locale', default=DEFAULT_LOCALE)
_catalogs: Dict[str, Dict[str, str]] = {}


def locale_dir() -> Path:
    from webui.web_paths import package_dir

    local = package_dir() / 'locale'
    if local.is_dir():
        return local
    try:
        from ament_index_python.packages import get_package_share_directory

        return Path(get_package_share_directory('webui')) / 'locale'
    except Exception:
        return local


def normalize_locale(raw: Optional[str]) -> str:
    if not raw:
        return DEFAULT_LOCALE
    code = str(raw).strip().lower().replace('_', '-')
    if code in _LOCALE_ALIASES:
        code = _LOCALE_ALIASES[code]
    if code in SUPPORTED_LOCALES:
        return code
    base = code.split('-', 1)[0]
    if base in _LOCALE_ALIASES:
        base = _LOCALE_ALIASES[base]
    return base if base in SUPPORTED_LOCALES else DEFAULT_LOCALE


def _load_catalog(locale: str) -> Dict[str, str]:
    locale = normalize_locale(locale)
    if locale in _catalogs:
        return _catalogs[locale]
    path = locale_dir() / f'{locale}.json'
    if not path.is_file() and locale != DEFAULT_LOCALE:
        return _load_catalog(DEFAULT_LOCALE)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            flat = {str(k): str(v) for k, v in data.items()}
            _catalogs[locale] = flat
            return flat
    except Exception:
        pass
    _catalogs[locale] = {}
    return _catalogs[locale]


def bind_locale(locale: str) -> str:
    loc = normalize_locale(locale)
    _current_locale.set(loc)
    _load_catalog(loc)
    _load_catalog(DEFAULT_LOCALE)
    return loc


def get_locale() -> str:
    return _current_locale.get()


def translate(locale: str, key: str, **kwargs: Any) -> str:
    loc = normalize_locale(locale)
    catalog = _load_catalog(loc)
    text = catalog.get(key)
    if text is None and loc != DEFAULT_LOCALE:
        text = _load_catalog(DEFAULT_LOCALE).get(key)
    if text is None:
        text = key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, ValueError):
            return text
    return text


def _(key: str, **kwargs: Any) -> str:
    return translate(get_locale(), key, **kwargs)


def resolve_locale(request: Request) -> str:
    query = request.query_params.get('lang')
    if query:
        return normalize_locale(query)
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        return normalize_locale(cookie)
    accept = request.headers.get('accept-language', '')
    for part in accept.split(','):
        token = part.split(';', 1)[0].strip().lower()
        if not token:
            continue
        loc = normalize_locale(token)
        if loc in SUPPORTED_LOCALES:
            return loc
    return DEFAULT_LOCALE


def locale_switch_label(code: str) -> str:
    return {'en': 'EN', 'uk': 'UA'}.get(normalize_locale(code), code.upper())


def translate_validation_issues(issues: list) -> list:
    from webui.temperature_validation import T_MAX_K, T_MIN_K

    out = []
    for issue in issues:
        d = issue.to_dict() if hasattr(issue, 'to_dict') else dict(issue)
        code = d.get('code', '')
        params = _validation_params(d)
        params.setdefault('t_min', int(T_MIN_K) if T_MIN_K == int(T_MIN_K) else T_MIN_K)
        params.setdefault('t_max', int(T_MAX_K) if T_MAX_K == int(T_MAX_K) else T_MAX_K)
        key = f'validation.{code}' if code else ''
        if key and (_load_catalog(get_locale()).get(key) or _load_catalog(DEFAULT_LOCALE).get(key)):
            d['message'] = _(key, **params)
        out.append(d)
    return out


def _validation_params(issue: Mapping[str, Any]) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    for field in ('step_id', 'prev_step', 't_min', 't_max', 't_start', 't_stop', 'prev_stop', 'expected', 'value'):
        if field in issue and issue[field] is not None:
            params[field] = issue[field]
    step_id = issue.get('step_id') or 0
    if step_id and 'prev_step' not in params:
        params['prev_step'] = int(step_id) - 1
    msg = str(issue.get('message', ''))
    m = re.search(r'got ([\d.]+) K', msg)
    if m and 'value' not in params:
        params['value'] = m.group(1)
    m = re.search(r'ends at ([\d.]+) K', msg)
    if m and 'expected' not in params:
        params['expected'] = m.group(1)
    m = re.search(r'must be ([\d.]+) K', msg)
    if m and 'expected' not in params:
        params['expected'] = m.group(1)
    m = re.search(r'not ([\d.]+) K', msg)
    if m and 'value' not in params:
        params['value'] = m.group(1)
    return params


def translate_validation_result(result: Any) -> Dict[str, Any]:
    data = result.to_dict() if hasattr(result, 'to_dict') else dict(result)
    data['issues'] = translate_validation_issues(result.issues if hasattr(result, 'issues') else data.get('issues', []))
    return data


def translate_e720_modes() -> list:
    from webui.e720_sweep import SWEEP_MODE_LABELS

    return [( _(f'e720.mode.{mode}'), mode) for mode in sorted(SWEEP_MODE_LABELS)]


def i18n_template_context() -> Dict[str, Any]:
    loc = get_locale()
    return {
        'locale': loc,
        'locale_en': loc == 'en',
        'locale_uk': loc == 'uk',
        'lang_attr': 'uk' if loc == 'uk' else 'en',
    }
