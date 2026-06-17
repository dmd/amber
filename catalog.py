"""AMBER catalog data layer.

Loads the LibraryThing JSON export and any Calibre metadata databases, normalizes
every record, and implements the search/scoring logic. This is the single source
of truth for catalog data and search behaviour: build_catalog.py imports it to
generate static/catalog.json, and the browser app reimplements the same scoring
client-side. (There is no terminal UI any more — everything is the web build.)
"""

from __future__ import annotations

import glob
import html
import os
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable


APP_TITLE = "AUTOMATED LIBRARY SYSTEM"
MODULE_TITLE = "Outer Dibblestan Catalog"
DEFAULT_USER = "ddrucker"

DEFAULT_EBOOK_DATABASES = (
    ("DANIEL", "metadata-dmd.db"),
    ("CELESTE", "metadata-cad.db"),
)

DEFAULT_DATA_DIR = "data"


@dataclass(frozen=True)
class Book:
    book_id: str
    title: str
    primary_author: str
    authors: tuple[str, ...]
    isbns: tuple[str, ...]
    publication: str
    date: str
    collections: tuple[str, ...]
    ddc_codes: tuple[str, ...]
    ddc_words: tuple[str, ...]
    lcc_codes: tuple[str, ...]
    series: tuple[str, ...]
    genre: tuple[str, ...]
    awards: tuple[str, ...]
    entrydate: str
    formats: tuple[str, ...]
    copies: str
    summary: str
    source: str
    ebook_library: str
    search_fields: dict[str, str]
    compact_ids: str

    @property
    def ebook_marker(self) -> str:
        if not self.ebook_library:
            return ""
        return f"EBOOK: {self.ebook_library}"


@dataclass(frozen=True)
class SearchResult:
    book: Book
    score: int


def unique(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return tuple(out)


def strings_from(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (int, float)):
        return [str(value)]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(strings_from(item))
        return out
    if isinstance(value, tuple):
        out = []
        for item in value:
            out.extend(strings_from(item))
        return out
    if isinstance(value, dict):
        out = []
        for key in ("text", "fl", "lf", "code", "wording", "name", "value"):
            if key in value:
                out.extend(strings_from(value[key]))
        if not out:
            for item in value.values():
                out.extend(strings_from(item))
        return out
    return [str(value)]


def author_strings(record: dict[str, Any]) -> tuple[str, ...]:
    authors: list[str] = []
    raw_authors = record.get("authors")
    if isinstance(raw_authors, list):
        for author in raw_authors:
            if isinstance(author, dict):
                lf = str(author.get("lf") or "").strip()
                fl = str(author.get("fl") or "").strip()
                role = str(author.get("role") or "").strip()
                if lf:
                    authors.append(f"{lf} ({role})" if role else lf)
                if fl and fl.casefold() != lf.casefold():
                    authors.append(f"{fl} ({role})" if role else fl)
            else:
                authors.extend(strings_from(author))
    for key in ("primaryauthor", "secondaryauthor"):
        authors.extend(strings_from(record.get(key)))
    return unique(authors)


def format_strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, dict):
                out.extend(strings_from(item.get("text")))
            else:
                out.extend(strings_from(item))
        return unique(out)
    if isinstance(value, dict):
        return unique(strings_from(value.get("text")))
    return unique(strings_from(value))


def first_value(values: Iterable[str]) -> str:
    for value in values:
        text = str(value).strip()
        if text:
            return text
    return ""


def normalize_text(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def normalize_compact(text: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+", text.casefold()))


def normalize_date_key(text: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", text or "")
    if not match:
        return (0, 0, 0)
    year = int(match.group(1))
    month = int(match.group(2) or 1)
    day = int(match.group(3) or 1)
    return (year, month, day)


def normalize_record(book_id: str, record: dict[str, Any]) -> Book:
    title = first_value(strings_from(record.get("title")))
    authors = author_strings(record)
    primary_author = first_value(strings_from(record.get("primaryauthor"))) or (
        authors[0] if authors else ""
    )
    isbns = unique(
        [
            *strings_from(record.get("originalisbn")),
            *strings_from(record.get("isbn")),
            *strings_from(record.get("ean")),
            *strings_from(record.get("asin")),
        ]
    )
    ddc = record.get("ddc") if isinstance(record.get("ddc"), dict) else {}
    lcc = record.get("lcc")
    ddc_codes = unique(strings_from(ddc.get("code") if isinstance(ddc, dict) else None))
    ddc_words = unique(strings_from(ddc.get("wording") if isinstance(ddc, dict) else None))
    lcc_codes = unique(strings_from(lcc.get("code") if isinstance(lcc, dict) else lcc))
    formats = format_strings(record.get("format"))
    collections = unique(strings_from(record.get("collections")))
    series = unique(strings_from(record.get("series")))
    genre = unique(strings_from(record.get("genre")))
    awards = unique(strings_from(record.get("awards")))
    publication = first_value(strings_from(record.get("publication")))
    date = first_value(strings_from(record.get("date")))
    entrydate = first_value(strings_from(record.get("entrydate")))
    copies = first_value(strings_from(record.get("copies")))
    summary = first_value(strings_from(record.get("summary")))
    source = first_value(strings_from(record.get("source")))
    ebook_library = first_value(strings_from(record.get("ebook_library"))).upper()
    ebook_marker = f"EBOOK {ebook_library}" if ebook_library else ""

    fields = {
        "title": " ".join([title]),
        "author": " ".join([primary_author, *authors]),
        "isbn": " ".join(isbns),
        "subject": " ".join([*ddc_codes, *ddc_words, *lcc_codes, *genre, *collections, ebook_marker]),
        "series": " ".join(series),
        "keyword": " ".join(
            [
                title,
                primary_author,
                *authors,
                *isbns,
                publication,
                date,
                *collections,
                *ddc_codes,
                *ddc_words,
                *lcc_codes,
                *series,
                *genre,
                *awards,
                entrydate,
                *formats,
                copies,
                summary,
                source,
                ebook_marker,
                ebook_library,
                book_id,
            ]
        ),
    }
    search_fields = {name: normalize_text(value) for name, value in fields.items()}
    compact_ids = normalize_compact(" ".join([book_id, *isbns]))

    return Book(
        book_id=book_id,
        title=title,
        primary_author=primary_author,
        authors=authors,
        isbns=isbns,
        publication=publication,
        date=date,
        collections=collections,
        ddc_codes=ddc_codes,
        ddc_words=ddc_words,
        lcc_codes=lcc_codes,
        series=series,
        genre=genre,
        awards=awards,
        entrydate=entrydate,
        formats=formats,
        copies=copies,
        summary=summary,
        source=source,
        ebook_library=ebook_library,
        search_fields=search_fields,
        compact_ids=compact_ids,
    )


def load_catalog(path: str) -> list[Book]:
    import json

    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if isinstance(raw, dict):
        items = raw.items()
    elif isinstance(raw, list):
        items = ((str(index), item) for index, item in enumerate(raw, start=1))
    else:
        raise ValueError("Catalog JSON must be an object keyed by book ID or a list.")

    books: list[Book] = []
    for book_id, record in items:
        if not isinstance(record, dict):
            continue
        books.append(normalize_record(str(book_id), record))
    return books


def clean_html_text(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return " ".join(text.split())


def calibre_year(value: str) -> str:
    match = re.match(r"^(\d{4})", value or "")
    if not match:
        return ""
    year = int(match.group(1))
    if year < 1000:
        return ""
    return str(year)


def calibre_date(value: str) -> str:
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", value or "")
    if not match:
        return ""
    if match.group(1).startswith("0101-"):
        return ""
    return match.group(1)


def rows_by_book(conn: sqlite3.Connection, query: str) -> dict[int, tuple[str, ...]]:
    grouped: dict[int, list[str]] = {}
    for row in conn.execute(query):
        grouped.setdefault(int(row["book"]), []).append(str(row["value"]))
    return {book_id: unique(values) for book_id, values in grouped.items()}


def identifiers_by_book(conn: sqlite3.Connection) -> dict[int, tuple[str, ...]]:
    grouped: dict[int, list[str]] = {}
    for row in conn.execute(
        """
        SELECT book, type, val
        FROM identifiers
        ORDER BY book, type, val
        """
    ):
        id_type = str(row["type"] or "").strip()
        value = str(row["val"] or "").strip()
        if not value:
            continue
        grouped.setdefault(int(row["book"]), []).append(value)
        if id_type:
            grouped[int(row["book"])].append(f"{id_type}:{value}")
    return {book_id: unique(values) for book_id, values in grouped.items()}


def load_calibre_catalog(path: str, library_name: str) -> list[Book]:
    library_name = library_name.upper()
    marker = f"EBOOK: {library_name}"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        authors = rows_by_book(
            conn,
            """
            SELECT bal.book AS book, a.name AS value
            FROM books_authors_link bal
            JOIN authors a ON a.id = bal.author
            ORDER BY bal.book, bal.id
            """,
        )
        formats = rows_by_book(
            conn,
            """
            SELECT book, format AS value
            FROM data
            ORDER BY book, format
            """,
        )
        tags = rows_by_book(
            conn,
            """
            SELECT btl.book AS book, t.name AS value
            FROM books_tags_link btl
            JOIN tags t ON t.id = btl.tag
            ORDER BY btl.book, t.name
            """,
        )
        series = rows_by_book(
            conn,
            """
            SELECT bsl.book AS book, s.name AS value
            FROM books_series_link bsl
            JOIN series s ON s.id = bsl.series
            ORDER BY bsl.book, s.name
            """,
        )
        publishers = rows_by_book(
            conn,
            """
            SELECT bpl.book AS book, p.name AS value
            FROM books_publishers_link bpl
            JOIN publishers p ON p.id = bpl.publisher
            ORDER BY bpl.book, p.name
            """,
        )
        identifiers = identifiers_by_book(conn)
        comments = {
            int(row["book"]): clean_html_text(str(row["text"] or ""))
            for row in conn.execute("SELECT book, text FROM comments ORDER BY book")
        }

        books: list[Book] = []
        for row in conn.execute(
            """
            SELECT id, title, author_sort, pubdate, timestamp, series_index, path, uuid
            FROM books
            ORDER BY id
            """
        ):
            calibre_id = int(row["id"])
            book_authors = authors.get(calibre_id, ())
            primary_author = book_authors[0] if book_authors else str(row["author_sort"] or "").strip()
            year = calibre_year(str(row["pubdate"] or ""))
            publisher_text = join_display(publishers.get(calibre_id, ()))
            publication = " ".join(part for part in (publisher_text, f"({year})" if year else "") if part)
            record = {
                "title": str(row["title"] or "").strip(),
                "primaryauthor": primary_author,
                "authors": list(book_authors),
                "isbn": list(identifiers.get(calibre_id, ())),
                "publication": publication,
                "date": year,
                "collections": [marker],
                "series": list(series.get(calibre_id, ())),
                "genre": list(tags.get(calibre_id, ())),
                "entrydate": calibre_date(str(row["timestamp"] or "")),
                "format": list(formats.get(calibre_id, ("EBOOK",))),
                "copies": "1",
                "summary": comments.get(calibre_id, ""),
                "source": f"Calibre {os.path.basename(path)}",
                "ebook_library": library_name,
            }
            books.append(normalize_record(f"calibre-{library_name.casefold()}:{calibre_id}", record))
        return books
    finally:
        conn.close()


def find_default_catalog(directory: str = DEFAULT_DATA_DIR) -> str:
    candidates = sorted(glob.glob(os.path.join(directory, "librarything_*.json")))
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise FileNotFoundError(
            f"No librarything_*.json catalog file found in {directory!r}."
        )
    names = ", ".join(os.path.basename(path) for path in candidates)
    raise FileExistsError(f"Multiple catalog files found; use --catalog. Found: {names}")


def default_ebook_databases(directory: str = DEFAULT_DATA_DIR) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for library_name, filename in DEFAULT_EBOOK_DATABASES:
        path = os.path.join(directory, filename)
        if os.path.exists(path):
            found.append((library_name, path))
    return found


def load_combined_catalog(
    catalog_path: str,
    ebook_databases: Iterable[tuple[str, str]] = (),
) -> list[Book]:
    books = load_catalog(catalog_path)
    for library_name, db_path in ebook_databases:
        books.extend(load_calibre_catalog(db_path, library_name))
    return books


def tokenize_query(query: str) -> list[str]:
    return [token for token in normalize_text(query).split() if token]


def field_match_score(text: str, terms: list[str], phrase: str) -> int:
    if not text or not terms:
        return 0
    tokens = text.split()
    if phrase and text == phrase:
        return 900
    if phrase and token_sequence_starts(tokens, terms):
        return 760
    if phrase and token_sequence_contains(tokens, terms):
        return 620
    if all(term in tokens for term in terms):
        return 430
    if all(any(token_matches(term, token) for token in tokens) for term in terms):
        return 300
    matched = sum(1 for term in terms if any(token_matches(term, token) for token in tokens))
    if matched:
        return matched * 35
    return 0


def token_matches(term: str, token: str) -> bool:
    if term == token:
        return True
    if term.isdigit():
        return False
    return token.startswith(term)


def token_sequence_starts(tokens: list[str], terms: list[str]) -> bool:
    return len(tokens) >= len(terms) and tokens[: len(terms)] == terms


def token_sequence_contains(tokens: list[str], terms: list[str]) -> bool:
    if not terms or len(terms) > len(tokens):
        return False
    return any(tokens[index : index + len(terms)] == terms for index in range(len(tokens) - len(terms) + 1))


def score_book(book: Book, mode: str, query: str) -> int:
    terms = tokenize_query(query)
    phrase = normalize_text(query)
    if not terms:
        return 0

    if mode == "isbn":
        compact_query = normalize_compact(query)
        if compact_query and compact_query in book.compact_ids:
            return 10_000 + len(compact_query)
        return 0

    if mode in {"title", "author", "subject", "series"}:
        primary = field_match_score(book.search_fields.get(mode, ""), terms, phrase)
        if primary < 300:
            return 0
        secondary = field_match_score(book.search_fields["keyword"], terms, phrase)
        return primary * 10 + secondary

    if mode == "keyword":
        weights = {
            "title": 8,
            "author": 7,
            "isbn": 7,
            "series": 6,
            "subject": 5,
            "keyword": 1,
        }
        score = 0
        for field, weight in weights.items():
            score += field_match_score(book.search_fields.get(field, ""), terms, phrase) * weight
        return score

    return 0


def search_books(books: Iterable[Book], mode: str, query: str = "") -> list[SearchResult]:
    if mode == "recent":
        recent = sorted(
            books,
            key=lambda book: (normalize_date_key(book.entrydate), book.title.casefold()),
            reverse=True,
        )
        return [SearchResult(book, 1) for book in recent]

    results: list[SearchResult] = []
    for book in books:
        score = score_book(book, mode, query)
        if score > 0:
            results.append(SearchResult(book, score))
    results.sort(
        key=lambda result: (
            -result.score,
            result.book.title.casefold(),
            result.book.primary_author.casefold(),
            result.book.book_id,
        )
    )
    return results


def join_display(values: Iterable[str], empty: str = "") -> str:
    text = "; ".join(value for value in values if value)
    return text or empty


def format_display(book: Book) -> str:
    formats = "/".join(book.formats)
    if book.ebook_library:
        return " ".join(part for part in (book.ebook_library, formats or "EBOOK") if part)
    copies = f" x{book.copies}" if book.copies else ""
    return f"{formats or 'ITEM'}{copies}"
