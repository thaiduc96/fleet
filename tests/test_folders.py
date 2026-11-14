"""Folder picker: lists subfolders inside ~ only, here or on a peer."""

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from fleetd.actions import ActionError
from fleetd.config import Peer
from fleetd.folders import MAX_FOLDERS, list_folders
from tests.conftest import AUTH, TOKEN

MakeClient = Callable[..., TestClient]
WORK = Peer(name="work", url="https://work.ts.net", label="💼 Công ty")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    for d in ("code/app", "code/Beta", "code/.git", "Documents"):
        (tmp_path / "home" / d).mkdir(parents=True)
    (tmp_path / "home/code/notes.txt").write_text("x")
    return tmp_path / "home"


# ---------------------------------------------------------------- listing
def test_empty_path_lists_home_without_a_parent(home: Path) -> None:
    result = list_folders("", home)

    assert (result.path, result.parent, result.folders) == ("~", None, ["code", "Documents"])


def test_tilde_path_lists_visible_subfolders_sorted_ignoring_case(home: Path) -> None:
    result = list_folders("~/code", home)

    assert (result.path, result.parent, result.folders) == ("~/code", "~", ["app", "Beta"])


def test_absolute_path_inside_home_is_accepted(home: Path) -> None:
    assert list_folders(str(home / "code"), home).path == "~/code"


def test_link_pointing_outside_home_is_not_offered(home: Path, tmp_path: Path) -> None:
    (tmp_path / "outside").mkdir()
    (home / "code/escape").symlink_to(tmp_path / "outside")

    assert list_folders("~/code", home).folders == ["app", "Beta"]


@pytest.mark.parametrize(("raw", "status"), [("/", 400), ("~/../", 400), ("~/missing", 404),
                                             ("code", 400)])
def test_paths_that_cannot_be_a_session_folder_are_refused(home: Path, raw: str, status: int) -> None:
    with pytest.raises(ActionError) as e:
        list_folders(raw, home)

    assert e.value.status == status


def test_huge_folder_is_cut_and_marked_truncated(home: Path) -> None:
    for i in range(MAX_FOLDERS + 1):
        (home / "Documents" / f"d{i:04}").mkdir()

    result = list_folders("~/Documents", home)

    assert (len(result.folders), result.truncated) == (MAX_FOLDERS, True)


# ---------------------------------------------------------------- API
def test_folders_need_the_token(make_client: MakeClient) -> None:
    assert make_client().get("/api/m/home/folders").status_code == 401


def test_local_machine_lists_its_own_home(make_client: MakeClient) -> None:
    r = make_client().get("/api/m/home/folders", params={"path": "~"}, headers=AUTH)

    assert (r.status_code, r.json()) == (200, {"path": "~", "parent": None, "folders": ["proj"],
                                               "truncated": False})


def test_folder_outside_home_is_400(make_client: MakeClient) -> None:
    r = make_client().get("/api/folders", params={"path": "/etc"}, headers=AUTH)

    assert r.status_code == 400


def test_peer_folders_are_forwarded_with_path_and_token(make_client: MakeClient) -> None:
    seen: list[tuple[str, str, str | None]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.path, req.url.params["path"], req.headers.get("authorization")))
        return httpx.Response(200, json={"path": "~/a b", "parent": "~", "folders": ["x"]})

    c = make_client(peers=(WORK,), peer_handler=handler)

    r = c.get("/api/m/work/folders", params={"path": "~/a b"}, headers=AUTH)

    assert (r.status_code, r.json()["folders"]) == (200, ["x"])
    assert seen == [("/api/folders", "~/a b", f"Bearer {TOKEN}")]


def test_peer_without_folder_picker_explains_it_needs_updating(make_client: MakeClient) -> None:
    c = make_client(peers=(WORK,), peer_handler=lambda r: httpx.Response(404, json={"detail": "Not Found"}))

    r = c.get("/api/m/work/folders", headers=AUTH)

    assert r.status_code == 404 and "bản cũ" in r.json()["detail"]


def test_folders_for_an_unknown_machine_is_404(make_client: MakeClient) -> None:
    assert make_client().get("/api/m/nope/folders", headers=AUTH).status_code == 404
