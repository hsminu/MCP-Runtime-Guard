"""Contract 공통 차단 대역 자체 검증: 이벤트 루프 self-pipe만 허용한다."""

import asyncio
import socket

import pytest


def test_event_loop_self_pipe_is_allowed():
    asyncio.run(asyncio.sleep(0))


@pytest.mark.parametrize("method", ["connect", "connect_ex"])
def test_other_connections_are_blocked_even_on_loopback(block_network, method):
    with (
        socket.create_server(("127.0.0.1", 0)) as listener,
        socket.socket() as client,
        pytest.raises(AssertionError, match="^Unexpected live network in contract test$"),
    ):
        getattr(client, method)(listener.getsockname())
    assert block_network.call_count == 1
    block_network.reset_mock()
