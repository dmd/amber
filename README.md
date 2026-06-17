# AMBER

**A**ccess **M**ethod for **B**ibliographic **E**lectronic **R**ecords — a Dynix-style terminal search for the local LibraryThing (JSON export) and Calibre databases.

Catalog files live in `./data/`, e.g. in my case:

- `data/librarything_*.json` — LibraryThing JSON export (loaded by default)
- `data/metadata-dmd.db` as `DANIEL` in the format/library column
- `data/metadata-cad.db` as `CELESTE` in the format/library column

The two `metadata-*.db` files are auto-detected in `data/`. Pass `--no-ebooks` to `build_catalog.py` to skip them, or `--catalog PATH` to point at a specific LibraryThing export.

## Layout

- `catalog.py` — the data layer: loads LibraryThing + Calibre records and implements search/scoring. No UI; imported by the build script and exercised by the tests.
- `build_catalog.py` — build step that turns `data/` into `static/catalog.json`.
- `static/` — the deployable, fully client-side site (see [static/README.md](static/README.md)).

## Web (static site)

The site is fully static — no application server. `build_catalog.py` uses
`catalog.py` to precompute the catalog, and everything (search **and** the 80x25
terminal UI) runs client-side in the browser.

### 1. Build the catalog

Run from the repo root whenever `data/` changes:

```sh
./build_catalog.py                 # reads data/, writes static/catalog.json
./build_catalog.py --no-ebooks     # LibraryThing only
./build_catalog.py --catalog PATH  # explicit catalog path
```

### 2. Serve `static/`

Point any static file server at the `static/` directory. With Caddy:

```caddyfile
amber.example.com {
    root * /path/to/amber/static
    file_server
}
```

Or test locally:

```sh
cd static && python3 -m http.server 8000   # http://localhost:8000/
```

`static/` is self-contained (xterm.js is vendored under `static/vendor/`). The only
generated artifact is `static/catalog.json`; rebuild it with `build_catalog.py`.

## Tests

```sh
./test_amber.py        # or: python3 -m unittest test_amber
```
