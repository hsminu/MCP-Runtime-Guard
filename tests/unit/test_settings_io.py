"""security.settings_io(C2.14, CFG-01) 단위 테스트. 앱 루트는 임시 폴더로 바꿔 쓴다."""

import ctypes
import hashlib
import os
import sys

import pytest

from guard_app.security import paths, settings_io
from guard_app.security.paths import AppPathError, prepare_app_folders
from guard_app.security.settings_io import (
    SettingsGeneration,
    SettingsIOError,
    read_settings_file,
    replace_settings_file,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="설정 파일 연산은 Windows 전용이다")

LIMIT = 4096
FIRST = b'{"schema_version": 1, "settings": {"first": true}}\n'
SECOND = b'{"schema_version": 1, "settings": {"second": true}}\n'


@pytest.fixture
def folders(tmp_path):
    prepared = prepare_app_folders(tmp_path / "app")
    yield prepared
    prepared.close()


def _code(raised):
    return str(raised.value)


def _leftovers(folders):
    return sorted(p.name for p in folders.paths.config.iterdir() if p.name != "settings.json")


def _apply_dacl(path, sddl):
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


def test_missing_file_means_first_run(folders):
    assert read_settings_file(folders, max_bytes=LIMIT) is None


def test_read_returns_bytes_and_their_generation(folders):
    folders.paths.settings_file.write_bytes(FIRST)
    snapshot = read_settings_file(folders, max_bytes=LIMIT)
    assert snapshot.data == FIRST
    assert snapshot.generation == SettingsGeneration(len(FIRST), hashlib.sha256(FIRST).hexdigest())
    assert "first" not in repr(snapshot)


def test_read_stops_one_byte_past_the_limit(folders):
    folders.paths.settings_file.write_bytes(b"x" * (LIMIT + 50))
    snapshot = read_settings_file(folders, max_bytes=LIMIT)
    assert len(snapshot.data) == LIMIT + 1
    assert snapshot.generation.size == LIMIT + 50


@pytest.mark.parametrize("kind", ["directory", "junction", "hardlink", "wide", "symlink"])
def test_unusable_settings_file_is_refused(folders, tmp_path, kind):
    import _winapi

    target = folders.paths.settings_file
    if kind == "directory":
        target.mkdir()
    elif kind == "junction":
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        _winapi.CreateJunction(str(elsewhere), str(target))
    elif kind == "hardlink":
        target.write_bytes(FIRST)
        os.link(target, tmp_path / "second-name.json")
    elif kind == "wide":
        target.write_bytes(FIRST)
        _apply_dacl(target, f"D:(A;;FA;;;{paths._current_user_sid()})(A;;FR;;;WD)")
    else:
        elsewhere = tmp_path / "elsewhere.json"
        elsewhere.write_bytes(FIRST)
        try:
            target.symlink_to(elsewhere)
        except OSError:
            pytest.skip("심볼릭 링크를 만들 권한이 없다")
    with pytest.raises(SettingsIOError) as raised:
        read_settings_file(folders, max_bytes=LIMIT)
    assert _code(raised) == "SETTINGS_FILE_INVALID"


def test_file_open_for_writing_elsewhere_is_not_read(folders):
    folders.paths.settings_file.write_bytes(FIRST)
    with folders.paths.settings_file.open("r+b"), pytest.raises(SettingsIOError) as raised:
        read_settings_file(folders, max_bytes=LIMIT)
    assert _code(raised) == "SETTINGS_FILE_READ_FAILED"


def test_first_save_then_chained_saves(folders):
    first = replace_settings_file(folders, FIRST, expected=None, max_bytes=LIMIT)
    assert folders.paths.settings_file.read_bytes() == FIRST
    assert read_settings_file(folders, max_bytes=LIMIT).generation == first
    second = replace_settings_file(folders, SECOND, expected=first, max_bytes=LIMIT)
    assert folders.paths.settings_file.read_bytes() == SECOND
    assert second != first
    head, entries = paths._dacl_entries(folders.paths.settings_file)
    assert {sid for _, _, _, sid in entries} == {paths._current_user_sid(), paths.SYSTEM_SID}
    assert _leftovers(folders) == []


@pytest.mark.parametrize("case", ["edited", "appeared", "deleted"])
def test_file_changed_since_it_was_read_is_not_overwritten(folders, case):
    target = folders.paths.settings_file
    expected = None
    if case != "appeared":
        expected = replace_settings_file(folders, FIRST, expected=None, max_bytes=LIMIT)
    if case == "edited":
        target.write_bytes(b'{"edited": "by hand"}')
    elif case == "appeared":
        target.write_bytes(b'{"made": "elsewhere"}')
    else:
        target.unlink()
    with pytest.raises(SettingsIOError) as raised:
        replace_settings_file(folders, SECOND, expected=expected, max_bytes=LIMIT)
    assert _code(raised) == "SETTINGS_FILE_CHANGED"
    if case == "deleted":
        assert not target.exists()
    else:
        assert target.read_bytes() != SECOND
    assert _leftovers(folders) == []


def test_target_held_open_fails_without_changing_it(folders):
    current = replace_settings_file(folders, FIRST, expected=None, max_bytes=LIMIT)
    # 다른 핸들이 열고 있으면 교체가 실제로 실패한다(삭제 공유 없음).
    with folders.paths.settings_file.open("rb"), pytest.raises(SettingsIOError) as raised:
        replace_settings_file(folders, SECOND, expected=current, max_bytes=LIMIT)
    assert _code(raised) == "SETTINGS_FILE_WRITE_FAILED"
    assert folders.paths.settings_file.read_bytes() == FIRST
    assert _leftovers(folders) == []


def test_flush_failure_leaves_the_file_and_no_temporary(folders, monkeypatch):
    current = replace_settings_file(folders, FIRST, expected=None, max_bytes=LIMIT)

    def fail(descriptor):
        raise OSError("disk")

    monkeypatch.setattr(settings_io.os, "fsync", fail)
    with pytest.raises(SettingsIOError) as raised:
        replace_settings_file(folders, SECOND, expected=current, max_bytes=LIMIT)
    assert _code(raised) == "SETTINGS_FILE_WRITE_FAILED"
    assert folders.paths.settings_file.read_bytes() == FIRST
    assert _leftovers(folders) == []


@pytest.mark.parametrize(
    ("after_move", "code", "content"),
    [
        ("nothing", "SETTINGS_FILE_WRITE_FAILED", FIRST),
        ("moved", "SETTINGS_FILE_WRITE_UNKNOWN", SECOND),
        ("other", "SETTINGS_FILE_CHANGED", b'{"other": 1}'),
    ],
)
def test_failed_move_is_classified_by_what_is_on_disk(
    folders, monkeypatch, after_move, code, content
):
    current = replace_settings_file(folders, FIRST, expected=None, max_bytes=LIMIT)
    real_move = settings_io._MoveFileExW
    sources = []

    def failing_move(source, target, flags):
        sources.append(os.path.basename(source))
        if after_move == "moved":
            real_move(source, target, flags)
        elif after_move == "other":
            with open(target, "wb") as other:
                other.write(b'{"other": 1}')
        return 0

    monkeypatch.setattr(settings_io, "_MoveFileExW", failing_move)
    with pytest.raises(SettingsIOError) as raised:
        replace_settings_file(folders, SECOND, expected=current, max_bytes=LIMIT)
    assert _code(raised) == code
    assert folders.paths.settings_file.read_bytes() == content
    assert len(sources) == 1
    assert paths.SETTINGS_TEMP.fullmatch(sources[0])
    assert _leftovers(folders) == []


@pytest.mark.parametrize("data", ["text", b"", b"x" * (LIMIT + 1), None])
def test_invalid_data_is_refused(folders, data):
    with pytest.raises(SettingsIOError) as raised:
        replace_settings_file(folders, data, expected=None, max_bytes=LIMIT)
    assert _code(raised) == "SETTINGS_FILE_DATA_INVALID"
    assert not folders.paths.settings_file.exists()


def test_expected_must_be_a_generation(folders):
    with pytest.raises(SettingsIOError) as raised:
        replace_settings_file(folders, FIRST, expected=("size", "hash"), max_bytes=LIMIT)
    assert _code(raised) == "SETTINGS_FILE_DATA_INVALID"


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, None])
def test_invalid_limit_is_refused(folders, limit):
    for action in (
        lambda: read_settings_file(folders, max_bytes=limit),
        lambda: replace_settings_file(folders, FIRST, expected=None, max_bytes=limit),
    ):
        with pytest.raises(SettingsIOError) as raised:
            action()
        assert _code(raised) == "SETTINGS_FILE_LIMIT_INVALID"


def test_closed_folders_refuse_both(tmp_path):
    prepared = prepare_app_folders(tmp_path / "app")
    prepared.close()
    for action in (
        lambda: read_settings_file(prepared, max_bytes=LIMIT),
        lambda: replace_settings_file(prepared, FIRST, expected=None, max_bytes=LIMIT),
    ):
        with pytest.raises(AppPathError) as raised:
            action()
        assert _code(raised) == "APP_FOLDERS_CLOSED"


def test_errors_carry_no_path_or_content(folders):
    folders.paths.settings_file.write_bytes(b'{"marker-settings-7f3a": 1}')
    with pytest.raises(SettingsIOError) as raised:
        replace_settings_file(folders, SECOND, expected=None, max_bytes=LIMIT)
    assert _code(raised) == "SETTINGS_FILE_CHANGED"
    assert "marker" not in repr(raised.value)
    assert "settings.json" not in repr(raised.value)
