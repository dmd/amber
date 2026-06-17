# AMBER — static / serverless edition

A 100% client-side build of AMBER. Same 80×25 amber-phosphor terminal look as the
PTY web version, but there is **no backend**: the catalog is precomputed into
`catalog.json` and all search + rendering runs in the browser. Drop these files on
any static host (S3+CloudFront, Cloudflare Pages, GitHub Pages, nginx, …).

## Files

| File | What it is |
|------|------------|
| `index.html` | Page shell + CRT bezel |
| `styles.css` | CRT styling (amber default; `?theme=green` supported) |
| `app.js` | JS port of amber.py's search engine **and** the DynixApp UI, driving xterm.js |
| `catalog.json` | Precomputed catalog (display fields + search indexes) — **embeds your library data** |
| `vendor/` | Vendored xterm.js + canvas addon + xterm.css (self-contained, no CDN at runtime) |

## Rebuild the catalog

Regenerate `catalog.json` whenever `data/` changes. Run from the repo root:

```sh
./build_catalog.py            # reads data/ exactly like amber.py, writes static/catalog.json
./build_catalog.py --no-ebooks
```

`build_catalog.py` reuses amber.py's `load_combined_catalog`, so the data layer
stays single-sourced — search behaviour is verified byte-identical to the TUI.

## Run locally

`fetch()` needs HTTP (not `file://`), so serve the directory:

```sh
cd static && python3 -m http.server 8000
# open http://localhost:8000/
```

## Notes

- `catalog.json` is ~5 MB raw, ~1 MB gzipped — enable gzip/brotli on your host.
- It contains the full catalog (titles, authors, summaries). Anyone who loads the
  page can download it; that's the tradeoff of going fully static vs. a search API.
- `.gitignore` excludes `data/` source files but **not** `static/catalog.json` —
  decide whether you want the generated data file in git.
