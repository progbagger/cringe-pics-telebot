import argparse
import asyncio
from typing import Any

from aiohttp import web


class FakeStatsD(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._metrics: list[dict[str, Any]] = []

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        asyncio.create_task(self._record_datagram(data, addr))

    async def _record_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        metrics = [_parse_metric(line, addr) for line in data.decode("ascii").splitlines() if line]
        async with self._condition:
            self._metrics.extend(metrics)
            self._condition.notify_all()

    async def healthcheck(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def list_metrics(self, request: web.Request) -> web.Response:
        name = request.query.get("name")
        async with self._condition:
            metrics = self._metrics
            if name is not None:
                metrics = [metric for metric in metrics if metric["name"] == name]
            return web.json_response({"ok": True, "result": metrics})

    async def wait_for_metric(self, request: web.Request) -> web.Response:
        name = request.query["name"]
        timeout = float(request.query.get("timeout", "10"))
        try:
            async with asyncio.timeout(timeout):
                async with self._condition:
                    while not (metrics := [metric for metric in self._metrics if metric["name"] == name]):
                        await self._condition.wait()
        except TimeoutError as error:
            raise web.HTTPRequestTimeout(text=f"Metric {name!r} was not received") from error
        return web.json_response({"ok": True, "result": metrics[0]})

    async def reset(self, request: web.Request) -> web.Response:
        async with self._condition:
            self._metrics.clear()
            self._condition.notify_all()
        return web.json_response({"ok": True})


def _parse_metric(line: str, addr: tuple[str, int]) -> dict[str, Any]:
    name, payload = line.split(":", maxsplit=1)
    value, metric_type, *_ = payload.split("|")
    parsed_value = int(value) if metric_type == "c" else float(value)
    return {
        "name": name,
        "value": parsed_value,
        "type": metric_type,
        "source": {"host": addr[0], "port": addr[1]},
    }


def create_app(fake: FakeStatsD) -> web.Application:
    app = web.Application()
    app.router.add_get("/healthz", fake.healthcheck)
    app.router.add_get("/test/metrics", fake.list_metrics)
    app.router.add_get("/test/wait", fake.wait_for_metric)
    app.router.add_post("/test/reset", fake.reset)
    return app


async def serve(*, host: str, http_port: int, udp_port: int) -> None:
    fake = FakeStatsD()
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(lambda: fake, local_addr=(host, udp_port))
    runner = web.AppRunner(create_app(fake))
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=http_port)
    await site.start()
    try:
        await asyncio.Event().wait()
    finally:
        transport.close()
        await runner.cleanup()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--http-port", type=int, required=True)
    parser.add_argument("--udp-port", type=int, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(serve(host=args.host, http_port=args.http_port, udp_port=args.udp_port))


if __name__ == "__main__":
    main()
