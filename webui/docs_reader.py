from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from webui.web_paths import package_dir

_SLUG_RE = re.compile(r'^[a-z0-9-]+$')
_MANIFEST_NAME = 'manifest.json'


def _share_docs_root() -> Path | None:
    try:
        from ament_index_python.packages import get_package_share_directory

        root = Path(get_package_share_directory('webui')) / 'docs'
        if root.is_dir():
            return root
    except Exception:
        pass
    return None


def docs_workspace_root() -> Path:
    """Directory containing en/, uk/, and manifest.json."""
    shared = _share_docs_root()
    if shared is not None:
        return shared
    # Dev: workspace/docs next to src/
    # package_dir = .../src/webui/webui → workspace root is parents[2]
    candidate = package_dir().resolve().parents[2] / 'docs'
    if candidate.is_dir():
        return candidate
    return package_dir() / 'docs'


def normalize_lang(lang: str | None) -> str:
    if not lang:
        return 'en'
    lang = lang.strip().lower()
    if lang in ('uk', 'ua'):
        return 'uk'
    return 'en'


def _load_manifest() -> dict[str, Any]:
    path = docs_workspace_root() / _MANIFEST_NAME
    if not path.is_file():
        return {'pages': []}
    return json.loads(path.read_text(encoding='utf-8'))


def list_pages(lang: str) -> list[dict[str, str]]:
    lang = normalize_lang(lang)
    pages: list[dict[str, str]] = []
    root = docs_workspace_root()
    for entry in _load_manifest().get('pages', []):
        slug = str(entry.get('slug', '')).strip()
        if not _SLUG_RE.match(slug):
            continue
        file_name = str(entry.get('file', f'{slug}.md'))
        titles = entry.get('title') or {}
        title = titles.get(lang) or titles.get('en') or slug.replace('-', ' ').title()
        md_path = root / lang / file_name
        if md_path.is_file():
            pages.append({'slug': slug, 'title': title, 'file': file_name})
    return pages


def page_markdown_path(lang: str, slug: str) -> Path | None:
    lang = normalize_lang(lang)
    if not _SLUG_RE.match(slug):
        return None
    for page in _load_manifest().get('pages', []):
        if str(page.get('slug')) != slug:
            continue
        file_name = str(page.get('file', f'{slug}.md'))
        path = docs_workspace_root() / lang / file_name
        if path.is_file():
            return path.resolve()
        return None
    return None


def page_title(lang: str, slug: str) -> str:
    lang = normalize_lang(lang)
    for page in _load_manifest().get('pages', []):
        if str(page.get('slug')) != slug:
            continue
        titles = page.get('title') or {}
        return str(titles.get(lang) or titles.get('en') or slug)
    return slug


def render_markdown(text: str) -> str:
    import markdown

    return markdown.markdown(
        text,
        extensions=['extra', 'sane_lists', 'toc'],
        extension_configs={'toc': {'permalink': True}},
    )
