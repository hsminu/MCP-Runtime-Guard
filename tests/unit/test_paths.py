"""security.paths(C2.13) 단위 테스트. 앱 루트는 임시 폴더로 바꿔 쓴다."""

import ctypes
import hashlib
import os
import stat
import sys
from pathlib import Path

import pytest

from guard_app.security import paths
from guard_app.security.paths import MANAGED_DIRS, AppPathError, AppPaths, prepare_app_folders

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="앱 폴더 권한은 Windows 전용이다")


@pytest.fixture
def user():
    return paths._current_user_sid()


@pytest.fixture
def folders(tmp_path):
    prepared = prepare_app_folders(tmp_path / "app")
    yield prepared
    prepared.close()


def _private(path, user, *, protected):
    head, entries = paths._dacl_entries(path)
    return ("P" in head) is protected and {sid for _, _, _, sid in entries} == {user, "SY"}


def _apply_dacl(path, sddl):
    """테스트 준비용. 상속을 막지 않은 DACL을 직접 건다."""
    descriptor = ctypes.c_void_p()
    assert paths._ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), None
    )
    present, dacl, defaulted = ctypes.c_int(), ctypes.c_void_p(), ctypes.c_int()
    try:
        assert paths._GetSecurityDescriptorDacl(
            descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
        )
        assert paths._SetNamedSecurityInfoW(str(path), 1, 0x4, None, None, dacl, None) == 0
    finally:
        paths._LocalFree(descriptor)


def _junction(target, link):
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


def test_app_root_is_under_local_app_data():
    root = paths.app_root()
    assert root.name == "MCPRuntimeGuard"
    assert str(root.parent).casefold() == os.environ["LOCALAPPDATA"].casefold()


def test_prepare_creates_private_layout(tmp_path, user):
    root = tmp_path / "app"
    prepared = prepare_app_folders(root)
    try:
        assert prepared.paths == AppPaths(root)
        assert prepared.stale_removed == 0
        for folder in (root, *(root / name for name in MANAGED_DIRS)):
            assert folder.is_dir()
            assert _private(folder, user, protected=True)
            assert paths._owner_sid(folder) == user
        assert (root / paths.APP_LOCK_FILE).is_file()
    finally:
        prepared.close()


def test_files_in_task_folder_inherit_only_user_and_system(folders, user):
    task = folders.create_task_folder("session-0123abcd")
    (task / "logs_2.sqlite").write_bytes(b"raw")
    assert _private(task, user, protected=False)
    assert _private(task / "logs_2.sqlite", user, protected=False)


def test_existing_root_with_wider_permissions_is_tightened(tmp_path, user):
    root = tmp_path / "app"
    (root / "config").mkdir(parents=True)
    (root / "config" / "old.json").write_text("{}", encoding="utf-8")
    # Everyone(WD) 읽기와 상속 허용 상태로 둔다.
    _apply_dacl(root, f"D:(A;OICI;FA;;;{user})(A;OICI;FR;;;WD)(A;OICI;FA;;;BA)")
    assert not _private(root, user, protected=True)
    prepared = prepare_app_folders(root)
    try:
        assert _private(root, user, protected=True)
        assert _private(root / "config", user, protected=True)
        assert _private(root / "config" / "old.json", user, protected=False)
    finally:
        prepared.close()


def test_second_instance_is_refused_until_first_closes(tmp_path):
    root = tmp_path / "app"
    first = prepare_app_folders(root)
    with pytest.raises(AppPathError) as raised:
        prepare_app_folders(root)
    assert str(raised.value) == "APP_ALREADY_RUNNING"
    first.close()
    first.close()
    prepare_app_folders(root).close()


def test_stale_task_folders_are_removed_without_following_links(tmp_path):
    root = tmp_path / "app"
    tasks = root / "tasks"
    old = tasks / "session-old"
    old.mkdir(parents=True)
    (old / "logs_2.sqlite-wal").write_bytes(b"raw tool result")
    readonly = old / "readonly.txt"
    readonly.write_text("x", encoding="ascii")
    os.chmod(readonly, stat.S_IREAD)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="ascii")
    _junction(outside, old / "nested-link")
    _junction(outside, tasks / "top-link")
    (tasks / "stray.txt").write_text("x", encoding="ascii")
    prepared = prepare_app_folders(root)
    try:
        assert prepared.stale_removed == 3
        assert list(tasks.iterdir()) == []
        assert (outside / "keep.txt").read_text(encoding="ascii") == "keep"
    finally:
        prepared.close()


def test_stale_task_folder_that_cannot_be_removed_refuses_start(tmp_path):
    root = tmp_path / "app"
    old = root / "tasks" / "session-old"
    old.mkdir(parents=True)
    held = old / "logs_2.sqlite"
    held.write_bytes(b"raw")
    with held.open("rb"), pytest.raises(AppPathError) as raised:
        prepare_app_folders(root)
    assert str(raised.value) == "APP_STALE_TASK_CLEANUP_FAILED"
    # 실패한 시작은 잠금을 남기지 않는다.
    retried = prepare_app_folders(root)
    retried.close()
    assert not old.exists()


@pytest.mark.parametrize("kind", ["junction", "file", "relative"])
def test_unusable_root_is_refused(tmp_path, kind):
    if kind == "junction":
        target = tmp_path / "target"
        target.mkdir()
        root = tmp_path / "app"
        _junction(target, root)
    elif kind == "file":
        root = tmp_path / "app"
        root.write_text("x", encoding="ascii")
    else:
        root = Path("app")
    with pytest.raises(AppPathError) as raised:
        prepare_app_folders(root)
    assert str(raised.value) == "APP_ROOT_INVALID"


def test_root_owned_by_another_user_is_refused(tmp_path, monkeypatch):
    root = tmp_path / "app"
    root.mkdir()
    monkeypatch.setattr(paths, "_current_user_sid", lambda: "S-1-5-21-1-2-3-1001")
    with pytest.raises(AppPathError) as raised:
        prepare_app_folders(root)
    assert str(raised.value) == "APP_ROOT_NOT_OWNED"


def test_managed_folder_that_is_a_link_is_refused(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    target = tmp_path / "elsewhere"
    target.mkdir()
    _junction(target, root / "codex_home")
    with pytest.raises(AppPathError) as raised:
        prepare_app_folders(root)
    assert str(raised.value) == "APP_FOLDER_INVALID"
    # 실패한 시작은 잠금을 남기지 않는다.
    (root / "codex_home").rmdir()
    prepare_app_folders(root).close()


@pytest.mark.parametrize(
    "task_id",
    ["", "..", "a/b", "a\\b", "a.b", "CON", "lpt1", "Com0", "-x", "_x", " x", "x" * 129, 5, None],
)
def test_invalid_task_ids_are_refused(folders, task_id):
    with pytest.raises(AppPathError) as raised:
        folders.create_task_folder(task_id)
    assert str(raised.value) == "TASK_ID_INVALID"


def test_task_folder_is_never_reused(folders):
    folders.create_task_folder("session-1")
    with pytest.raises(AppPathError) as raised:
        folders.create_task_folder("session-1")
    assert str(raised.value) == "TASK_FOLDER_EXISTS"


def test_remove_deletes_task_folder_with_read_only_and_nested_files(folders, tmp_path):
    task = folders.create_task_folder("session-2")
    (task / "sub").mkdir()
    (task / "sub" / "state_5.sqlite-shm").write_bytes(b"x")
    readonly = task / "readonly.txt"
    readonly.write_text("x", encoding="ascii")
    os.chmod(readonly, stat.S_IREAD)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="ascii")
    _junction(outside, task / "link")
    folders.remove_task_folder(task)
    assert not task.exists()
    assert (outside / "keep.txt").exists()


def test_remove_refuses_paths_outside_tasks_and_links(folders, tmp_path):
    other = tmp_path / "other" / "session-3"
    other.mkdir(parents=True)
    nested = folders.create_task_folder("session-4") / "inner"
    nested.mkdir()
    link = folders.paths.tasks / "session-5"
    _junction(other, link)
    for target in (other, folders.paths.codex_home, nested, link, folders.paths.tasks / "x.y"):
        with pytest.raises(AppPathError) as raised:
            folders.remove_task_folder(target)
        assert str(raised.value) == "TASK_FOLDER_INVALID"
    assert other.exists()
    assert nested.exists()


def test_remove_reports_a_file_still_held_open(folders):
    task = folders.create_task_folder("session-6")
    held = task / "logs_2.sqlite"
    held.write_bytes(b"raw")
    with held.open("rb"), pytest.raises(AppPathError) as raised:
        folders.remove_task_folder(task)
    assert str(raised.value) == "TASK_FOLDER_CLEANUP_FAILED"
    folders.remove_task_folder(task)
    assert not task.exists()


def test_closed_folders_refuse_use(tmp_path):
    prepared = prepare_app_folders(tmp_path / "app")
    task = prepared.create_task_folder("session-7")
    prepared.close()
    for action in (
        lambda: prepared.create_task_folder("session-8"),
        lambda: prepared.remove_task_folder(task),
    ):
        with pytest.raises(AppPathError) as raised:
            action()
        assert str(raised.value) == "APP_FOLDERS_CLOSED"


@pytest.mark.parametrize("version", ["0.162", "0.162.1-win", "../1.2.3", "", 1])
def test_codex_install_path_needs_a_plain_version(tmp_path, version):
    assert AppPaths(tmp_path).codex_install("0.162.1") == tmp_path / "codex" / "0.162.1"
    with pytest.raises(AppPathError) as raised:
        AppPaths(tmp_path).codex_install(version)
    assert str(raised.value) == "APP_CODEX_VERSION_INVALID"


def test_errors_do_not_carry_paths(tmp_path):
    marker = tmp_path / "marker-app-root-7f3a"
    marker.write_text("x", encoding="ascii")
    with pytest.raises(AppPathError) as raised:
        prepare_app_folders(marker)
    assert "marker" not in str(raised.value)


@pytest.mark.asyncio
async def test_task_folder_lifecycle_with_the_launcher(folders):
    from guard_app.security.launcher import LaunchSpec, launch_process

    python = Path(sys.base_prefix) / "python.exe"
    data = python.read_bytes()
    task = folders.create_task_folder("session-9")
    code = "open('logs_2.sqlite', 'wb').write(b'raw'); import sys; sys.stdin.read()"
    launched = await launch_process(
        LaunchSpec(
            executable=python,
            executable_size=len(data),
            executable_sha256=hashlib.sha256(data).hexdigest(),
            args=("-I", "-c", code),
            env=(("SYSTEMROOT", os.environ["SYSTEMROOT"]),),
            cwd=task,
            stderr_path=task / "stderr.log",
        )
    )
    assert await launched.close() is True
    assert (task / "logs_2.sqlite").read_bytes() == b"raw"
    folders.remove_task_folder(task)
    assert not task.exists()
