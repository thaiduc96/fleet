"""Phase 4 (experimental): move a session's conversation to another Mac.

What a session is on disk (Claude Code 2.1.323):
  ~/.claude/projects/<enc(cwd)>/<sid>.jsonl        the transcript
  ~/.claude/projects/<enc(cwd)>/<sid>/...          subagents/, tool-results/
  ~/.claude/file-history/<sid>/...                 backups used by /rewind
where enc() replaces every non-alphanumeric character with "-".

Verified on 2026-11-13: a transcript written into the right project dir from
outside (no other metadata) resumes with its context via `claude --resume`.

The two Macs have different home dirs (/Users/alice vs /Users/bob), so
paths inside the files are rewritten from the source home to the target home.
Code does NOT travel: the repo must exist on the target at the same path
relative to ~, with the commits pushed/pulled; we only warn about that.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from fleetd.actions import ActionError, safe_dir
from fleetd.models import BundleFile, GitInfo, HandoffBundle, ImportBody, ImportResult
from fleetd.transcripts import UUID_RE, TranscriptIndex

MAX_BUNDLE_BYTES = 64 * 1024 * 1024
TRANSCRIPT = "transcript.jsonl"
SESSION_DIR = "session/"         # -> projects/<enc>/<sid>/
FILE_HISTORY = "file-history/"   # -> file-history/<sid>/

Git = Callable[..., str | None]


def encode_project(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def run_git(cwd: Path, *args: str) -> str | None:
    try:
        p = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                           timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def git_info(cwd: Path, git: Git = run_git) -> GitInfo | None:
    root = git(cwd, "rev-parse", "--show-toplevel")
    if root is None:
        return None
    status = git(cwd, "status", "--porcelain") or ""
    return GitInfo(root=root, remote=git(cwd, "remote", "get-url", "origin"),
                   branch=git(cwd, "rev-parse", "--abbrev-ref", "HEAD"),
                   head=git(cwd, "rev-parse", "HEAD"),
                   dirty=len([ln for ln in status.splitlines() if ln.strip()]))


def normalize_remote(url: str | None) -> str | None:
    """git@github.com:a/b.git and https://github.com/a/b -> github.com/a/b"""
    if not url:
        return None
    u = re.sub(r"^(https?://|ssh://)?(git@)?", "", url.strip())
    u = u.replace(":", "/", 1) if "/" not in u.split(":", 1)[0] and ":" in u else u
    return re.sub(r"\.git$", "", u).rstrip("/").lower()


def remap(text: str, src: str, dst: str) -> str:
    """Replace path prefix `src` by `dst` where it is a whole path component."""
    return remap_many(text, [(src, dst)])


def remap_many(text: str, pairs: list[tuple[str, str]]) -> str:
    """Several prefix replacements in ONE pass, longest prefix first.

    Sequential passes would rewrite text produced by an earlier pass (e.g. when
    the target home lies under the source home), so they are done together.
    """
    mapping = {src: dst for src, dst in pairs if src and src != dst}
    if not mapping:
        return text
    alternatives = "|".join(re.escape(s) for s in sorted(mapping, key=len, reverse=True))
    pattern = re.compile(f"(?:{alternatives})(?=[/\"\\\\\\s']|$)")
    return pattern.sub(lambda m: mapping[m.group(0)], text)


def _safe_rel(rel: str) -> PurePosixPath:
    p = PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise ActionError(400, f"đường dẫn không hợp lệ trong gói: {rel}")
    return p


class Handoff:
    def __init__(self, claude_home: Path, idx: TranscriptIndex, user_home: Path,
                 machine: str, git: Git = run_git, max_bytes: int = MAX_BUNDLE_BYTES) -> None:
        self.claude_home = claude_home
        self.idx = idx
        self.user_home = user_home
        self.machine = machine
        self.git = git
        self.max_bytes = max_bytes

    # ---- source side ---------------------------------------------------------
    def export(self, session_id: str, meta_only: bool = False) -> HandoffBundle:
        if not UUID_RE.match(session_id):
            raise ActionError(400, "bad session id")
        path = self.idx.find(session_id)
        if not path:
            raise ActionError(404, "không có transcript của session này trên máy nguồn")
        summary = self.idx.summary(path)
        if not summary.cwd:
            raise ActionError(409, "transcript không ghi thư mục làm việc (cwd)")
        sources: list[tuple[str, Path]] = [(TRANSCRIPT, path)]
        for base, prefix in ((path.with_suffix(""), SESSION_DIR),
                             (self.claude_home / "file-history" / session_id, FILE_HISTORY)):
            if base.is_dir():
                sources += [(prefix + f.relative_to(base).as_posix(), f)
                            for f in sorted(base.rglob("*")) if f.is_file()]
        size = sum(f.stat().st_size for _, f in sources)
        if size > self.max_bytes:
            raise ActionError(413, f"session quá lớn để chuyển ({size // 2**20} MB)")
        cwd = Path(summary.cwd)
        files = [] if meta_only else [
            BundleFile(rel=rel, data=base64.b64encode(f.read_bytes()).decode()) for rel, f in sources]
        return HandoffBundle(session_id=session_id, source_machine=self.machine,
                             home=str(self.user_home), cwd=summary.cwd, title=summary.title,
                             git=git_info(cwd, self.git) if cwd.is_dir() else None,
                             size=size, files=files)

    # ---- target side ---------------------------------------------------------
    def _trusted(self, cwd: Path) -> bool | None:
        try:
            data = json.loads((self.user_home / ".claude.json").read_text())
        except (OSError, ValueError):
            return None
        entry = (data.get("projects") or {}).get(str(cwd)) if isinstance(data, dict) else None
        return bool(entry and entry.get("hasTrustDialogAccepted"))

    def _git_warnings(self, src: GitInfo | None, cwd: Path) -> list[str]:
        warn: list[str] = []
        if src and src.dirty:
            warn.append(f"Máy nguồn còn {src.dirty} file chưa commit — các thay đổi đó KHÔNG được chuyển.")
        if not src or not src.head:
            return warn
        dst = git_info(cwd, self.git)
        if dst is None:
            warn.append("Thư mục đích không phải git repo, trong khi ở máy nguồn là repo.")
            return warn
        if normalize_remote(src.remote) != normalize_remote(dst.remote):
            warn.append(f"Remote khác nhau: nguồn {src.remote or '—'}, đích {dst.remote or '—'}.")
        if self.git(cwd, "cat-file", "-e", f"{src.head}^{{commit}}") is None:
            warn.append(f"Máy đích chưa có commit {src.head[:7]} (nhánh {src.branch}): "
                        "hãy push ở máy nguồn rồi git pull ở đây.")
        elif dst.head != src.head:
            warn.append(f"Máy đích đang ở {(dst.head or '')[:7]} ({dst.branch}), "
                        f"session dừng ở {src.head[:7]} ({src.branch}).")
        return warn

    def import_(self, body: ImportBody) -> ImportResult:
        b = body.bundle
        if not UUID_RE.match(b.session_id):
            raise ActionError(400, "bad session id")
        src_home, dst_home = b.home.rstrip("/"), str(self.user_home).rstrip("/")
        raw_cwd = body.target_cwd or remap(b.cwd, src_home, dst_home)
        try:
            cwd = safe_dir(raw_cwd, self.user_home)
        except ActionError as e:
            if e.status == 404:
                raise ActionError(404, f"Máy này chưa có thư mục {raw_cwd}: clone/pull repo trước "
                                       "(cùng đường dẫn tính từ ~).") from e
            raise
        if self.idx.find(b.session_id) and not body.overwrite:
            raise ActionError(409, "Máy này đã có transcript của session này — chọn 'Ghi đè' để thay "
                                   "bằng bản từ máy nguồn.")
        warnings = self._git_warnings(b.git, cwd)
        if self._trusted(cwd) is False:
            warnings.append(f"Thư mục {cwd} chưa được trust trên máy này: mở `claude` ở đó một lần "
                            "trước khi tiếp tục session.")
        result = ImportResult(session_id=b.session_id, cwd=str(cwd), dry_run=body.dry_run, warnings=warnings)
        if body.dry_run:
            return result
        if not any(f.rel == TRANSCRIPT for f in b.files):
            raise ActionError(400, "gói không có transcript")
        project = self.claude_home / "projects" / encode_project(str(cwd))
        for f in b.files:
            dest = self._dest(_safe_rel(f.rel), project, b.session_id)
            data = base64.b64decode(f.data)
            if f.rel == TRANSCRIPT or f.rel.startswith(SESSION_DIR):
                data = self._rewrite(data, b.cwd, str(cwd), src_home, dst_home)
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".fleet-tmp")
            tmp.write_bytes(data)
            tmp.replace(dest)
            result.written.append(str(dest))
        return result

    def _dest(self, rel: PurePosixPath, project: Path, sid: str) -> Path:
        s = rel.as_posix()
        if s == TRANSCRIPT:
            return project / f"{sid}.jsonl"
        if s.startswith(SESSION_DIR):
            return project / sid / s[len(SESSION_DIR):]
        if s.startswith(FILE_HISTORY):
            return self.claude_home / "file-history" / sid / s[len(FILE_HISTORY):]
        raise ActionError(400, f"loại file không hỗ trợ trong gói: {s}")

    @staticmethod
    def _rewrite(data: bytes, src_cwd: str, dst_cwd: str, src_home: str, dst_home: str) -> bytes:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return data
        # the exact working dir (may be remapped to a custom target) and any other path under ~
        return remap_many(text, [(src_cwd, dst_cwd), (src_home, dst_home)]).encode("utf-8")
