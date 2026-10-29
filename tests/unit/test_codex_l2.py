"""security.codex_l2(C6.14) 단위 테스트. codex.exe 없이 판정·가짜 모델·실행 명세를 본다.

정상 관찰의 모양은 실제 codex.exe 0.162.1 실행에서 본 것과 같다(완료 기록의 근거 JSON).
`GUARD_TEST_CODEX_EXE`에 고정 버전 codex.exe 경로를 주면 마지막 테스트가 실제로 L2를 돌린다.
"""

import asyncio
import copy
import json
import os
import sys
import tomllib
from pathlib import Path

import pytest

from guard_app.security import codex_l2
from guard_app.security.codex_home import CODEX_CONFIG_DIR, write_codex_home
from guard_app.security.codex_l2 import (
    FORBIDDEN_FUNCTIONS,
    L2_FINAL_TEXT,
    L2_PROMPT,
    PROBE_NAMESPACE,
    PROBE_RESULT,
    PROBE_TOOL,
    L2Observation,
    MockModel,
    evaluate,
    forced_calls,
    probe_tools,
)
from guard_app.security.launcher import (
    L2_TOKEN_ENV,
    CodexLauncher,
    LaunchError,
    l2_overrides,
)

L1 = tomllib.loads((CODEX_CONFIG_DIR / "config.toml").read_text(encoding="utf-8"))
HOME = Path("C:/MCPRuntimeGuard/codex_home")
TASK = "C:\\MCPRuntimeGuard\\tasks\\l2-0123456789abcdef"
PORT = 50435
BASE = "You are the Guard L2 test assistant."
COLLABORATION = (
    "<collaboration_mode># Collaboration Mode: Default\n\nYou are now in Default mode.\n"
    "</collaboration_mode>"
)
TOKEN = "a" * 43


def _params(tools=None):
    return {
        "model": "gpt-6.1-sol",
        "approvalsReviewer": "user",
        "ephemeral": True,
        "environments": [],
        "baseInstructions": BASE,
        "dynamicTools": list(probe_tools() if tools is None else tools),
    }


def _message(role, text):
    return {"type": "message", "role": role, "content": [{"type": "input_text", "text": text}]}


def _tools_block(extra=()):
    return {
        "type": "additional_tools",
        "role": "developer",
        "tools": [
            {
                "type": "namespace",
                "name": "functions",
                "tools": [{"type": "function", "name": "request_user_input"}],
            },
            {
                "type": "namespace",
                "name": PROBE_NAMESPACE,
                "tools": [{"type": "function", "name": PROBE_TOOL}],
            },
            *extra,
        ],
    }


def _outputs():
    items = [
        {
            "type": "function_call_output",
            "call_id": f"l2_forbidden_{i}",
            "output": f"unsupported call: {n}",
        }
        for i, n in enumerate(FORBIDDEN_FUNCTIONS)
    ]
    items.append({"type": "function_call_output", "call_id": "l2_local_shell", "output": "aborted"})
    items.append(
        {
            "type": "custom_tool_call_output",
            "call_id": "l2_custom_apply_patch",
            "output": "unsupported custom tool call: apply_patch",
        }
    )
    items += [
        {"type": "function_call_output", "call_id": f"l2_probe_{n}", "output": PROBE_RESULT}
        for n in (1, 2)
    ]
    return items


def _provider():
    values = {
        key.rsplit(".", 1)[1]: value
        for key, value in l2_overrides(PORT).items()
        if key.startswith("model_providers.")
    }
    return {**values, "experimental_bearer_token": None, "requires_openai_auth": False}


def good():
    """실제 0.162.1 실행과 같은 모양의 통과 관찰."""
    config = copy.deepcopy(L1)
    config["model_catalog_json"] = str(HOME / "model_catalog.json")
    config["features"] = {**L1["features"], "mentions_v2": True, "remote_control": False}
    config.update(
        {
            "mcp_servers": {},
            "profile": None,
            "instructions": None,
            "developer_instructions": None,
            "model_instructions_file": None,
            "model_provider": "guard_l2",
            "model_providers": {"guard_l2": _provider()},
            "sqlite_home": TASK,
        }
    )
    layers = [
        {
            "name": {"type": "sessionFlags"},
            "config": {"model_provider": "guard_l2", "model_providers": {}, "sqlite_home": TASK},
        },
        {
            "name": {"type": "user", "file": str(HOME / "config.toml"), "profile": None},
            "config": copy.deepcopy(L1),
        },
        {
            "name": {"type": "system", "file": "C:\\ProgramData\\OpenAI\\Codex\\config.toml"},
            "config": {},
        },
    ]
    messages = [
        _message("developer", BASE),
        _message("developer", COLLABORATION),
        _message("user", L2_PROMPT),
    ]
    first = {"input": [_tools_block(), *messages], "tool_choice": "auto"}
    second = {"input": [_tools_block(), *messages, *_outputs()], "tool_choice": "auto"}
    return L2Observation(
        model_port=PORT,
        task_folder=TASK,
        requirements={"supportsIndependentSpeedModes": True, "requirements": None},
        config=config,
        config_layers=layers,
        remote_status_read="disabled",
        remote_status_changed=["disabled"],
        thread_params=_params(),
        item_types=[
            "userMessage",
            "userMessage",
            "dynamicToolCall",
            "dynamicToolCall",
            "agentMessage",
        ],
        server_requests=["item/tool/call", "item/tool/call"],
        probe_calls=[
            (PROBE_NAMESPACE, PROBE_TOOL, "l2_probe_1"),
            (PROBE_NAMESPACE, PROBE_TOOL, "l2_probe_2"),
        ],
        turn_status="completed",
        model_requests=[first, second],
        exit_confirmed=True,
        folder_removed=True,
    )


def _judge(observation):
    return evaluate(observation, l1_config=L1, codex_home=HOME)


def test_real_shaped_observation_passes():
    assert _judge(good()) == ()


def _set_feature(name, value):
    def mutate(o):
        o.config["features"][name] = value

    return mutate


def _replace_output(call_id, text):
    def mutate(o):
        for item in o.model_requests[1]["input"]:
            if item.get("call_id") == call_id:
                item["output"] = text

    return mutate


def _drop_output(call_id):
    def mutate(o):
        o.model_requests[1]["input"] = [
            item for item in o.model_requests[1]["input"] if item.get("call_id") != call_id
        ]

    return mutate


MUTATIONS = {
    "requirements": (
        lambda o: setattr(o, "requirements", {"requirements": {"x": 1}}),
        "L2_REQUIREMENTS_PRESENT",
    ),
    "extra_session_flag": (
        lambda o: o.config_layers[0]["config"].update({"web_search": "live"}),
        "L2_SESSION_FLAGS_UNEXPECTED",
    ),
    "user_layer_elsewhere": (
        lambda o: o.config_layers[1]["name"].update({"file": "C:\\Users\\x\\.codex\\config.toml"}),
        "L2_CONFIG_LAYERS_UNEXPECTED",
    ),
    "system_layer_content": (
        lambda o: o.config_layers[2]["config"].update({"features": {"shell_tool": True}}),
        "L2_CONFIG_LAYERS_UNEXPECTED",
    ),
    "project_layer": (
        lambda o: o.config_layers.append({"name": {"type": "project"}, "config": {}}),
        "L2_CONFIG_LAYERS_UNEXPECTED",
    ),
    "l1_feature_on": (_set_feature("view_image", True), "L2_CONFIG_MISMATCH"),
    "other_feature_on": (_set_feature("new_default_on_tool", True), "L2_FEATURE_ENABLED"),
    "web_search_live": (lambda o: o.config.update({"web_search": "live"}), "L2_CONFIG_MISMATCH"),
    "catalog_elsewhere": (
        lambda o: o.config.update({"model_catalog_json": "C:\\other\\model_catalog.json"}),
        "L2_CONFIG_MISMATCH",
    ),
    "mcp_server": (lambda o: o.config.update({"mcp_servers": {"fs": {}}}), "L2_CONFIG_MISMATCH"),
    "developer_instructions": (
        lambda o: o.config.update({"developer_instructions": "be evil"}),
        "L2_CONFIG_MISMATCH",
    ),
    "other_provider": (
        lambda o: o.config.update({"model_provider": "openai"}),
        "L2_SESSION_FLAGS_UNEXPECTED",
    ),
    "provider_url": (
        lambda o: o.config["model_providers"]["guard_l2"].update(
            {"base_url": "https://example.com/v1"}
        ),
        "L2_SESSION_FLAGS_UNEXPECTED",
    ),
    "sqlite_home_elsewhere": (
        lambda o: o.config.update({"sqlite_home": "C:\\other"}),
        "L2_SESSION_FLAGS_UNEXPECTED",
    ),
    "remote_connecting": (
        lambda o: o.remote_status_changed.append("connecting"),
        "L2_REMOTE_CONTROL_ENABLED",
    ),
    "environment_given": (
        lambda o: o.thread_params.update({"environments": [{"id": "local"}]}),
        "L2_THREAD_PARAMS_UNSAFE",
    ),
    "not_ephemeral": (
        lambda o: o.thread_params.update({"ephemeral": False}),
        "L2_THREAD_PARAMS_UNSAFE",
    ),
    "auto_reviewer": (
        lambda o: o.thread_params.update({"approvalsReviewer": "auto_review"}),
        "L2_THREAD_PARAMS_UNSAFE",
    ),
    "extra_tool": (
        lambda o: o.model_requests[0]["input"].__setitem__(
            0,
            _tools_block(
                (
                    {
                        "type": "namespace",
                        "name": "functions",
                        "tools": [{"type": "function", "name": "spawn_agent"}],
                    },
                )
            ),
        ),
        "L2_TOOLS_UNEXPECTED",
    ),
    "hosted_tool": (
        lambda o: o.model_requests[0]["input"].__setitem__(
            0, _tools_block(({"type": "tool_search"},))
        ),
        "L2_TOOLS_UNEXPECTED",
    ),
    "top_level_tools": (
        lambda o: o.model_requests[0].update({"tools": [{"type": "web_search"}]}),
        "L2_TOOLS_UNEXPECTED",
    ),
    "permissions_block": (
        lambda o: o.model_requests[0]["input"].append(
            _message("developer", "<permissions instructions>x")
        ),
        "L2_PROMPT_UNEXPECTED",
    ),
    "environment_block": (
        lambda o: o.model_requests[1]["input"].append(_message("user", "<environment_context>x")),
        "L2_PROMPT_UNEXPECTED",
    ),
    "other_base": (
        lambda o: o.model_requests[0]["input"].__setitem__(
            1, _message("developer", "You are Codex")
        ),
        "L2_PROMPT_UNEXPECTED",
    ),
    "top_level_instructions": (
        lambda o: o.model_requests[0].update({"instructions": "x"}),
        "L2_PROMPT_UNEXPECTED",
    ),
    "shell_ran": (_replace_output("l2_forbidden_1", "exit code 0"), "L2_FORBIDDEN_NOT_REFUSED"),
    "local_shell_ran": (_replace_output("l2_local_shell", "hello"), "L2_FORBIDDEN_NOT_REFUSED"),
    "tool_search_handled": (
        _replace_output("l2_forbidden_17", "aborted"),
        "L2_FORBIDDEN_NOT_REFUSED",
    ),
    "forbidden_output_missing": (_drop_output("l2_forbidden_3"), "L2_FORBIDDEN_NOT_REFUSED"),
    "probe_output_missing": (_drop_output("l2_probe_2"), "L2_PROBE_CALLS_UNEXPECTED"),
    "command_item": (lambda o: o.item_types.append("commandExecution"), "L2_ITEM_UNEXPECTED"),
    "approval_request": (
        lambda o: o.server_requests.append("item/commandExecution/requestApproval"),
        "L2_SERVER_REQUEST_UNEXPECTED",
    ),
    "probe_overlap": (lambda o: setattr(o, "probe_overlap", True), "L2_PROBE_CALLS_OVERLAP"),
    "probe_missing": (lambda o: o.probe_calls.pop(), "L2_PROBE_CALLS_UNEXPECTED"),
    "turn_failed": (lambda o: setattr(o, "turn_status", "failed"), "L2_TURN_NOT_COMPLETED"),
    "model_get": (
        lambda o: o.other_requests.append("GET /v1/models"),
        "L2_MODEL_TRAFFIC_UNEXPECTED",
    ),
    "unauthorized": (
        lambda o: setattr(o, "unauthorized_requests", 1),
        "L2_MODEL_TRAFFIC_UNEXPECTED",
    ),
    "unreadable": (
        lambda o: setattr(o, "unreadable_requests", 1),
        "L2_MODEL_TRAFFIC_UNEXPECTED",
    ),
    "one_request": (lambda o: o.model_requests.pop(), "L2_MODEL_REQUEST_MISSING"),
    "exit_unconfirmed": (lambda o: setattr(o, "exit_confirmed", False), "L2_EXIT_UNCONFIRMED"),
    "folder_left": (lambda o: setattr(o, "folder_removed", False), "L2_FOLDER_NOT_REMOVED"),
    "run_error": (lambda o: o.errors.append("L2_REQUEST_TIMEOUT"), "L2_REQUEST_TIMEOUT"),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_each_departure_fails_with_its_code(name):
    mutate, code = MUTATIONS[name]
    observation = good()
    mutate(observation)
    assert code in _judge(observation)


@pytest.mark.skipif(sys.platform != "win32", reason="정션은 Windows 기능이다")
def test_the_same_files_under_another_path_pass_and_other_files_fail(tmp_path):
    """A161: 앱 패키지 가상화처럼 Codex가 같은 파일을 다른 경로로 보고해도 통과한다.
    내용이 같아도 다른 파일이면 실패한다."""
    home = tmp_path / "home"
    other = tmp_path / "other"
    for folder in (home, other):
        folder.mkdir()
        for name in ("config.toml", "model_catalog.json"):
            (folder / name).write_text("same content", encoding="utf-8")
    alias = tmp_path / "alias"
    import _winapi

    _winapi.CreateJunction(str(home), str(alias))

    def reported(folder):
        observation = good()
        observation.config_layers[1]["name"]["file"] = str(folder / "config.toml")
        observation.config["model_catalog_json"] = str(folder / "model_catalog.json")
        return evaluate(observation, l1_config=L1, codex_home=home)

    assert reported(home) == ()
    assert reported(alias) == ()
    assert reported(other) == ("L2_CONFIG_LAYERS_UNEXPECTED", "L2_CONFIG_MISMATCH")
    assert reported(tmp_path / "missing") == ("L2_CONFIG_LAYERS_UNEXPECTED", "L2_CONFIG_MISMATCH")


def test_probe_calls_in_either_order_pass():
    """#21: 실제 0.162.1이 두 검사 호출을 바꾼 순서로 차례로 보낸 경우(40회 중 1회)는 통과다."""
    swapped = good()
    swapped.probe_calls.reverse()
    assert _judge(swapped) == ()
    doubled = good()
    doubled.probe_calls.append(doubled.probe_calls[0])
    assert "L2_PROBE_CALLS_UNEXPECTED" in _judge(doubled)


def test_allowed_extra_feature_and_missing_config_shapes():
    allowed = good()
    allowed.config["features"]["mentions_v2"] = True
    assert _judge(allowed) == ()
    broken = good()
    broken.config = None
    assert "L2_CONFIG_UNREADABLE" in _judge(broken)


def test_forced_calls_cover_the_seven_and_the_other_builtins():
    calls = forced_calls()
    names = [
        item.get("name")
        for item in calls
        if item["type"] == "function_call" and "namespace" not in item
    ]
    assert sorted(names) == sorted(FORBIDDEN_FUNCTIONS)
    assert len(set(names)) == len(names)
    seven = {"exec_command", "shell", "shell_command", "view_image", "apply_patch", "spawn_agent"}
    assert seven <= set(names)
    assert any(item["type"] == "local_shell_call" for item in calls)
    assert any(
        item["type"] == "custom_tool_call" and item["name"] == "apply_patch" for item in calls
    )
    probes = [item for item in calls if item.get("namespace") == PROBE_NAMESPACE]
    assert [json.loads(item["arguments"])["n"] for item in probes] == [1, 2]


async def _http(port, request: bytes) -> tuple[int, bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    head, _, body = data.partition(b"\r\n\r\n")
    return int(head.split(b" ", 2)[1]), body


def _post(path, body: bytes, token=TOKEN, extra=b""):
    return (
        (
            f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer {token}\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
            "Connection: close\r\n"
        ).encode()
        + extra
        + b"\r\n"
        + body
    )


@pytest.mark.asyncio
async def test_mock_model_scripts_forced_calls_then_a_final_answer():
    model = MockModel(TOKEN)
    port = await model.start()
    try:
        status, first = await _http(port, _post("/v1/responses", b'{"input": []}'))
        assert status == 200
        events = [
            json.loads(line[6:])
            for line in first.decode().splitlines()
            if line.startswith("data: ")
        ]
        assert events[0]["type"] == "response.created"
        assert events[-1]["type"] == "response.completed"
        names = {
            event["item"].get("name")
            for event in events
            if event["type"] == "response.output_item.done"
        }
        assert set(FORBIDDEN_FUNCTIONS) <= names
        status, second = await _http(port, _post("/v1/responses", b'{"input": [1]}'))
        assert status == 200
        assert L2_FINAL_TEXT in second.decode()
        assert model.requests == [{"input": []}, {"input": [1]}]
    finally:
        await model.close()


@pytest.mark.asyncio
async def test_mock_model_refuses_wrong_token_other_paths_and_chunked_bodies():
    model = MockModel(TOKEN)
    port = await model.start()
    try:
        assert (await _http(port, _post("/v1/responses", b"{}", token="b" * 43)))[0] == 401
        get = (
            f"GET /v1/models HTTP/1.1\r\nAuthorization: Bearer {TOKEN}\r\nConnection: close\r\n\r\n"
        )
        assert (await _http(port, get.encode()))[0] == 404
        chunked = _post("/v1/responses", b"{}", extra=b"Transfer-Encoding: chunked\r\n")
        assert (await _http(port, chunked))[0] == 400
        assert model.requests == []
        assert model.unauthorized == 1
        assert model.other_requests == ["GET /v1/models"]
        assert model.unreadable == 1
    finally:
        await model.close()


@pytest.mark.asyncio
async def test_extra_unreadable_request_fails_an_otherwise_good_run():
    """#41 B 리뷰 1번: 정상 요청 두 개 말고 읽을 수 없는 요청이 하나 더 오면 L2가 실패한다."""
    model = MockModel(TOKEN)
    port = await model.start()
    try:
        chunked = _post("/v1/responses", b"{}", extra=b"Transfer-Encoding: chunked\r\n")
        assert (await _http(port, chunked))[0] == 400
    finally:
        await model.close()
    observation = good()
    expected_requests = list(observation.model_requests)
    model.requests.extend(expected_requests)
    codex_l2._record_model_traffic(observation, model)
    assert observation.model_requests == expected_requests
    assert observation.unreadable_requests == 1
    assert _judge(observation) == ("L2_MODEL_TRAFFIC_UNEXPECTED",)


PYTHON = Path(sys.base_prefix) / "python.exe"


@pytest.mark.skipif(sys.platform != "win32", reason="launcher는 Windows 전용이다")
def test_l2_spec_adds_only_the_fixed_provider_overrides(tmp_path):
    home, task = tmp_path / "home", tmp_path / "task"
    home.mkdir()
    task.mkdir()
    launcher = CodexLauncher(PYTHON, home, "0" * 64)
    product = launcher.spec(task)
    spec = launcher.l2_spec(task, PORT, TOKEN)
    assert spec.args[: len(product.args)] == product.args
    extra = spec.args[len(product.args) :]
    assert extra[::2] == ("-c",) * len(l2_overrides(PORT))
    assert [arg.split("=", 1)[0] for arg in extra[1::2]] == list(l2_overrides(PORT))
    assert f'base_url="http://127.0.0.1:{PORT}/v1"' in " ".join(extra)
    assert spec.env == (*product.env, (L2_TOKEN_ENV, TOKEN))
    assert not any("model_provider" in arg for arg in product.args)
    assert (spec.executable, spec.cwd, spec.stderr_path) == (
        product.executable,
        product.cwd,
        product.stderr_path,
    )


@pytest.mark.skipif(sys.platform != "win32", reason="launcher는 Windows 전용이다")
@pytest.mark.parametrize(
    ("port", "token"),
    [(0, TOKEN), (70000, TOKEN), ("50435", TOKEN), (PORT, "short"), (PORT, "a b" * 20)],
)
def test_l2_spec_rejects_bad_port_or_token(tmp_path, port, token):
    home, task = tmp_path / "home", tmp_path / "task"
    home.mkdir()
    task.mkdir()
    with pytest.raises(LaunchError) as raised:
        CodexLauncher(PYTHON, home, "0" * 64).l2_spec(task, port, token)
    assert str(raised.value) == "LAUNCH_L2_INVALID"


REAL_CODEX = os.environ.get("GUARD_TEST_CODEX_EXE")


@pytest.mark.skipif(not REAL_CODEX, reason="GUARD_TEST_CODEX_EXE가 없으면 실제 L2는 돌리지 않는다")
@pytest.mark.asyncio
async def test_real_codex_passes_l2(tmp_path):
    from guard_app.security.paths import prepare_app_folders

    folders = prepare_app_folders(tmp_path / "app")
    try:
        digest = write_codex_home(folders.paths.codex_home)
        launcher = CodexLauncher(Path(REAL_CODEX), folders.paths.codex_home, digest)
        report = await codex_l2.run_l2(launcher, folders, thread_params=_params)
    finally:
        folders.close()
    assert report.failures == ()
    assert report.passed
    assert report.home_digest == digest


# #41 A 리뷰 후속(C6.23)


def test_l2_allowed_items_match_the_l3_allowed_items():
    """#41 A 리뷰 4번: L2와 L3가 같은 허용 항목으로 판정한다."""
    from guard_app.services.codex_session import L3_ALLOWED_ITEMS

    assert codex_l2.ALLOWED_ITEM_TYPES == L3_ALLOWED_ITEMS


def _codes_in_module() -> set[str]:
    """`codex_l2` 소스에서 `L2_` 고정 코드 문자열을 모은다. 상수 정의 자체는 뺀다."""
    import ast
    import re

    tree = ast.parse(Path(codex_l2.__file__).read_text(encoding="utf-8"))
    skip = {
        id(node)
        for assign in ast.walk(tree)
        if isinstance(assign, ast.Assign)
        and any(getattr(t, "id", None) == "L2_FAILURE_CODES" for t in assign.targets)
        for node in ast.walk(assign)
    }
    return {
        node.value
        for node in ast.walk(tree)
        if id(node) not in skip
        and isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and re.fullmatch(r"L2_[A-Z_]+", node.value)
    }


def test_l2_failure_codes_are_exactly_the_codes_the_module_uses():
    """#83 C 리뷰 참고 4: A가 사본 대신 쓸 상수. 코드를 더하거나 빼면 상수도 함께 바뀐다."""
    assert _codes_in_module() == codex_l2.L2_FAILURE_CODES
    assert len(codex_l2.L2_FAILURE_CODES) == 29


def test_a_l2_failure_codes_are_the_c_constant():
    """A151 이름(`session_preparation.L2_FAILURE_CODES`)이 사본이 아니라 C 상수인지(C6.14-2)."""
    from guard_app.services.session_preparation import L2_FAILURE_CODES

    assert L2_FAILURE_CODES is codex_l2.L2_FAILURE_CODES


@pytest.mark.asyncio
@pytest.mark.parametrize("deadline", [0, -1, float("inf"), float("nan"), True, "30", 601])
async def test_invalid_deadline_is_refused_before_anything_starts(tmp_path, deadline):
    with pytest.raises(codex_l2.L2Error) as raised:
        await codex_l2.observe_l2(None, None, thread_params=_params, deadline_s=deadline)
    assert str(raised.value) == "L2_DEADLINE_INVALID"


@pytest.mark.skipif(sys.platform != "win32", reason="launcher는 Windows 전용이다")
@pytest.mark.asyncio
async def test_deadline_ends_a_codex_that_never_answers(tmp_path):
    """#41 A 리뷰 2번: 응답하지 않는 Codex는 상한에서 끊고, 종료 확인과 폴더 삭제까지 한다."""
    import hashlib
    import time

    from guard_app.security.launcher import LaunchSpec, launch_process
    from guard_app.security.paths import prepare_app_folders

    data = PYTHON.read_bytes()

    class SilentCodex:
        """요청을 읽기만 하고 답하지 않다가 stdin EOF에 끝나는 대역."""

        async def launch_l2(self, task_dir, port, token):
            return await launch_process(
                LaunchSpec(
                    executable=PYTHON,
                    executable_size=len(data),
                    executable_sha256=hashlib.sha256(data).hexdigest(),
                    args=("-I", "-c", "import sys; sys.stdin.buffer.read()"),
                    env=(("SYSTEMROOT", os.environ["SYSTEMROOT"]),),
                    cwd=task_dir,
                    stderr_path=task_dir / "stderr.log",
                )
            )

    folders = prepare_app_folders(tmp_path / "app")
    try:
        started = time.perf_counter()
        observation = await codex_l2.observe_l2(
            SilentCodex(), folders, thread_params=_params, deadline_s=0.5
        )
        elapsed = time.perf_counter() - started
        assert list(folders.paths.tasks.iterdir()) == []
    finally:
        folders.close()
    assert observation.errors == ["L2_DEADLINE_EXCEEDED"]
    assert observation.exit_confirmed
    assert observation.folder_removed
    assert elapsed < 5
    assert "L2_DEADLINE_EXCEEDED" in _judge(observation)
