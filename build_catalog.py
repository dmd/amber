#!/usr/bin/env -S uv run --script
# /// script
# dependencies = []
# ///
"""Build the static-site catalog JSON from the catalog.py data layer.

Loads the LibraryThing export plus any Calibre databases, then dumps every
normalized record (display fields *and* the precomputed search indexes) to
static/catalog.json. The browser app reads this file and runs the same scoring
logic client-side, so search behaviour matches catalog.py without re-deriving
any normalization in JS.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

import catalog as catalog_lib


def build(catalog_path: str | None, no_ebooks: bool) -> dict:
    catalog_path = catalog_path or catalog_lib.find_default_catalog()
    catalog_dir = os.path.dirname(os.path.abspath(catalog_path)) or "."
    ebook_databases = [] if no_ebooks else catalog_lib.default_ebook_databases(catalog_dir)
    books = catalog_lib.load_combined_catalog(catalog_path, ebook_databases)

    records = []
    for book in books:
        record = asdict(book)
        # ebook_marker is a property, not a field; include it so the JS detail
        # view doesn't have to reproduce the rule.
        record["ebook_marker"] = book.ebook_marker
        records.append(record)

    return {
        "app_title": catalog_lib.APP_TITLE,
        "module_title": catalog_lib.MODULE_TITLE,
        "user": catalog_lib.DEFAULT_USER,
        "count": len(records),
        "books": records,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build static catalog.json for the browser app")
    parser.add_argument("--catalog", help="Path to LibraryThing JSON export")
    parser.add_argument("--no-ebooks", action="store_true", help="Do not load Calibre ebook databases")
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parent / "static" / "catalog.json"),
        help="Output path for the generated catalog JSON",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    payload = build(args.catalog, args.no_ebooks)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))

    size = out_path.stat().st_size
    print(f"wrote {payload['count']} records to {out_path} ({size / 1_048_576:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
