"""Attachments: saved where the session runs, path built server-side, old days pruned."""

import os
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from fleetd.config import Peer
from fleetd.uploads import MAX_UPLOAD_BYTES, Uploads, clean_name
from tests.conftest import AUTH, TOKEN

MakeClient = Callable[..., TestClient]
WRITE = {**AUTH, "X-Fleet-Request": "1"}
WORK = Peer(name="work", url="https://work.ts.net", label="💼 Công ty")
NOV_14 = 1_794_657_600.0  # 2026-11-14T12:00:00Z
DAY = 86400.0


# ---------------------------------------------------------------- names
@pytest.mark.parametrize(("raw", "expected"), [
    ("ảnh chụp màn hình.png", "ảnh_chụp_màn_hình.png"),
    ("../../etc/passwd", "passwd"),
    ("C:\\Users\\x\\report.pdf", "report.pdf"),
    ("bad\x00name\n.txt", "bad_name_.txt"),
    ("...", "file"),
    (".env", "env"),
])
def test_clean_name_keeps_only_a_safe_base_name(raw: str, expected: str) -> None:
    assert clean_name(raw) == expected


def test_clean_name_cuts_long_names_but_keeps_the_extension() -> None:
    assert clean_name("a" * 200 + ".png") == "a" * 76 + ".png"


# ---------------------------------------------------------------- storage
def test_save_writes_a_private_file_in_a_dated_folder(tmp_path: Path) -> None:
    uploads = Uploads(tmp_path / "uploads", now=lambda: NOV_14)

    result = uploads.save("../shot.png", b"PNG")

    path = Path(result.path)
    assert path.parent == tmp_path / "uploads" / "2026-11-14"
    assert path.name.endswith("-shot.png") and (result.name, result.size) == ("shot.png", 3)
    assert path.read_bytes() == b"PNG" and os.stat(path).st_mode & 0o777 == 0o600


def test_saving_the_same_name_twice_never_overwrites(tmp_path: Path) -> None:
    uploads = Uploads(tmp_path, now=lambda: NOV_14)

    first, second = uploads.save("a.txt", b"1"), uploads.save("a.txt", b"2")

    assert first.path != second.path and Path(first.path).read_bytes() == b"1"


def test_prune_removes_only_day_folders_older_than_the_limit(tmp_path: Path) -> None:
    old = Uploads(tmp_path, now=lambda: NOV_14 - 40 * DAY).save("old.txt", b"x")
    os.utime(old.path, (NOV_14 - 40 * DAY, NOV_14 - 40 * DAY))
    fresh = Uploads(tmp_path, now=lambda: NOV_14 - 2 * DAY).save("new.txt", b"x")
    os.utime(fresh.path, (NOV_14 - 2 * DAY, NOV_14 - 2 * DAY))
    (tmp_path / "not-a-day").mkdir()

    removed = Uploads(tmp_path, now=lambda: NOV_14).prune(keep_days=30)

    assert removed == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2026-11-12", "not-a-day"]


def test_prune_without_an_uploads_folder_does_nothing(tmp_path: Path) -> None:
    assert Uploads(tmp_path / "missing").prune() == 0


# ---------------------------------------------------------------- API
def test_upload_needs_the_csrf_header(make_client: MakeClient) -> None:
    c = make_client()

    r = c.post("/api/uploads", params={"name": "a.txt"}, headers=AUTH, content=b"x")

    assert r.status_code == 403


def test_upload_needs_the_token(make_client: MakeClient) -> None:
    c = make_client()

    r = c.post("/api/uploads", params={"name": "a.txt"}, headers={"X-Fleet-Request": "1"}, content=b"x")

    assert r.status_code == 401


def test_upload_returns_the_saved_path_under_fleet_home(make_client: MakeClient, tmp_path: Path) -> None:
    c = make_client()

    r = c.post("/api/m/home/uploads", params={"name": "../log.txt"}, headers=WRITE, content=b"hello")

    body = r.json()
    assert (r.status_code, body["name"], body["size"]) == (200, "log.txt", 5)
    assert Path(body["path"]).is_relative_to(tmp_path / "fleet" / "uploads")
    assert Path(body["path"]).read_bytes() == b"hello"


def test_upload_over_the_limit_is_refused_with_413(make_client: MakeClient) -> None:
    c = make_client()

    r = c.post("/api/uploads", params={"name": "big.bin"}, headers=WRITE,
               content=b"\0" * (MAX_UPLOAD_BYTES + 1))

    assert r.status_code == 413


def test_upload_for_a_peer_is_forwarded_with_token_header_and_bytes(make_client: MakeClient) -> None:
    seen: list[tuple[str, str, str | None, str | None, bytes]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.path, req.url.params["name"], req.headers.get("authorization"),
                     req.headers.get("x-fleet-request"), req.content))
        saved = {"path": "/Users/w/.fleet/uploads/x-a.png", "name": "a.png", "size": 3}
        return httpx.Response(200, json=saved)

    c = make_client(peers=(WORK,), peer_handler=handler)

    r = c.post("/api/m/work/uploads", params={"name": "a.png"}, headers=WRITE, content=b"PNG")

    assert (r.status_code, r.json()["path"]) == (200, "/Users/w/.fleet/uploads/x-a.png")
    assert seen == [("/api/uploads", "a.png", f"Bearer {TOKEN}", "1", b"PNG")]


def test_upload_for_an_old_peer_explains_it_needs_updating(make_client: MakeClient) -> None:
    c = make_client(peers=(WORK,), peer_handler=lambda r: httpx.Response(404, json={"detail": "Not Found"}))

    r = c.post("/api/m/work/uploads", params={"name": "a.png"}, headers=WRITE, content=b"PNG")

    assert r.status_code == 404 and "bản cũ" in r.json()["detail"]


def test_upload_for_an_unknown_machine_is_404(make_client: MakeClient) -> None:
    c = make_client()

    r = c.post("/api/m/nope/uploads", params={"name": "a.png"}, headers=WRITE, content=b"PNG")

    assert r.status_code == 404
