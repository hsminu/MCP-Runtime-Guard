"""SEQ-03 schema 준비, 검증된 백업과 시작 게이트의 단위 검증."""

import asyncio
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from guard_app.domain.enums import CommitOutcome, StorePhase, StorePrepareStep, StoreStartFailure
from guard_app.domain.errors import StoreError, StorePrepareError
from guard_app.domain.reasons import Reason
from guard_app.storage import SQLiteStore
from guard_app.storage import sqlite_store as store_module
from tests.unit.test_registry_store import legacy_database
from tests.unit.test_store_v3_migration import _v2_database


class _Backup:
    def __init__(self, folder, *, fail_publish=False, fail_discard=False):
        self.path = folder / "backup.partial"
        self.path.touch(exist_ok=False)
        self.final = folder / "backup.sqlite3"
        self.fail_publish = fail_publish
        self.fail_discard = fail_discard
        self.published = False
        self.discarded = False

    def publish(self):
        if self.fail_publish:
            raise OSError("PRIVATE_PUBLISH_ERROR")
        self.path.rename(self.final)
        self.published = True
        return self.final

    def discard(self):
        self.discarded = True
        if self.fail_discard:
            raise OSError("PRIVATE_DISCARD_ERROR")
        self.path.unlink(missing_ok=True)


class _Backups:
    def __init__(self, folder, source, **target_kwargs):
        self.folder = folder
        self.source = source
        self.target_kwargs = target_kwargs
        self.calls = []
        self.source_bytes_at_call = None
        self.target = None

    def begin(self, label):
        self.calls.append((label, threading.current_thread().name))
        self.source_bytes_at_call = self.source.read_bytes()
        self.target = _Backup(self.folder, **self.target_kwargs)
        return self.target


async def _close(store):
    store.close()
    assert await store.wait_closed(5)


async def _prepare(store, backups, steps, *, deadline=None):
    return await store.prepare_schema(
        deadline=time.monotonic() + 10 if deadline is None else deadline,
        begin_backup=backups.begin,
        on_step=steps.append,
    )


def _state_for_result(store):
    state = store.execution_state()
    assert state.phase is StorePhase.RECOVERING
    assert not state.execution_enabled


def test_empty_explicit_store_creates_v3_without_backup_and_remains_closed_to_execution(tmp_path):
    async def scenario():
        path = tmp_path / "new.db"
        store = SQLiteStore(path, startup="explicit")
        backups, steps = _Backups(tmp_path, path), []
        try:
            report = await _prepare(store, backups, steps)
            assert (report.from_version, report.to_version) == (0, 3)
            assert not report.backup_published
            assert report.migrated
            assert steps == [
                StorePrepareStep.STORE_CHECK,
                StorePrepareStep.STORE_MIGRATE,
                StorePrepareStep.STORE_VERIFY,
            ]
            assert backups.calls == []
            _state_for_result(store)
            with pytest.raises(StoreError):
                await store.load_task("task")
            with pytest.raises(StoreError):
                await store.list_registrations()
            with pytest.raises(StoreError):
                await _prepare(store, backups, steps)
        finally:
            await _close(store)

    asyncio.run(scenario())


def test_exact_v3_prepare_does_not_write_schema_or_start_backup(tmp_path):
    path = tmp_path / "existing.db"
    with SQLiteStore(path):
        pass
    before = path.read_bytes()

    async def scenario():
        store = SQLiteStore(path, startup="explicit")
        backups, steps = _Backups(tmp_path, path), []
        try:
            report = await _prepare(store, backups, steps)
            assert report.from_version == report.to_version == 3
            assert not report.backup_published
            assert not report.migrated
            assert backups.calls == []
            assert steps == [StorePrepareStep.STORE_CHECK, StorePrepareStep.STORE_VERIFY]
            assert path.read_bytes() == before
            _state_for_result(store)
        finally:
            await _close(store)

    asyncio.run(scenario())
    assert path.read_bytes() == before


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_backup_is_published_before_wal_and_migration(tmp_path, version):
    path = tmp_path / f"legacy-v{version}.db"
    (legacy_database if version == 1 else _v2_database)(path)
    before = path.read_bytes()

    async def scenario():
        store = SQLiteStore(path, startup="explicit")
        backups, steps, callback_threads = _Backups(tmp_path, path), [], []
        try:

            def on_step(step):
                callback_threads.append(threading.get_ident())
                steps.append(step)

            report = await store.prepare_schema(
                deadline=time.monotonic() + 10, begin_backup=backups.begin, on_step=on_step
            )
            assert report.from_version == version
            assert report.to_version == 3
            assert report.backup_published
            assert report.migrated
            assert steps == [
                StorePrepareStep.STORE_CHECK,
                StorePrepareStep.STORE_BACKUP,
                StorePrepareStep.STORE_MIGRATE,
                StorePrepareStep.STORE_VERIFY,
            ]
            assert all(ident == threading.get_ident() for ident in callback_threads)
            assert backups.calls == [(f"v{version}-to-v3", "sqlite-store-writer")]
            assert backups.source_bytes_at_call == before
            assert backups.target is not None
            assert backups.target.published
            with sqlite3.connect(backups.target.final) as backup:
                assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"
                assert backup.execute("PRAGMA user_version").fetchone()[0] == version
            _state_for_result(store)
        finally:
            await _close(store)

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["v1_registry", "future", "application", "ddl", "foreign_key"])
def test_check_rejects_unsupported_or_corrupt_schema_without_modifying_bytes(tmp_path, case):
    path = tmp_path / "original.db"
    (legacy_database if case == "v1_registry" else _v2_database)(path)
    with sqlite3.connect(path) as connection:
        if case == "v1_registry":
            connection.execute(
                "INSERT INTO registrations VALUES (?,?,?,?,?,?,?,?)",
                ("server", "pending", "internal", 0, "{}", None, None, 0),
            )
        elif case == "future":
            connection.execute("PRAGMA user_version=99")
        elif case == "application":
            connection.execute("PRAGMA application_id=0")
        elif case == "ddl":
            connection.execute("ALTER TABLE tasks ADD COLUMN extra TEXT")
        else:
            connection.execute(
                "INSERT INTO escalations VALUES (?, ?, ?, ?, ?)",
                ("missing-task", 0, "source", 0, 1),
            )
    before = path.read_bytes()

    async def scenario():
        store = SQLiteStore(path, startup="explicit")
        backups, steps = _Backups(tmp_path, path), []
        try:
            with pytest.raises(StorePrepareError) as error:
                await _prepare(store, backups, steps)
            expected = (
                StoreStartFailure.STORE_INTEGRITY_FAILED
                if case in {"ddl", "foreign_key"}
                else StoreStartFailure.STORE_SCHEMA_UNSUPPORTED
            )
            assert error.value.code is expected
            assert steps == [StorePrepareStep.STORE_CHECK]
            assert not backups.calls
            assert store.execution_state().phase is StorePhase.FAULTED
            assert not store.execution_state().execution_enabled
            assert store.execution_state().requires_restart
            assert path.read_bytes() == before
            with pytest.raises(StoreError):
                await _prepare(store, backups, steps)
        finally:
            await _close(store)

    asyncio.run(scenario())
    assert path.read_bytes() == before


def test_unsupported_wal_database_remains_untouched_after_prepare_failure_and_close(tmp_path):
    path = tmp_path / "unsupported-wal.db"
    # 정상 종료하면 SQLite가 자동으로 checkpoint하므로 비정상 프로세스 종료를 재현한다.
    script = """
import os, sqlite3, sys
connection = sqlite3.connect(sys.argv[1], isolation_level=None)
connection.execute('CREATE TABLE seed (value TEXT)')
connection.execute('PRAGMA application_id=' + sys.argv[2])
connection.execute('PRAGMA user_version=2')
connection.execute('PRAGMA journal_mode=WAL')
connection.execute('CREATE TABLE unrecognized (value TEXT)')
connection.execute('PRAGMA user_version=99')
os._exit(0)
"""
    subprocess.run(  # noqa: S603 - 고정된 테스트 스크립트와 현재 Python만 실행한다.
        [sys.executable, "-c", script, str(path), str(store_module.APPLICATION_ID)], check=True
    )
    wal_path = path.with_name(path.name + "-wal")
    before = path.read_bytes(), wal_path.read_bytes()

    async def scenario():
        store = SQLiteStore(path, startup="explicit")
        backups, steps = _Backups(tmp_path, path), []
        try:
            with pytest.raises(StorePrepareError) as error:
                await _prepare(store, backups, steps)
            assert error.value.code is StoreStartFailure.STORE_SCHEMA_UNSUPPORTED
            assert steps == [StorePrepareStep.STORE_CHECK]
            assert not backups.calls
        finally:
            await _close(store)

    asyncio.run(scenario())
    assert (path.read_bytes(), wal_path.read_bytes()) == before


def test_explicit_start_without_checkpoint_control_rejects_before_connect(tmp_path, monkeypatch):
    path = tmp_path / "original.db"
    _v2_database(path)
    before = path.read_bytes()
    monkeypatch.delattr(sqlite3, "SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE", raising=False)
    connects = []

    def unexpected_connect(*args, **kwargs):
        connects.append((args, kwargs))
        raise AssertionError("connection must not be opened")

    monkeypatch.setattr(sqlite3, "connect", unexpected_connect)
    with pytest.raises(StoreError):
        SQLiteStore(path, startup="explicit")
    assert not connects
    assert path.read_bytes() == before


def test_expired_deadline_rejects_before_any_backup_and_faults_store(tmp_path):
    async def scenario():
        path = tmp_path / "deadline.db"
        _v2_database(path)
        before = path.read_bytes()
        store = SQLiteStore(path, startup="explicit")
        backups, steps = _Backups(tmp_path, path), []
        try:
            with pytest.raises(StorePrepareError) as error:
                await _prepare(store, backups, steps, deadline=time.monotonic() - 1)
            assert error.value.code is StoreStartFailure.STORE_DEADLINE_EXCEEDED
            assert store.execution_state().phase is StorePhase.FAULTED
            assert not backups.calls
            assert path.read_bytes() == before
        finally:
            await _close(store)

    asyncio.run(scenario())


def test_check_disk_io_error_has_fixed_start_failure_and_no_source_write(tmp_path, monkeypatch):
    path = tmp_path / "PRIVATE_SOURCE.db"
    _v2_database(path)
    before = path.read_bytes()
    original_connect = sqlite3.connect
    armed = threading.Event()

    class IoFaultConnection(sqlite3.Connection):
        def execute(self, statement, parameters=(), /):
            if armed.is_set() and statement.startswith("SELECT name,sql FROM sqlite_schema"):
                armed.clear()
                error = sqlite3.OperationalError("PRIVATE_DISK_ERROR")
                error.sqlite_errorcode = sqlite3.SQLITE_IOERR
                raise error
            return super().execute(statement, parameters)

    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *args, **kwargs: original_connect(*args, **kwargs, factory=IoFaultConnection),
    )

    async def scenario():
        store = SQLiteStore(path, startup="explicit")
        backups, steps = _Backups(tmp_path, path), []

        def on_step(step):
            steps.append(step)
            if step is StorePrepareStep.STORE_CHECK:
                armed.set()

        try:
            with pytest.raises(StorePrepareError) as error:
                await store.prepare_schema(
                    deadline=time.monotonic() + 10, begin_backup=backups.begin, on_step=on_step
                )
            assert error.value.code is StoreStartFailure.STORE_IO_FAILED
            assert "PRIVATE_" not in str(error.value) + repr(error.value)
            assert store.execution_state().phase is StorePhase.FAULTED
            assert not backups.calls
            assert path.read_bytes() == before
        finally:
            await _close(store)

    asyncio.run(scenario())
    assert path.read_bytes() == before


def test_new_database_creation_disk_error_reports_io_failure(tmp_path, monkeypatch):
    path = tmp_path / "new.db"
    original_connect = sqlite3.connect
    armed = threading.Event()

    class CreateFaultConnection(sqlite3.Connection):
        def execute(self, statement, parameters=(), /):
            if armed.is_set() and statement.startswith("CREATE TABLE"):
                armed.clear()
                error = sqlite3.OperationalError("PRIVATE_CREATE_IO_ERROR")
                error.sqlite_errorcode = sqlite3.SQLITE_IOERR
                raise error
            return super().execute(statement, parameters)

    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *args, **kwargs: original_connect(*args, **kwargs, factory=CreateFaultConnection),
    )

    async def scenario():
        store = SQLiteStore(path, startup="explicit")
        backups, steps = _Backups(tmp_path, path), []

        def on_step(step):
            steps.append(step)
            if step is StorePrepareStep.STORE_MIGRATE:
                armed.set()

        try:
            with pytest.raises(StorePrepareError) as error:
                await store.prepare_schema(
                    deadline=time.monotonic() + 10, begin_backup=backups.begin, on_step=on_step
                )
            assert error.value.code is StoreStartFailure.STORE_IO_FAILED
            assert "PRIVATE_" not in str(error.value) + repr(error.value)
            assert store.execution_state().phase is StorePhase.FAULTED
            assert not backups.calls
        finally:
            await _close(store)

    asyncio.run(scenario())
    with original_connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert not connection.execute(
            "SELECT name FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*'"
        ).fetchall()


@pytest.mark.parametrize("case", ["begin", "backup", "verify", "publish", "discard"])
def test_backup_failure_keeps_legacy_file_and_discards_partial(tmp_path, monkeypatch, case):
    path = tmp_path / "legacy.db"
    _v2_database(path)
    before = path.read_bytes()
    original_connect = sqlite3.connect

    class FaultyBackupConnection(sqlite3.Connection):
        def backup(self, target, *, pages=-1, progress=None, name="main", sleep=0.250):
            if case == "backup":
                raise sqlite3.OperationalError("PRIVATE_BACKUP_ERROR")
            if case == "verify":
                # SQLite destination remains empty: version and app ID verification must fail.
                return None
            return super().backup(target, pages=pages, progress=progress, name=name, sleep=sleep)

    def connect(*args, **kwargs):
        return original_connect(*args, **kwargs, factory=FaultyBackupConnection)

    monkeypatch.setattr(sqlite3, "connect", connect)

    async def scenario():
        store = SQLiteStore(path, startup="explicit")
        backups = _Backups(
            tmp_path,
            path,
            fail_publish=case in {"publish", "discard"},
            fail_discard=case == "discard",
        )
        steps = []

        def begin(label):
            if case == "begin":
                raise OSError("PRIVATE_BEGIN_ERROR")
            return backups.begin(label)

        try:
            with pytest.raises(StorePrepareError) as error:
                await store.prepare_schema(
                    deadline=time.monotonic() + 10, begin_backup=begin, on_step=steps.append
                )
            assert error.value.code is StoreStartFailure.STORE_BACKUP_FAILED
            assert "PRIVATE_" not in str(error.value) + repr(error.value)
            assert str(path) not in str(error.value) + repr(error.value)
            assert store.execution_state().phase is StorePhase.FAULTED
            assert store.execution_state().requires_restart
            assert steps == [StorePrepareStep.STORE_CHECK, StorePrepareStep.STORE_BACKUP]
            if case != "begin":
                assert backups.target is not None
                assert backups.target.discarded
                assert not backups.target.published
            assert path.read_bytes() == before
        finally:
            await _close(store)

    asyncio.run(scenario())
    assert path.read_bytes() == before


@pytest.mark.parametrize("fault", ["migration", "commit_before", "commit_ack"])
def test_migration_failure_keeps_published_backup_and_reports_commit_outcome(
    tmp_path, monkeypatch, fault
):
    path = tmp_path / "legacy.db"
    _v2_database(path)
    original_connect = sqlite3.connect
    armed = threading.Event()

    class FaultyMigrationConnection(sqlite3.Connection):
        def execute(self, statement, parameters=(), /):
            if (
                armed.is_set()
                and fault == "migration"
                and statement.startswith('ALTER TABLE "requests_new"')
            ):
                armed.clear()
                raise sqlite3.OperationalError("PRIVATE_MIGRATION_ERROR")
            if armed.is_set() and fault == "commit_before" and statement == "COMMIT":
                armed.clear()
                raise sqlite3.OperationalError("PRIVATE_COMMIT_BEFORE_ERROR")
            result = super().execute(statement, parameters)
            if armed.is_set() and fault == "commit_ack" and statement == "COMMIT":
                armed.clear()
                raise sqlite3.OperationalError("PRIVATE_COMMIT_ACK_ERROR")
            return result

    def connect(*args, **kwargs):
        return original_connect(*args, **kwargs, factory=FaultyMigrationConnection)

    monkeypatch.setattr(sqlite3, "connect", connect)

    async def scenario():
        store = SQLiteStore(path, startup="explicit")
        backups, steps = _Backups(tmp_path, path), []

        def on_step(step):
            steps.append(step)
            if step is StorePrepareStep.STORE_MIGRATE:
                armed.set()

        try:
            with pytest.raises(StorePrepareError) as error:
                await store.prepare_schema(
                    deadline=time.monotonic() + 10, begin_backup=backups.begin, on_step=on_step
                )
            assert error.value.code is (
                StoreStartFailure.STORE_COMMIT_UNKNOWN
                if fault == "commit_ack"
                else StoreStartFailure.STORE_MIGRATION_FAILED
            )
            assert store.execution_state().phase is StorePhase.FAULTED
            assert store.execution_state().requires_restart
            if fault in {"commit_ack", "commit_before"}:
                assert store.execution_state().commit_outcome is (
                    CommitOutcome.UNKNOWN if fault == "commit_ack" else CommitOutcome.ROLLED_BACK
                )
                assert store._connection is None
            assert backups.target is not None
            assert backups.target.published
            assert backups.target.final.exists()
            assert not backups.target.discarded
            assert "PRIVATE_" not in str(error.value) + repr(error.value)
        finally:
            await _close(store)

    asyncio.run(scenario())
    # Journal conversion can change file bytes after backup publication; compare the
    # committed schema/version and retained verified backup instead of raw bytes.
    with original_connect(path) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == (
            3 if fault == "commit_ack" else 2
        )
    with original_connect(tmp_path / "backup.sqlite3") as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 2
        assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_deadline_during_backup_discards_partial_without_migration(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    _v2_database(path)
    before = path.read_bytes()
    original_connect = sqlite3.connect
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(store_module, "time", SimpleNamespace(monotonic=lambda: clock.now))

    class SlowBackupConnection(sqlite3.Connection):
        def backup(self, target, *, pages=-1, progress=None, name="main", sleep=0.250):
            clock.now = 2.0
            assert progress is not None
            progress(sqlite3.SQLITE_OK, 1, 2)
            return super().backup(target, pages=pages, progress=progress, name=name, sleep=sleep)

    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *args, **kwargs: original_connect(*args, **kwargs, factory=SlowBackupConnection),
    )

    async def scenario():
        store = SQLiteStore(path, startup="explicit")
        backups, steps = _Backups(tmp_path, path), []
        try:
            with pytest.raises(StorePrepareError) as error:
                await _prepare(store, backups, steps, deadline=1.0)
            assert error.value.code is StoreStartFailure.STORE_DEADLINE_EXCEEDED
            assert store.execution_state().phase is StorePhase.FAULTED
            assert backups.target is not None
            assert backups.target.discarded
            assert not backups.target.published
            assert path.read_bytes() == before
        finally:
            await _close(store)

    asyncio.run(scenario())
    assert path.read_bytes() == before


def test_failure_restoring_foreign_keys_after_commit_is_not_reported_as_rollback(
    tmp_path, monkeypatch
):
    path = tmp_path / "legacy.db"
    _v2_database(path)
    original_connect = sqlite3.connect
    armed = threading.Event()

    class RestoreFaultConnection(sqlite3.Connection):
        def execute(self, statement, parameters=(), /):
            if armed.is_set() and statement == "PRAGMA foreign_keys=1":
                armed.clear()
                error = sqlite3.OperationalError("PRIVATE_FK_RESTORE_ERROR")
                error.sqlite_errorcode = sqlite3.SQLITE_IOERR
                raise error
            return super().execute(statement, parameters)

    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *args, **kwargs: original_connect(*args, **kwargs, factory=RestoreFaultConnection),
    )

    async def scenario():
        store = SQLiteStore(path, startup="explicit")
        backups, steps = _Backups(tmp_path, path), []

        def on_step(step):
            steps.append(step)
            if step is StorePrepareStep.STORE_MIGRATE:
                armed.set()

        try:
            with pytest.raises(StorePrepareError) as error:
                await store.prepare_schema(
                    deadline=time.monotonic() + 10, begin_backup=backups.begin, on_step=on_step
                )
            assert error.value.code is StoreStartFailure.STORE_IO_FAILED
            assert store.execution_state().phase is StorePhase.FAULTED
            assert store.execution_state().commit_outcome is not CommitOutcome.ROLLED_BACK
            assert backups.target is not None
            assert backups.target.published
        finally:
            await _close(store)

    asyncio.run(scenario())
    with original_connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_deadline_after_migration_commit_rejects_result_but_database_is_v3(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    _v2_database(path)
    original_connect = sqlite3.connect
    armed = threading.Event()
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(store_module, "time", SimpleNamespace(monotonic=lambda: clock.now))

    class SlowCommitConnection(sqlite3.Connection):
        def execute(self, statement, parameters=(), /):
            result = super().execute(statement, parameters)
            if armed.is_set() and statement == "COMMIT":
                armed.clear()
                clock.now = 2.0
            return result

    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *args, **kwargs: original_connect(*args, **kwargs, factory=SlowCommitConnection),
    )

    async def scenario():
        store = SQLiteStore(path, startup="explicit")
        backups, steps = _Backups(tmp_path, path), []

        def on_step(step):
            steps.append(step)
            if step is StorePrepareStep.STORE_MIGRATE:
                armed.set()

        try:
            with pytest.raises(StorePrepareError) as error:
                await store.prepare_schema(
                    deadline=1.0, begin_backup=backups.begin, on_step=on_step
                )
            assert error.value.code is StoreStartFailure.STORE_DEADLINE_EXCEEDED
            assert store.execution_state().phase is StorePhase.FAULTED
            assert backups.target is not None
            assert backups.target.published
        finally:
            await _close(store)

    asyncio.run(scenario())
    with original_connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"


@pytest.mark.parametrize("step_number", [2, 4], ids=["backup", "migration"])
@pytest.mark.parametrize("interrupt", ["halt", "close", "deadline"])
def test_queued_step_does_not_start_after_halt_close_or_deadline(
    tmp_path, monkeypatch, step_number, interrupt
):
    path = tmp_path / "queued.db"
    _v2_database(path)
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(store_module, "time", SimpleNamespace(monotonic=lambda: clock.now))

    async def scenario():
        store = SQLiteStore(path, startup="explicit")
        backups, steps = _Backups(tmp_path, path), []
        entered, release = threading.Event(), threading.Event()
        original_submit = store._writer.submit
        hold_task = None
        submission_count = 0

        def hold():
            entered.set()
            assert release.wait(5)

        async def blocked_submit(operation, *, write, **kwargs):
            nonlocal submission_count, hold_task
            submission_count += 1
            if submission_count == step_number:
                hold_task = asyncio.create_task(original_submit(hold, write=False))
                async with asyncio.timeout(5):
                    while not entered.is_set():  # noqa: ASYNC110
                        await asyncio.sleep(0.005)
            return await original_submit(operation, write=write, **kwargs)

        store._writer.submit = blocked_submit
        deadline = 1.0 if interrupt == "deadline" else 10.0
        prepare = asyncio.create_task(_prepare(store, backups, steps, deadline=deadline))
        try:
            async with asyncio.timeout(5):
                while True:
                    with store._writer._condition:
                        queued = len(store._writer._jobs) == 1
                    if entered.is_set() and queued:
                        break
                    await asyncio.sleep(0.005)
            if interrupt == "halt":
                store.halt_execution(Reason.INTERNAL_ERROR)
            elif interrupt == "close":
                store.close()
            else:
                clock.now = deadline + 1.0
            release.set()
            with pytest.raises(StorePrepareError) as error:
                await prepare
            if interrupt == "deadline":
                assert error.value.code is StoreStartFailure.STORE_DEADLINE_EXCEEDED
            else:
                assert error.value.code is StoreStartFailure.STORE_IO_FAILED
            if step_number == 2:
                assert backups.calls == []
            else:
                assert backups.target is not None
                assert backups.target.published
        finally:
            release.set()
            if hold_task is not None:
                await asyncio.gather(hold_task, return_exceptions=True)
            store.close()
            assert await store.wait_closed(5)

    asyncio.run(scenario())
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


@pytest.mark.skipif(sys.platform != "win32", reason="C AppFolders는 Windows 전용")
def test_real_windows_backup_target_publishes_verified_legacy_backup(tmp_path):
    from guard_app.security.paths import prepare_app_folders

    folders = prepare_app_folders(tmp_path / "app")
    try:
        path = folders.paths.db_file
        _v2_database(path)

        async def scenario():
            store = SQLiteStore(path, startup="explicit")
            try:
                report = await store.prepare_schema(
                    deadline=time.monotonic() + 10, begin_backup=folders.begin_backup
                )
                assert report.backup_published
                assert report.migrated
                assert report.from_version == 2
                files = list(folders.paths.backups.glob("v2-to-v3-*.sqlite3"))
                assert len(files) == 1
                with sqlite3.connect(files[0]) as backup:
                    assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"
                    assert backup.execute("PRAGMA user_version").fetchone()[0] == 2
            finally:
                await _close(store)

        asyncio.run(scenario())
    finally:
        folders.close()
