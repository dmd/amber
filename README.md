# CatPAC

Dynix-style terminal search for the local LibraryThing JSON export.

By default, CatPAC also loads the local Calibre databases:

- `metadata-dmd.db` as `DANIEL` in the format/library column
- `metadata-cad.db` as `CELESTE` in the format/library column

Use `--no-ebooks` to search only the LibraryThing export, or `--ebook-db NAME=PATH` to add another Calibre metadata database.

## Local TUI

```sh
./catpac.py
./catpac.py --theme green
./catpac.py --check
./catpac.py --no-ebooks
```

## Telnet Server

Start the local telnet bridge:

```sh
./catpac_telnet.py --host 127.0.0.1 --port 2323
```

Connect locally from another terminal:

```sh
telnet 127.0.0.1 2323
```

## Public TCP Tunnel

With the telnet server running locally, expose it through ngrok:

```sh
ngrok tcp 2323
```

Ngrok prints a forwarding address like `tcp://0.tcp.ngrok.io:12345`. Connect to it with:

```sh
telnet 0.tcp.ngrok.io 12345
```

The telnet server binds to localhost by default. Use `--host 0.0.0.0` only when you explicitly want it reachable on the local network without a tunnel.

## Web Terminal

Build and run the Docker image:

```sh
docker build -t catpac-web .
docker run --rm -p 2380:2380 catpac-web
```

Open:

```text
http://localhost:2380/
```

The browser UI is an 80x25 xterm.js terminal connected to the same CatPAC curses app over a WebSocket PTY bridge.
