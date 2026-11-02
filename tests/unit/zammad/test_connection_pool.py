"""Exercise the owned client's connection bound against a synthetic local server."""

import asyncio
import socket

from pydantic import SecretStr

from chronikwerk.configuration.zammad import ZammadConnection
from chronikwerk.zammad.gateway import AsyncZammadClient

_SOCKET_CONNECT = socket.socket.connect


def test_shared_client_bounds_simultaneous_upstream_connections(monkeypatch) -> None:
    async def scenario() -> None:
        active = 0
        peak = 0
        full = asyncio.Event()
        release = asyncio.Event()
        handlers: set[asyncio.Task] = set()

        async def handle(reader, writer) -> None:
            nonlocal active, peak
            task = asyncio.current_task()
            assert task is not None
            handlers.add(task)
            try:
                await reader.readuntil(b"\r\n\r\n")
                active += 1
                peak = max(peak, active)
                if active == 10:
                    full.set()
                await release.wait()
                body = b'["synthetic"]'
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                    + body
                )
                await writer.drain()
                active -= 1
            finally:
                writer.close()
                await writer.wait_closed()
                handlers.discard(task)

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        def connect_local(sock, address):
            # This fixture permits only this synthetic listener, never external traffic.
            assert address == ("127.0.0.1", port)
            return _SOCKET_CONNECT(sock, address)

        monkeypatch.setattr(socket.socket, "connect", connect_local)
        connection = ZammadConnection(
            origin=f"http://127.0.0.1:{port}",
            api_token=SecretStr("synthetic"),
            allow_private_origin=True,
            allow_insecure_http=True,
        )
        async with server, AsyncZammadClient(connection=connection) as client:
            tasks = [asyncio.create_task(client.list_tags(7)) for _ in range(15)]
            try:
                await asyncio.wait_for(full.wait(), 5)
                await asyncio.sleep(0.02)
                assert peak == 10
                assert not any(task.done() for task in tasks)
            finally:
                release.set()
                results = await asyncio.wait_for(asyncio.gather(*tasks), 5)
            assert len(results) == 15
            assert peak == 10
        await asyncio.gather(*handlers)

    asyncio.run(scenario())
