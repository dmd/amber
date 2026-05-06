#!/usr/bin/env -S UV_CACHE_DIR=/private/tmp/codex-uv-cache uv run --script
# /// script
# dependencies = []
# ///
"""Serve amber.py over a small telnet-compatible PTY bridge."""

from __future__ import annotations

import argparse
import os
import pty
import select
import signal
import socket
import struct
import sys
import termios
import threading
from dataclasses import dataclass
from typing import Callable


IAC = 255
DONT = 254
DO = 253
WONT = 252
WILL = 251
SB = 250
SE = 240
IP = 244
BREAK = 243

OPT_ECHO = 1
OPT_SUPPRESS_GO_AHEAD = 3
OPT_TERMINAL_TYPE = 24
OPT_NAWS = 31
TTYPE_SEND = 1


SUPPORTED_DO = {OPT_ECHO, OPT_SUPPRESS_GO_AHEAD}
SUPPORTED_WILL = {OPT_SUPPRESS_GO_AHEAD, OPT_TERMINAL_TYPE, OPT_NAWS}

DEFAULT_MAX_SESSIONS = 32


@dataclass
class WindowSize:
    cols: int
    rows: int


class TelnetInput:
    """Strip telnet command bytes and collect small negotiation responses."""

    def __init__(
        self,
        on_naws: Callable[[int, int], None] | None = None,
        on_interrupt: Callable[[], None] | None = None,
    ) -> None:
        self.state = "data"
        self.command = 0
        self.suboption = 0
        self.subdata = bytearray()
        self.responses = bytearray()
        self.on_naws = on_naws
        self.on_interrupt = on_interrupt

    def feed(self, data: bytes) -> bytes:
        out = bytearray()
        for byte in data:
            if self.state == "data":
                if byte == IAC:
                    self.state = "iac"
                else:
                    out.append(byte)
            elif self.state == "iac":
                if byte == IAC:
                    out.append(IAC)
                    self.state = "data"
                elif byte in (DO, DONT, WILL, WONT):
                    self.command = byte
                    self.state = "command"
                elif byte == SB:
                    self.state = "suboption"
                elif byte in (IP, BREAK):
                    if self.on_interrupt:
                        self.on_interrupt()
                    self.state = "data"
                else:
                    self.state = "data"
            elif self.state == "command":
                self.negotiate(self.command, byte)
                self.state = "data"
            elif self.state == "suboption":
                self.suboption = byte
                self.subdata = bytearray()
                self.state = "subdata"
            elif self.state == "subdata":
                if byte == IAC:
                    self.state = "subiac"
                else:
                    self.subdata.append(byte)
            elif self.state == "subiac":
                if byte == IAC:
                    self.subdata.append(IAC)
                    self.state = "subdata"
                elif byte == SE:
                    self.handle_suboption()
                    self.state = "data"
                else:
                    self.state = "data"
        return bytes(out)

    def negotiate(self, command: int, option: int) -> None:
        if command == DO:
            self.respond(WILL if option in SUPPORTED_DO else WONT, option)
        elif command == WILL:
            self.respond(DO if option in SUPPORTED_WILL else DONT, option)
            if option == OPT_TERMINAL_TYPE:
                self.responses.extend(bytes([IAC, SB, OPT_TERMINAL_TYPE, TTYPE_SEND, IAC, SE]))
        elif command in (DONT, WONT):
            return

    def handle_suboption(self) -> None:
        if self.suboption == OPT_NAWS and len(self.subdata) >= 4 and self.on_naws:
            cols, rows = struct.unpack("!HH", bytes(self.subdata[:4]))
            if cols > 0 and rows > 0:
                self.on_naws(cols, rows)

    def respond(self, command: int, option: int) -> None:
        self.responses.extend(bytes([IAC, command, option]))

    def drain_responses(self) -> bytes:
        data = bytes(self.responses)
        self.responses.clear()
        return data


def telnet_preamble() -> bytes:
    return bytes(
        [
            IAC,
            WILL,
            OPT_ECHO,
            IAC,
            WILL,
            OPT_SUPPRESS_GO_AHEAD,
            IAC,
            DO,
            OPT_SUPPRESS_GO_AHEAD,
            IAC,
            DO,
            OPT_NAWS,
            IAC,
            DO,
            OPT_TERMINAL_TYPE,
        ]
    )


def escape_telnet_output(data: bytes) -> bytes:
    return data.replace(bytes([IAC]), bytes([IAC, IAC]))


def set_window_size(fd: int, size: WindowSize) -> None:
    packed = struct.pack("HHHH", size.rows, size.cols, 0, 0)
    try:
        termios.tcsetwinsize(fd, (size.rows, size.cols))
    except AttributeError:
        import fcntl

        fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)
    except OSError:
        pass


def spawn_amber(args: argparse.Namespace, size: WindowSize) -> tuple[int, int]:
    pid, master_fd = pty.fork()
    if pid == 0:
        env = os.environ.copy()
        env["TERM"] = args.term
        env["LINES"] = str(size.rows)
        env["COLUMNS"] = str(size.cols)
        os.chdir(args.directory)
        command = [os.path.join(args.directory, "amber.py"), "--theme", args.theme]
        if args.catalog:
            command.extend(["--catalog", args.catalog])
        os.execvpe(command[0], command, env)
    set_window_size(master_fd, size)
    return pid, master_fd


def close_child(pid: int, master_fd: int) -> None:
    if master_fd >= 0:
        try:
            os.close(master_fd)
        except OSError:
            pass
    if pid > 0:
        try:
            os.kill(pid, signal.SIGHUP)
        except ProcessLookupError:
            pass
        except OSError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
        except OSError:
            pass


def bridge_client(conn: socket.socket, addr: tuple[str, int], args: argparse.Namespace) -> None:
    size = WindowSize(cols=args.cols, rows=args.rows)
    pid = -1
    master_fd = -1

    def update_size(cols: int, rows: int) -> None:
        size.cols = cols
        size.rows = rows
        if master_fd >= 0:
            set_window_size(master_fd, size)

    def interrupt_child() -> None:
        if pid > 0:
            try:
                os.kill(pid, signal.SIGINT)
            except OSError:
                pass

    parser = TelnetInput(on_naws=update_size, on_interrupt=interrupt_child)

    try:
        conn.sendall(telnet_preamble())
        pid, master_fd = spawn_amber(args, size)
        while True:
            readable, _, _ = select.select([conn, master_fd], [], [])
            if conn in readable:
                data = conn.recv(4096)
                if not data:
                    break
                user_bytes = parser.feed(data)
                responses = parser.drain_responses()
                if responses:
                    conn.sendall(responses)
                if user_bytes:
                    os.write(master_fd, user_bytes)
            if master_fd in readable:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                conn.sendall(escape_telnet_output(data))
    finally:
        close_child(pid, master_fd)
        try:
            conn.close()
        except OSError:
            pass
        if args.verbose:
            print(f"Disconnected {addr[0]}:{addr[1]}", flush=True)


def serve(args: argparse.Namespace) -> int:
    semaphore = threading.BoundedSemaphore(args.max_sessions)

    def session(conn: socket.socket, addr: tuple[str, int]) -> None:
        try:
            bridge_client(conn, addr, args)
        finally:
            semaphore.release()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((args.host, args.port))
        server.listen(args.backlog)
        print(
            f"AMBER telnet listening on {args.host}:{args.port} "
            f"(catalog directory: {args.directory}, max sessions: {args.max_sessions})",
            flush=True,
        )
        while True:
            conn, addr = server.accept()
            if not semaphore.acquire(blocking=False):
                if args.verbose:
                    print(f"Rejected (busy) {addr[0]}:{addr[1]}", flush=True)
                try:
                    conn.sendall(b"\r\nAMBER busy. Try again shortly.\r\n")
                except OSError:
                    pass
                try:
                    conn.close()
                except OSError:
                    pass
                continue
            if args.verbose:
                print(f"Connected {addr[0]}:{addr[1]}", flush=True)
            thread = threading.Thread(target=session, args=(conn, addr), daemon=True)
            thread.start()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve AMBER over telnet")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host, defaults to localhost")
    parser.add_argument("--port", type=int, default=2323, help="Bind port, defaults to 2323")
    parser.add_argument("--catalog", help="Optional catalog path passed to amber.py")
    parser.add_argument("--theme", choices=("amber", "green"), default="amber")
    parser.add_argument("--term", default="xterm-256color", help="TERM value for spawned sessions")
    parser.add_argument("--cols", type=int, default=80, help="Default terminal columns before NAWS")
    parser.add_argument("--rows", type=int, default=24, help="Default terminal rows before NAWS")
    parser.add_argument("--backlog", type=int, default=5)
    parser.add_argument(
        "--max-sessions",
        type=int,
        default=DEFAULT_MAX_SESSIONS,
        help="Maximum concurrent telnet sessions; extra connections are dropped",
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--directory",
        default=os.path.dirname(os.path.abspath(__file__)),
        help="Directory containing amber.py and the catalog JSON",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return serve(args)
    except KeyboardInterrupt:
        print("\nAMBER telnet stopped.", flush=True)
        return 130
    except OSError as exc:
        print(f"amber_telnet: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
