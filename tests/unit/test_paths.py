"""security.paths(C2.13·C2.18·C2.19) 단위 테스트. 앱 루트는 임시 폴더로 바꿔 쓴다."""

import ctypes
import hashlib
import os
import re
import sqlite3
import stat
import sys
from datetime import UTC, datetime
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
    return ("P" in head) is protected and {sid for _, _, _, sid in entries} == {
        user,
        paths.SYSTEM_SID,
    }


def _own(*folders):
    """테스트가 미리 만든 폴더의 소유자를 현재 사용자로 적는다.

    관리자 권한으로 실행하면(CI 러너) `mkdir`로 만든 폴더의 기본 소유자는 Administrators라서,
    "이전 실행이 남긴 앱 폴더"를 흉내 내려면 소유자를 명시해야 한다. `SetNamedSecurityInfoW`는
    소유자만 바꿔도 상속 ACE를 다시 계산해 DACL을 바꾸므로, DACL을 건드리지 않는
    `SetFileSecurityW`를 쓰고 이미 현재 사용자면 아무것도 하지 않는다.
    """
    user = paths._current_user_sid()
    set_file_security = ctypes.WinDLL("advapi32", use_last_error=True).SetFileSecurityW
    set_file_security.restype = ctypes.c_int
    set_file_security.argtypes = (ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_void_p)
    descriptor = ctypes.c_void_p()
    assert paths._ConvertStringSecurityDescriptorToSecurityDescriptorW(
        f"O:{user}", 1, ctypes.byref(descriptor), None
    )
    try:
        for folder in folders:
            if paths._owner_sid(folder) != user:
                assert set_file_security(str(folder), 0x1, descriptor)
    finally:
        paths._LocalFree(descriptor)


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


def test_db_file_lives_in_the_private_data_folder(folders, user):
    layout = folders.paths
    assert layout.db_file == layout.root / "data" / "guard.sqlite3"
    assert layout.backups == layout.root / "data" / "backups"
    assert layout.backups.is_dir()
    layout.db_file.write_bytes(b"SQLite format 3")
    (layout.data / "guard.sqlite3-wal").write_bytes(b"wal")
    assert _private(layout.db_file, user, protected=False)
    assert _private(layout.data / "guard.sqlite3-wal", user, protected=False)


def test_existing_root_with_wider_permissions_is_tightened(tmp_path, user):
    root = tmp_path / "app"
    (root / "config").mkdir(parents=True)
    _own(root, root / "config")
    (root / "config" / "old.json").write_text("{}", encoding="utf-8")
    # Everyone(WD) 읽기와 상속 허용 상태로 둔다.
    _apply_dacl(root, f"D:(A;OICI;FA;;;{user})(A;OICI;FR;;;WD)(A;OICI;FA;;;BA)")
    assert not _private(root, user, protected=True)
    prepared = prepare_app_folders(root)
    try:
        assert _private(root, user, protected=True)
        assert _private(root / "config", user, protected=True)
        assert _private(root / "config" / "old.json", user, protected=False)
        # 잠금 파일은 넓은 권한 아래에서 만들어진 뒤, 잠금 다음의 재적용으로 바로잡힌다.
        assert _private(root / paths.APP_LOCK_FILE, user, protected=False)
    finally:
        prepared.close()


def test_second_instance_does_not_touch_permissions(tmp_path, monkeypatch):
    """#35 B 리뷰 2번: 잠금을 못 잡은 실행은 DACL을 다시 걸지(하위 전파) 않는다."""
    root = tmp_path / "app"
    first = prepare_app_folders(root)
    calls = []
    original = paths._SetNamedSecurityInfoW

    def counting(*args):
        calls.append(args[0])
        return original(*args)

    monkeypatch.setattr(paths, "_SetNamedSecurityInfoW", counting)
    try:
        with pytest.raises(AppPathError) as raised:
            prepare_app_folders(root)
        assert str(raised.value) == "APP_ALREADY_RUNNING"
        assert calls == []
    finally:
        first.close()
    prepare_app_folders(root).close()
    assert str(root) in calls


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
    _own(root, tasks)
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
    _own(root, root / "tasks")
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
    _own(root)
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
        prepared.db_path,
        lambda: prepared.begin_backup("v2-to-v3"),
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


@pytest.mark.parametrize(
    ("alias", "sid"), [("SY", "S-1-5-18"), ("BA", "S-1-5-32-544"), ("WD", "S-1-1-0")]
)
def test_sddl_aliases_are_compared_as_full_sids(alias, sid):
    assert paths._canonical_sid(alias) == sid
    assert paths._canonical_sid(sid) == sid


def test_builtin_administrator_alias_counts_as_the_user(monkeypatch, tmp_path):
    """내장 Administrator(RID 500)의 SID는 SDDL에서 `LA`로 나온다(CI 러너)."""
    administrator = paths._canonical_sid("LA")
    assert administrator.endswith("-500")
    sddl = {
        paths._DACL_SECURITY_INFORMATION: "D:P(A;OICI;FA;;;LA)(A;OICI;FA;;;SY)",
        paths._OWNER_SECURITY_INFORMATION: "O:LA",
    }
    monkeypatch.setattr(paths, "_security_sddl", lambda path, information: sddl[information])
    assert paths._owner_sid(tmp_path) == administrator
    assert paths._is_private(tmp_path, administrator, protected=True)
    assert not paths._is_private(tmp_path, "S-1-5-21-1-2-3-1001", protected=True)


@pytest.mark.parametrize(
    "dacl",
    [
        'D:P(A;OICI;FA;;;{user})(XA;;FA;;;WD;(WIN://SYSAPPID Contains "x"))',
        "D:P(A;OICI;FA;;;{user})(A;OICI;FA;;;SY)garbage",
    ],
)
def test_unread_ace_text_is_not_ignored(monkeypatch, tmp_path, user, dacl):
    monkeypatch.setattr(paths, "_security_sddl", lambda path, information: dacl.format(user=user))
    with pytest.raises(AppPathError) as raised:
        paths._is_private(tmp_path, user, protected=True)
    assert str(raised.value) == "APP_FOLDER_PERMISSION_FAILED"


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


# C2.19: DB 경로와 이관 전 백업 입구(#37 SEQ-03·REC-01)


def _sqlite_db(path, *, version=2):
    connection = sqlite3.connect(path)
    try:
        with connection:
            connection.execute("CREATE TABLE IF NOT EXISTS t(x)")
            connection.execute("INSERT INTO t VALUES (1)")
            connection.execute(f"PRAGMA user_version = {version}")
    finally:
        connection.close()


def _check(path):
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        quick = connection.execute("PRAGMA quick_check").fetchone()[0]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    finally:
        connection.close()
    return quick, version


def _backup_into(source, target):
    src = sqlite3.connect(source)
    destination = sqlite3.connect(target)
    try:
        src.backup(destination)
    finally:
        destination.close()
        src.close()


def test_db_path_accepts_a_new_or_live_wal_database(folders):
    db = folders.db_path()
    assert db == folders.paths.db_file
    assert not db.exists()
    connection = sqlite3.connect(db)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE t(x)")
        connection.execute("INSERT INTO t VALUES (1)")
        connection.commit()
        assert (db.parent / "guard.sqlite3-wal").exists()
        assert folders.db_path() == db
    finally:
        connection.close()
    assert folders.db_path() == db


@pytest.mark.parametrize("kind", ["directory", "junction", "hardlink", "wide", "symlink"])
def test_db_path_refuses_files_outside_the_folder_rules(folders, tmp_path, user, kind):
    db = folders.paths.db_file
    if kind == "directory":
        (db.parent / "guard.sqlite3-wal").mkdir()
    elif kind == "junction":
        target = tmp_path / "elsewhere"
        target.mkdir()
        _junction(target, db)
    elif kind == "hardlink":
        db.write_bytes(b"SQLite format 3")
        os.link(db, tmp_path / "second-name.sqlite3")
    elif kind == "wide":
        db.write_bytes(b"SQLite format 3")
        _apply_dacl(db, f"D:(A;;FA;;;{user})(A;;FR;;;WD)")
    else:
        target = tmp_path / "elsewhere.sqlite3"
        target.write_bytes(b"SQLite format 3")
        try:
            db.symlink_to(target)
        except OSError:
            pytest.skip("심볼릭 링크를 만들 권한이 없다")
    with pytest.raises(AppPathError) as raised:
        folders.db_path()
    assert str(raised.value) == "APP_DB_INVALID"


def test_backup_round_trip_with_the_sqlite_backup_api(folders, user):
    _sqlite_db(folders.paths.db_file, version=2)
    target = folders.begin_backup("v2-to-v3")
    assert target.path.parent == folders.paths.backups
    assert re.fullmatch(r"v2-to-v3-\d{8}T\d{6}Z\.partial", target.path.name)
    assert target.path.read_bytes() == b""
    _backup_into(folders.paths.db_file, target.path)
    assert _check(target.path) == ("ok", 2)
    final = target.publish()
    assert final == target.path.with_suffix(".sqlite3")
    assert not target.path.exists()
    assert _check(final) == ("ok", 2)
    assert _private(final, user, protected=False)
    # publish 뒤의 discard는 완성된 백업을 지우지 않는다.
    target.discard()
    assert final.exists()
    with pytest.raises(AppPathError) as raised:
        target.publish()
    assert str(raised.value) == "APP_BACKUP_TARGET_CLOSED"


def test_publish_waits_for_the_backup_connection_to_close(folders):
    _sqlite_db(folders.paths.db_file)
    target = folders.begin_backup("v2-to-v3")
    src = sqlite3.connect(folders.paths.db_file)
    destination = sqlite3.connect(target.path)
    try:
        src.backup(destination)
        with pytest.raises(AppPathError) as raised:
            target.publish()
        assert str(raised.value) == "APP_BACKUP_PUBLISH_FAILED"
    finally:
        destination.close()
        src.close()
    # 실패한 publish는 대상을 닫지 않는다. 연결을 닫은 뒤 다시 부를 수 있다.
    assert _check(target.publish()) == ("ok", 2)


@pytest.mark.parametrize("case", ["empty", "journal-left", "final-exists"])
def test_publish_refuses_unfinished_or_clashing_backups(folders, case):
    target = folders.begin_backup("v2-to-v3")
    final = target.path.with_suffix(".sqlite3")
    if case != "empty":
        target.path.write_bytes(b"SQLite format 3 backup")
    if case == "journal-left":
        Path(f"{target.path}-journal").write_bytes(b"journal")
    elif case == "final-exists":
        final.write_bytes(b"older backup")
    with pytest.raises(AppPathError) as raised:
        target.publish()
    assert str(raised.value) == "APP_BACKUP_PUBLISH_FAILED"
    assert target.path.exists()
    if case == "final-exists":
        assert final.read_bytes() == b"older backup"
    else:
        assert not final.exists()


def test_discard_removes_the_partial_and_sqlite_side_files(folders):
    target = folders.begin_backup("v2-to-v3")
    target.path.write_bytes(b"partial")
    sides = [Path(f"{target.path}{suffix}") for suffix in ("-journal", "-wal", "-shm")]
    for side in sides:
        side.write_bytes(b"x")
    target.discard()
    assert not target.path.exists()
    assert not any(side.exists() for side in sides)
    target.discard()
    with pytest.raises(AppPathError) as raised:
        target.publish()
    assert str(raised.value) == "APP_BACKUP_TARGET_CLOSED"


def test_discard_reports_a_file_still_held_open(folders):
    target = folders.begin_backup("v2-to-v3")
    with target.path.open("rb"), pytest.raises(AppPathError) as raised:
        target.discard()
    assert str(raised.value) == "APP_BACKUP_DISCARD_FAILED"
    target.discard()
    assert not target.path.exists()


def test_backup_names_in_the_same_second_do_not_collide(folders, monkeypatch):
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 27, 1, 2, 3, tzinfo=UTC)

    monkeypatch.setattr(paths, "datetime", _Frozen)
    backups = folders.paths.backups
    (backups / "v2-to-v3-20261027T010203Z.sqlite3").write_bytes(b"done earlier")
    names = [folders.begin_backup("v2-to-v3").path.name for _ in range(8)]
    assert names == [f"v2-to-v3-20261027T010203Z-{n}.partial" for n in range(2, 10)]
    with pytest.raises(AppPathError) as raised:
        folders.begin_backup("v2-to-v3")
    assert str(raised.value) == "APP_BACKUP_CREATE_FAILED"


@pytest.mark.parametrize(
    "label",
    ["", "V2", "v2_to_v3", "-v2", "v2-", "v2--v3", "a/b", "..", "v2.v3", "x" * 33, 5, None],
)
def test_invalid_backup_labels_are_refused(folders, label):
    with pytest.raises(AppPathError) as raised:
        folders.begin_backup(label)
    assert str(raised.value) == "APP_BACKUP_LABEL_INVALID"
    assert list(folders.paths.backups.iterdir()) == []


def test_unfinished_backups_are_removed_at_start_and_finished_ones_kept(tmp_path):
    root = tmp_path / "app"
    first = prepare_app_folders(root)
    target = first.begin_backup("v2-to-v3")
    Path(f"{target.path}-journal").write_bytes(b"journal")
    backups = first.paths.backups
    (backups / "v1-to-v2-20261001T000000Z.sqlite3").write_bytes(b"finished")
    (backups / "notes.txt").write_text("keep", encoding="ascii")
    first.close()
    second = prepare_app_folders(root)
    try:
        assert second.stale_backups_removed == 2
        assert sorted(p.name for p in backups.iterdir()) == [
            "notes.txt",
            "v1-to-v2-20261001T000000Z.sqlite3",
        ]
    finally:
        second.close()


def test_unfinished_backup_that_cannot_be_removed_refuses_start(tmp_path):
    root = tmp_path / "app"
    first = prepare_app_folders(root)
    target = first.begin_backup("v2-to-v3")
    first.close()
    with target.path.open("rb"), pytest.raises(AppPathError) as raised:
        prepare_app_folders(root)
    assert str(raised.value) == "APP_STALE_BACKUP_CLEANUP_FAILED"
    # 실패한 시작은 잠금을 남기지 않는다.
    retried = prepare_app_folders(root)
    retried.close()
    assert not target.path.exists()


# C2.14: 설정 교체가 끊겨 남은 임시 파일 정리


def test_settings_temporaries_are_removed_at_start_and_settings_kept(tmp_path):
    root = tmp_path / "app"
    prepare_app_folders(root).close()
    config = root / "config"
    (config / "settings.json").write_bytes(b"{}")
    (config / f"settings.json.{'a' * 32}.tmp").write_bytes(b"half")
    (config / f"settings.json.{'b' * 32}.tmp").write_bytes(b"half")
    (config / "settings.json.bak").write_bytes(b"user file")
    (config / f"settings.json.{'g' * 32}.tmp").write_bytes(b"not ours")
    prepared = prepare_app_folders(root)
    try:
        assert prepared.stale_settings_removed == 2
        assert sorted(p.name for p in config.iterdir()) == [
            "settings.json",
            "settings.json.bak",
            f"settings.json.{'g' * 32}.tmp",
        ]
    finally:
        prepared.close()


def test_settings_temporary_that_cannot_be_removed_refuses_start(tmp_path):
    root = tmp_path / "app"
    prepare_app_folders(root).close()
    held = root / "config" / f"settings.json.{'c' * 32}.tmp"
    held.write_bytes(b"half")
    with held.open("rb"), pytest.raises(AppPathError) as raised:
        prepare_app_folders(root)
    assert str(raised.value) == "APP_STALE_SETTINGS_CLEANUP_FAILED"
    retried = prepare_app_folders(root)
    retried.close()
    assert not held.exists()


def test_is_private_file_reads_inherited_and_explicit_entries(folders, user):
    inherited = folders.paths.config / "inherited.json"
    inherited.write_bytes(b"{}")
    assert paths.is_private_file(inherited)
    widened = folders.paths.config / "widened.json"
    widened.write_bytes(b"{}")
    _apply_dacl(widened, f"D:(A;;FA;;;{user})(A;;FR;;;WD)")
    assert not paths.is_private_file(widened)
