"""Qt main thread and backend ownership for product and explicit demo modes."""

from __future__ import annotations

import argparse
import importlib.util
import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from guard_app.services.codex_preparation import CodexProcessPreparation
    from guard_app.services.product_settings import SettingsFile

_APP_CODE = re.compile(r"APP_[A-Z_]{1,60}")
_SETTINGS_CODE = re.compile(r"SETTINGS_[A-Z_]{1,60}")
# C2.14 file codes; B3.11 parse codes are the other SETTINGS_ ones.
_SETTINGS_FILE_REFUSALS = {
    "SETTINGS_FILE_INVALID": (
        "설정 파일이 링크이거나 다른 사용자에게도 권한이 있어 열지 않음. 앱을 시작하지 않음"
    ),
    "SETTINGS_FILE_READ_FAILED": (
        "설정 파일을 읽지 못해 앱을 시작하지 않음. 다른 프로그램이 파일을 쓰는 중인지 확인하세요"
    ),
}
# B6.17 8.1 (#37): after closing, watch the backend this long past the bridge's
# own shutdown bound before giving up on a confirmed cleanup (A110).
CLOSE_OBSERVATION_S = 5.0
EXIT_CLEANUP_UNCONFIRMED = 3


def main() -> int:
    from guard_app.security.redact import install_log_redaction

    install_log_redaction()
    args = _parse_args()

    codex: CodexProcessPreparation | None = None
    settings: SettingsFile | None = None
    if not args.demo:
        from guard_app.security.paths import AppPathError
        from guard_app.services.codex_preparation import CodexProcessPreparation
        from guard_app.services.product_settings import SettingsStartError, read_settings

        # A102: the single-instance lock comes before the DB, the backend and the
        # window. Without it the app does not start.
        codex = CodexProcessPreparation(executable=args.codex_exe)
        try:
            codex.claim()
        except AppPathError as error:
            print(_claim_refusal(error), file=sys.stderr)
            return 1
        # CFG-01: a settings file that cannot be read or parsed stops the start
        # and is left as it is. No file is the first run.
        try:
            settings = read_settings(codex.folders)
        except SettingsStartError as error:
            codex.close()
            print(_settings_refusal(error.code), file=sys.stderr)
            return 1
        # A106: C's check of the product DB and its side files (C2.19) refuses a
        # link, an extra hard link or foreign permissions before the window opens.
        try:
            _ = codex.db_file
        except AppPathError as error:
            codex.close()
            print(_claim_refusal(error), file=sys.stderr)
            return 1
    confirmed: bool | None = None  # None: _run raised before the backend reported
    handler: RotatingFileHandler | None = None
    root = logging.getLogger()
    previous_level = root.level
    try:
        if codex is not None:
            try:
                handler = RotatingFileHandler(
                    codex.folders.paths.data / "guard.log",
                    maxBytes=1024 * 1024,
                    backupCount=2,
                    encoding="utf-8",
                )
            except OSError:
                print("로그 파일을 열지 못해 앱을 시작하지 않음", file=sys.stderr)
                return 1
            handler.setLevel(logging.INFO)
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
            )
            root.addHandler(handler)
            root.setLevel(logging.INFO)
        code, confirmed = _run(args, codex, settings)
    finally:
        if handler is not None:
            root.removeHandler(handler)
            handler.close()
            root.setLevel(previous_level)
        # Release the single-instance lock unless the backend left cleanup
        # unconfirmed: then the process ends while it still holds the lock.
        if codex is not None and confirmed is not False:
            codex.close()
    if not confirmed:
        _end_unconfirmed()
    return code


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MCP Runtime Guard")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="소스 checkout에서 선택한 Store + 실제 MCP 합성 서버 사용",
    )
    # Demo-only options default to None so product mode can refuse them.
    parser.add_argument("--scenario", choices=("ask", "allow", "deny"))
    parser.add_argument("--mcp-transport", choices=("stdio", "http"))
    parser.add_argument("--store", choices=("memory", "sqlite"))
    parser.add_argument(
        "--db-path",
        metavar="PATH",
        help="시연 SQLite 파일(기존 부모 디렉터리, 앱 폴더 밖). 제품은 앱 폴더의 고정 DB를 쓴다",
    )
    parser.add_argument(
        "--codex-exe",
        metavar="PATH",
        type=Path,
        help="고정 버전 codex.exe의 절대 경로(기본: tools/install_codex.py 설치 위치)",
    )
    args = parser.parse_args()
    # A106: product mode opens only the fixed DB in the app folder (C2.17).
    demo_only = (
        args.scenario,
        args.mcp_transport,
        args.store,
        args.db_path,
    )
    if not args.demo and any(value is not None for value in demo_only):
        parser.error("시연 전용 옵션은 --demo와 함께만 쓸 수 있다")
    if args.demo:
        args.scenario = args.scenario or "ask"
        args.mcp_transport = args.mcp_transport or "stdio"
        args.store = args.store or "memory"
        if importlib.util.find_spec("examples") is None:
            parser.error(
                "합성 시연(--demo)은 소스 checkout에서 python -m guard_app으로만 실행할 수 있다"
            )
    if args.demo and (args.store == "sqlite") != (args.db_path is not None):
        parser.error("저장소 종류와 DB 경로 설정이 일치하지 않는다")
    if args.demo and args.db_path is not None and not _outside_app_folder(args.db_path):
        parser.error("시연 DB는 제품 앱 폴더 밖이라고 확인된 경로만 쓸 수 있다")
    if args.codex_exe is not None and (args.demo or not args.codex_exe.is_absolute()):
        parser.error("--codex-exe는 제품 실행에서 codex.exe의 절대 경로로만 쓸 수 있다")

    return args


def _end_unconfirmed() -> NoReturn:
    """B6.17 8.1: cleanup was not confirmed, so end the process now (A110).

    The single-instance lock is not released first; the OS releases its handle
    when the process ends, and C2.12's Job objects end any Codex left. A normal
    exit would join a stuck backend or worker thread with the window gone.
    """
    print("정리 완료를 확인하지 못한 채 앱을 종료한다", file=sys.stderr)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(EXIT_CLEANUP_UNCONFIRMED)


def _outside_app_folder(db_path: str) -> bool:
    """A106: whether a demo DB is confirmed to be outside the product app folder.

    The product DB is fixed in that folder (C2.17) and its single owner is the
    app lock (REC-01), which the demo does not take. Links and junctions are
    resolved, and a hard link to the product DB counts as the product DB. A path
    that cannot be checked counts as inside.
    """
    from guard_app.security.paths import AppPathError, AppPaths, app_root

    if sys.platform != "win32":
        return True  # the app folder, like product mode, exists only on Windows
    try:
        product = AppPaths(app_root())
        target = Path(db_path).resolve()
        if target.is_relative_to(product.root.resolve()):
            return False
        return not (
            target.exists() and product.db_file.exists() and target.samefile(product.db_file)
        )
    except (AppPathError, OSError, ValueError):
        return False


def _claim_refusal(error: Exception) -> str:
    """Fixed text for a refused claim. C2.13 codes carry no paths; other text is not shown."""
    code = error.args[0] if error.args else None
    if code == "APP_ALREADY_RUNNING":
        return "MCP Runtime Guard가 이미 실행 중이라 시작하지 않는다"
    if code == "APP_DB_INVALID":
        return "제품 DB 파일이 링크이거나 권한이 달라 앱을 시작하지 않음(APP_DB_INVALID)"
    if type(code) is str and _APP_CODE.fullmatch(code):
        return f"앱 폴더를 준비하지 못해 시작하지 않는다({code})"
    return "앱 폴더를 준비하지 못해 시작하지 않는다"


def _settings_refusal(code: str) -> str:
    """Fixed text for a settings file the start refused. The file is never overwritten."""
    known = _SETTINGS_FILE_REFUSALS.get(code)
    if known is not None:
        return known
    if _SETTINGS_CODE.fullmatch(code):
        return (
            f"설정 파일 형식이 올바르지 않아 앱을 시작하지 않음({code}). "
            "앱을 닫은 상태에서 파일을 고치세요"
        )
    return "설정 파일을 읽지 못해 앱을 시작하지 않음"


def _run(
    args: argparse.Namespace,
    codex: CodexProcessPreparation | None,
    settings: SettingsFile | None = None,
) -> tuple[int, bool]:
    """Window, bridge and Qt loop. Product mode passes its claimed `codex`.

    Returns the Qt exit code and whether the backend confirmed its cleanup.
    """
    from PySide6.QtWidgets import QApplication

    from guard_app.services.bridge import BackendBridge

    store_title = "SQLite Store" if not args.demo or args.store == "sqlite" else "메모리 Store"
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    if codex is None:
        from examples.synthetic_app import bootstrap

        bridge = BackendBridge(
            lambda: bootstrap(
                args.scenario,
                mcp_transport=args.mcp_transport,
                store_kind=args.store,
                db_path=args.db_path,
            )
        )
    else:
        from guard_app.security.login_browser import open_login_browser
        from guard_app.services.codex_preparation import (
            PRODUCT_SHUTDOWN_TIMEOUT_S,
            PRODUCT_STARTUP_TIMEOUT_S,
        )
        from guard_app.services.session_runtime import bootstrap_product

        # A101: C's app folders and launcher behind each Task's Codex session.
        # A106: the DB is the one fixed in the folder `codex` claimed (C2.17),
        # checked again by C's `db_path()` right before the backend opens it.
        # A52: the login screen's app-server and logout cleanup go through it too.
        preparation = codex.preparation()
        account = codex.account_access(open_browser=open_login_browser)
        bridge = BackendBridge(
            lambda: bootstrap_product(
                codex.folders,
                preparation=preparation,
                settings=settings,
                account=account,
                on_step=lambda step: bridge.store_progress(step),
            ),
            startup_timeout=PRODUCT_STARTUP_TIMEOUT_S,
            shutdown_timeout=PRODUCT_SHUTDOWN_TIMEOUT_S,
        )

        codex.set_progress_listener(bridge.codex_progress)

    from guard_app.ui.application import run_application

    saver = None
    if codex is not None:
        from guard_app.security.secrets import save_scanner_key

        saver = save_scanner_key
    return run_application(
        app,
        bridge,
        context=f"{'합성 개발 시연' if args.demo else 'Codex 연결 대기'} / {store_title}",
        close_observation_s=CLOSE_OBSERVATION_S,
        scanner_key_saver=saver,
        scanner_key_tail=None if codex is None else _scanner_key_tail,
    )


def _scanner_key_tail() -> str | None:
    """Only C's safe suffix crosses into the UI; store errors carry no UI text."""
    from guard_app.security.secrets import ScannerKeyError, scanner_key_tail

    try:
        return scanner_key_tail()
    except ScannerKeyError:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
