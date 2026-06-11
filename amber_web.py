#!/usr/bin/env -S UV_CACHE_DIR=/private/tmp/amber-uv-cache uv run --script
# /// script
# dependencies = [
#   "aiohttp",
# ]
# ///
"""Serve AMBER as an 80x25 browser terminal."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import pty
import signal
import struct
import sys
import termios
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web


COLS = 80
ROWS = 25
DEFAULT_MAX_SESSIONS = 32


class SessionLimiter:
    """Cap concurrent PTY-backed sessions. Single-threaded asyncio, no lock needed."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.active = 0

    def try_acquire(self) -> bool:
        if self.active >= self.limit:
            return False
        self.active += 1
        return True

    def release(self) -> None:
        if self.active > 0:
            self.active -= 1


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    directory: Path
    theme: str
    catalog: str | None
    no_ebooks: bool
    term: str
    max_sessions: int


def set_window_size(fd: int, rows: int = ROWS, cols: int = COLS) -> None:
    packed = struct.pack("HHHH", rows, cols, 0, 0)
    try:
        termios.tcsetwinsize(fd, (rows, cols))
    except AttributeError:
        import fcntl

        fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)
    except OSError:
        pass


def spawn_amber(config: ServerConfig) -> tuple[int, int]:
    pid, master_fd = pty.fork()
    if pid == 0:
        env = os.environ.copy()
        env["TERM"] = config.term
        env["LINES"] = str(ROWS)
        env["COLUMNS"] = str(COLS)
        os.chdir(config.directory)
        command = [str(config.directory / "amber.py"), "--theme", config.theme]
        if config.catalog:
            command.extend(["--catalog", config.catalog])
        if config.no_ebooks:
            command.append("--no-ebooks")
        os.execvpe(command[0], command, env)
    set_window_size(master_fd)
    os.set_blocking(master_fd, False)
    return pid, master_fd


def close_child(pid: int, master_fd: int) -> None:
    if master_fd >= 0:
        with contextlib.suppress(OSError):
            os.close(master_fd)
    if pid > 0:
        with contextlib.suppress(ProcessLookupError, OSError):
            os.kill(pid, signal.SIGHUP)
        with contextlib.suppress(ChildProcessError, OSError):
            os.waitpid(pid, 0)


async def terminal_ws(request: web.Request) -> web.StreamResponse:
    config: ServerConfig = request.app["config"]
    limiter: SessionLimiter = request.app["limiter"]
    if not limiter.try_acquire():
        return web.Response(
            status=503,
            text="amber: too many active sessions, try again shortly\n",
            content_type="text/plain",
        )
    ws = web.WebSocketResponse(max_msg_size=1024 * 1024)
    try:
        await ws.prepare(request)
    except Exception:
        limiter.release()
        raise

    pid, master_fd = spawn_amber(config)
    loop = asyncio.get_running_loop()
    output: asyncio.Queue[bytes | None] = asyncio.Queue()
    closed = False

    def read_pty() -> None:
        nonlocal closed
        if closed:
            return
        try:
            data = os.read(master_fd, 4096)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if data:
            output.put_nowait(data)
        else:
            closed = True
            with contextlib.suppress(Exception):
                loop.remove_reader(master_fd)
            output.put_nowait(None)

    loop.add_reader(master_fd, read_pty)

    async def sender() -> None:
        while True:
            data = await output.get()
            if data is None:
                break
            await ws.send_bytes(data)

    sender_task = asyncio.create_task(sender())

    try:
        async for message in ws:
            if message.type == WSMsgType.BINARY:
                payload = message.data
            elif message.type == WSMsgType.TEXT:
                payload = message.data.encode("utf-8")
            elif message.type in (WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSED):
                break
            else:
                continue
            if payload:
                with contextlib.suppress(OSError):
                    os.write(master_fd, payload)
    finally:
        closed = True
        with contextlib.suppress(Exception):
            loop.remove_reader(master_fd)
        output.put_nowait(None)
        sender_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sender_task
        close_child(pid, master_fd)
        limiter.release()

    return ws


async def index(request: web.Request) -> web.FileResponse:
    static_dir: Path = request.app["static_dir"]
    return web.FileResponse(static_dir / "index.html")


async def healthz(_request: web.Request) -> web.Response:
    return web.Response(text="ok\n", content_type="text/plain")


def create_app(config: ServerConfig) -> web.Application:
    app = web.Application()
    root = Path(__file__).resolve().parent
    static_dir = root / "web"
    xterm_dir = root / "node_modules" / "@xterm" / "xterm"
    canvas_dir = root / "node_modules" / "@xterm" / "addon-canvas"
    app["config"] = config
    app["static_dir"] = static_dir
    app["limiter"] = SessionLimiter(config.max_sessions)
    app.router.add_get("/", index)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/ws", terminal_ws)
    app.router.add_static("/static", static_dir, show_index=False)
    if xterm_dir.exists():
        app.router.add_static("/xterm", xterm_dir, show_index=False)
    if canvas_dir.exists():
        app.router.add_static("/xterm-canvas", canvas_dir, show_index=False)
    return app


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve AMBER as a browser terminal")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=2380)
    parser.add_argument("--directory", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--theme", choices=("amber", "green"), default="amber")
    parser.add_argument("--catalog", help="Optional LibraryThing catalog path passed to amber.py")
    parser.add_argument("--no-ebooks", action="store_true", help="Do not load Calibre ebook databases")
    parser.add_argument("--term", default="xterm-256color")
    parser.add_argument(
        "--max-sessions",
        type=int,
        default=DEFAULT_MAX_SESSIONS,
        help="Maximum concurrent terminal sessions; extra upgrades get HTTP 503",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    config = ServerConfig(
        host=args.host,
        port=args.port,
        directory=Path(args.directory).resolve(),
        theme=args.theme,
        catalog=args.catalog,
        no_ebooks=args.no_ebooks,
        term=args.term,
        max_sessions=args.max_sessions,
    )
    app = create_app(config)
    web.run_app(app, host=config.host, port=config.port, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
