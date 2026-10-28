"""동기 B 테스트가 SQLite 연결을 writer 스레드에서만 관찰하는 도우미.

이벤트 루프 안에서는 이 프록시 대신 비동기 writer 도우미를 사용한다.
"""

from typing import Any


class _WriterCursor:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = iter(rows)

    def fetchone(self) -> Any:
        return next(self._rows, None)

    def fetchall(self) -> Any:
        return list(self._rows)

    def __iter__(self):
        return iter(self.fetchall())


class _WriterConnection:
    def __init__(self, store: Any) -> None:
        object.__setattr__(self, "_store", store)

    def execute(self, *args: Any, **kwargs: Any) -> _WriterCursor:
        def execute_on_writer(connection: Any) -> list[Any]:
            cursor = connection.execute(*args, **kwargs)
            try:
                return cursor.fetchall()
            finally:
                cursor.close()

        return _WriterCursor(self._store._run_on_writer(execute_on_writer))

    def executemany(self, *args: Any, **kwargs: Any) -> _WriterCursor:
        def execute_on_writer(connection: Any) -> list[Any]:
            cursor = connection.executemany(*args, **kwargs)
            try:
                return cursor.fetchall()
            finally:
                cursor.close()

        return _WriterCursor(self._store._run_on_writer(execute_on_writer))

    def is_instance(self, kind: type) -> bool:
        return self._store._run_on_writer(lambda connection: isinstance(connection, kind))

    def __getattr__(self, name: str) -> Any:
        return self._store._run_on_writer(lambda connection: getattr(connection, name))

    def __setattr__(self, name: str, value: Any) -> None:
        self._store._run_on_writer(lambda connection: setattr(connection, name, value))


def writer_connection(store: Any) -> _WriterConnection:
    return _WriterConnection(store)
