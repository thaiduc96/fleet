"""Browsers must never run a stale app.js after an update (1.1.0 showed the new 📎 button with old code)."""

import hashlib
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fleetd.main import WEB_DIR, versioned_index, web_version
from tests.conftest import AUTH

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


def test_the_dashboard_is_an_installable_web_app(make_client: MakeClient) -> None:
    c = make_client()

    r = c.get("/manifest.webmanifest")

    manifest = r.json()
    assert (r.status_code, r.headers["content-type"]) == (200, "application/manifest+json")
    assert (manifest["display"], manifest["start_url"]) == ("standalone", "/")
    assert [c.get(icon["src"]).status_code for icon in manifest["icons"]] == [200, 200, 200]


def test_index_links_the_manifest_and_the_ios_icon(make_client: MakeClient) -> None:
    c = make_client()

    html = c.get("/").text

    assert '<link rel="manifest" href="/manifest.webmanifest">' in html
    assert '<link rel="apple-touch-icon" href="/static/icons/apple-touch-icon.png">' in html
    assert c.get("/static/icons/apple-touch-icon.png").headers["content-type"] == "image/png"


def test_form_fields_are_16px_on_touch_screens_so_ios_does_not_zoom_on_focus() -> None:
    css = (WEB_DIR / "style.css").read_text()

    touch_field_rule = r"@media \(pointer: coarse\) \{\s*input, textarea, select[^{]*\{ font-size: 16px; \}"

    assert re.search(touch_field_rule, css)


# ---------------------------------------------------------------- reload after an update
def test_page_and_fleet_api_carry_the_same_web_version(make_client: MakeClient) -> None:
    c = make_client()

    html, fleet = c.get("/").text, c.get("/api/fleet", headers=AUTH).json()

    assert f'<meta name="fleet-web-version" content="{fleet["webVersion"]}">' in html


@pytest.mark.parametrize("changed", ["index.html", "app.js", "style.css"])
def test_changing_any_dashboard_file_changes_the_web_version(tmp_path: Path, changed: str) -> None:
    for name in ("index.html", "app.js", "style.css"):
        (tmp_path / name).write_text("v1")
    before = web_version(tmp_path)
    (tmp_path / changed).write_text("v2")

    after = web_version(tmp_path)

    assert before != after
