import argparse
import asyncio
from contextlib import suppress
from urllib.parse import urlsplit

from aiohttp import web


class FakeHTTPProxy:
    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._targets: list[str] = []

    async def healthcheck(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def list_targets(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "result": self._targets})

    async def wait_for_targets(self, request: web.Request) -> web.Response:
        expected_count = int(request.query.get("count", 1))
        timeout = float(request.query.get("timeout", 10))

        async with self._condition:
            try:
                async with asyncio.timeout(timeout):
                    await self._condition.wait_for(lambda: len(self._targets) >= expected_count)
            except TimeoutError:
                raise web.HTTPRequestTimeout from None

        return web.json_response({"ok": True, "result": self._targets})

    async def handle_connection(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        upstream_writer: asyncio.StreamWriter | None = None
        try:
            request_line = await client_reader.readline()
            method, target, _ = request_line.decode("ascii").strip().split(" ", maxsplit=2)
            while await client_reader.readline() not in {b"\r\n", b"\n", b""}:
                pass

            if method != "CONNECT":
                client_writer.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
                await client_writer.drain()
                return

            async with self._condition:
                self._targets.append(target)
                self._condition.notify_all()

            parsed_target = urlsplit(f"//{target}")
            if parsed_target.hostname is None or parsed_target.port is None:
                client_writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                await client_writer.drain()
                return

            try:
                upstream_reader, upstream_writer = await asyncio.open_connection(
                    parsed_target.hostname,
                    parsed_target.port,
                )
            except OSError:
                client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                await client_writer.drain()
                return

            client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await client_writer.drain()

            await asyncio.gather(
                self._relay(client_reader, upstream_writer),
                self._relay(upstream_reader, client_writer),
            )
        except UnicodeDecodeError, ValueError:
            client_writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            await client_writer.drain()
        finally:
            if upstream_writer is not None:
                upstream_writer.close()
                with suppress(ConnectionError):
                    await upstream_writer.wait_closed()
            client_writer.close()
            with suppress(ConnectionError):
                await client_writer.wait_closed()

    @staticmethod
    async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while data := await reader.read(64 * 1024):
                writer.write(data)
                await writer.drain()
        except ConnectionError:
            pass


async def run(proxy_port: int, control_port: int) -> None:
    proxy = FakeHTTPProxy()
    app = web.Application()
    app.router.add_get("/healthz", proxy.healthcheck)
    app.router.add_get("/test/targets", proxy.list_targets)
    app.router.add_get("/test/wait", proxy.wait_for_targets)

    runner = web.AppRunner(app)
    await runner.setup()
    proxy_server = await asyncio.start_server(proxy.handle_connection, "127.0.0.1", proxy_port)
    await web.TCPSite(runner, "127.0.0.1", control_port).start()

    try:
        await proxy_server.serve_forever()
    finally:
        proxy_server.close()
        await proxy_server.wait_closed()
        await runner.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proxy-port", type=int, required=True)
    parser.add_argument("--control-port", type=int, required=True)
    args = parser.parse_args()

    asyncio.run(run(args.proxy_port, args.control_port))


if __name__ == "__main__":
    main()
