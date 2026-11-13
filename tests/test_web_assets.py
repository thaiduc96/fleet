"""Browsers must never run a stale app.js after an update (1.1.0 showed the new 📎 button with old code)."""

import hashlib
import re
from collections.abc import Callable
from pathlib import Path

from fastapi.testclient import TestClient

from fleetd.main import WEB_DIR, versioned_index

MakeClient = Callable[..., TestClient]


def short_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


def test_index_links_assets_with_their_content_hash(make_client: MakeClient) -> None:
    c = make_client()

    html = c.get("/").text

    assert f'src="/static/app.js?v={short_hash(WEB_DIR / "app.js")}"' in html
    assert f'href="/static/style.css?v={short_hash(WEB_DIR / "style.css")}"' in html


def test_index_is_never_served_from_cache_unchecked(make_client: MakeClient) -> None:
    c = make_client()

    r = c.get("/")

    assert r.headers["cache-control"] == "no-cache"


def test_static_assets_must_be_revalidated(make_client: MakeClient) -> None:
    c = make_client()

    r = c.get("/static/app.js")

    assert (r.status_code, r.headers["cache-control"]) == (200, "no-cache")


def test_changing_app_js_changes_its_url(tmp_path: Path) -> None:
    page = '<link href="/static/style.css"><script src="/static/app.js"></script>'
    (tmp_path / "index.html").write_text(page)
    (tmp_path / "style.css").write_text("body{}")
    (tmp_path / "app.js").write_text("old()")
    before = versioned_index(tmp_path)
    (tmp_path / "app.js").write_text("new()")

    after = versioned_index(tmp_path)

    assert app_js_version(before) != app_js_version(after)


def app_js_version(html: str) -> str:
    m = re.search(r'app\.js\?v=([0-9a-f]{8})"', html)
    assert m, html
    return m.group(1)
