#!/usr/bin/env -S UV_CACHE_DIR=/private/tmp/codex-uv-cache uv run --script
# /// script
# dependencies = []
# ///
"""Dynix-style terminal search for a LibraryThing JSON export."""

from __future__ import annotations

import argparse
import curses
import datetime as dt
import glob
import html
import json
import locale
import os
import re
import sqlite3
import sys
import textwrap
from dataclasses import dataclass
from typing import Any, Iterable


APP_TITLE = "AUTOMATED LIBRARY SYSTEM"
MODULE_TITLE = "Outer Dibblestan Catalog"
DEFAULT_USER = "ddrucker"

COLOR_NORMAL = 1
COLOR_REVERSE = 2
COLOR_DIM = 3
COLOR_ALERT = 4

SEARCH_MENU = [
    ("1", "title", "TITLE SEARCH"),
    ("2", "author", "AUTHOR SEARCH"),
    ("3", "subject", "SUBJECT / CLASSIFICATION SEARCH"),
    ("4", "series", "SERIES SEARCH"),
    ("5", "isbn", "ISBN / IDENTIFIER SEARCH"),
    ("6", "keyword", "KEYWORD SEARCH"),
]

DEFAULT_EBOOK_DATABASES = (
    ("DANIEL", "metadata-dmd.db"),
    ("CELESTE", "metadata-cad.db"),
)

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
    lcc = record.get("lcc") if isinstance(record.get("lcc"), dict) else record.get("lcc")
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


def find_default_catalog(cwd: str = ".") -> str:
    candidates = sorted(glob.glob(os.path.join(cwd, "librarything_*.json")))
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise FileNotFoundError("No librarything_*.json catalog file found.")
    names = ", ".join(os.path.basename(path) for path in candidates)
    raise FileExistsError(f"Multiple catalog files found; use --catalog. Found: {names}")


def default_ebook_databases(cwd: str = ".") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for library_name, filename in DEFAULT_EBOOK_DATABASES:
        path = os.path.join(cwd, filename)
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


def clip(text: str, width: int) -> str:
    if width <= 0:
        return ""
    text = str(text)
    if len(text) <= width:
        return text
    if width == 1:
        return text[:1]
    return text[: width - 1] + "~"


def join_display(values: Iterable[str], empty: str = "") -> str:
    text = "; ".join(value for value in values if value)
    return text or empty


def format_display(book: Book) -> str:
    formats = "/".join(book.formats)
    if book.ebook_library:
        return " ".join(part for part in (book.ebook_library, formats or "EBOOK") if part)
    copies = f" x{book.copies}" if book.copies else ""
    return f"{formats or 'ITEM'}{copies}"


class DynixApp:
    def __init__(self, stdscr: Any, books: list[Book], catalog_path: str, theme: str) -> None:
        self.stdscr = stdscr
        self.books = books
        self.catalog_path = catalog_path
        self.theme = theme
        self.last_mode = "title"
        self.last_query = ""
        self.last_results: list[SearchResult] = []
        self.running = True

    def setup(self) -> None:
        try:
            locale.setlocale(locale.LC_ALL, "")
        except locale.Error:
            pass
        curses.curs_set(0)
        self.stdscr.keypad(True)
        curses.start_color()
        try:
            curses.use_default_colors()
            background = -1
        except curses.error:
            background = curses.COLOR_BLACK
        phosphor = curses.COLOR_GREEN if self.theme == "green" else curses.COLOR_YELLOW
        curses.init_pair(COLOR_NORMAL, phosphor, background)
        curses.init_pair(COLOR_REVERSE, curses.COLOR_BLACK, phosphor)
        curses.init_pair(COLOR_DIM, phosphor, background)
        curses.init_pair(COLOR_ALERT, curses.COLOR_RED, background)
        self.stdscr.bkgd(" ", curses.color_pair(COLOR_NORMAL))

    def run(self) -> None:
        self.setup()
        while self.running:
            choice = self.main_menu()
            if choice == "quit":
                break
            if choice == "help":
                outcome = self.help_screen()
                if outcome == "quit":
                    break
                continue
            if choice == "recent":
                self.show_results("recent", "", search_books(self.books, "recent"))
                continue
            mode = choice
            query = self.search_form(mode)
            if query is None:
                continue
            if query == "__quit__":
                break
            if query == "__home__":
                continue
            results = search_books(self.books, mode, query)
            self.last_mode = mode
            self.last_query = query
            self.last_results = results
            self.show_results(mode, query, results)
        self.goodbye()

    def attr(self, color: int = COLOR_NORMAL, *attrs: int) -> int:
        value = curses.color_pair(color)
        for extra in attrs:
            value |= extra
        return value

    def bar_attr(self) -> int:
        return self.attr(COLOR_NORMAL, curses.A_REVERSE, curses.A_BOLD)

    def add(self, y: int, x: int, text: str, attr: int | None = None) -> None:
        height, width = self.stdscr.getmaxyx()
        if y < 0 or y >= height or x >= width:
            return
        if x < 0:
            text = text[-x:]
            x = 0
        if not text:
            return
        try:
            self.stdscr.addstr(y, x, clip(text, width - x), attr or self.attr())
        except curses.error:
            pass

    def center(self, y: int, text: str, attr: int | None = None) -> None:
        _, width = self.stdscr.getmaxyx()
        self.add(y, max(0, (width - len(text)) // 2), text, attr)

    def fill_line(self, y: int, attr: int | None = None) -> None:
        _, width = self.stdscr.getmaxyx()
        line_attr = attr or self.attr()
        for x in range(width):
            try:
                self.stdscr.addch(y, x, " ", line_attr)
            except curses.error:
                pass

    def draw_cells(self, y: int, x: int, ch: int, count: int, attr: int | None = None) -> None:
        height, width = self.stdscr.getmaxyx()
        if y < 0 or y >= height or count <= 0:
            return
        cell_attr = attr or self.attr()
        for col in range(max(0, x), min(width, x + count)):
            try:
                self.stdscr.addch(y, col, ch, cell_attr)
            except curses.error:
                pass

    def draw_header(self, subtitle: str = "") -> None:
        self.stdscr.erase()
        height, width = self.stdscr.getmaxyx()
        now = dt.datetime.now()
        left = now.strftime("%m/%d/%Y")
        right = now.strftime("%H:%M")
        bar_attr = self.bar_attr()
        self.fill_line(0, bar_attr)
        self.add(0, 1, left, bar_attr)
        self.center(0, APP_TITLE, bar_attr)
        self.add(0, width - len(right) - 2, right, bar_attr)
        self.center(2, MODULE_TITLE, self.attr(COLOR_NORMAL, curses.A_BOLD))
        if subtitle:
            self.center(3, subtitle, self.attr(COLOR_NORMAL))
        if height < 18 or width < 70:
            self.center(6, "TERMINAL TOO SMALL - ENLARGE WINDOW", self.attr(COLOR_ALERT, curses.A_BOLD))

    def footer(self, text: str) -> None:
        height, _ = self.stdscr.getmaxyx()
        bar_attr = self.bar_attr()
        self.fill_line(height - 2, bar_attr)
        self.add(height - 2, 1, text, bar_attr)
        self.add(height - 1, 1, " ", self.attr())

    def draw_box(self, top: int, left: int, height: int, width: int) -> None:
        if height < 2 or width < 2:
            return
        attr = self.attr()
        self.add_cell(top, left, curses.ACS_ULCORNER, attr)
        self.draw_cells(top, left + 1, curses.ACS_HLINE, width - 2, attr)
        self.add_cell(top, left + width - 1, curses.ACS_URCORNER, attr)
        for y in range(top + 1, top + height - 1):
            self.add_cell(y, left, curses.ACS_VLINE, attr)
            self.add_cell(y, left + width - 1, curses.ACS_VLINE, attr)
        bottom = top + height - 1
        self.add_cell(bottom, left, curses.ACS_LLCORNER, attr)
        self.draw_cells(bottom, left + 1, curses.ACS_HLINE, width - 2, attr)
        self.add_cell(bottom, left + width - 1, curses.ACS_LRCORNER, attr)

    def add_cell(self, y: int, x: int, ch: int, attr: int | None = None) -> None:
        height, width = self.stdscr.getmaxyx()
        if y < 0 or y >= height or x < 0 or x >= width:
            return
        try:
            self.stdscr.addch(y, x, ch, attr or self.attr())
        except curses.error:
            pass

    def main_menu(self) -> str:
        while True:
            self.draw_header(f"{len(self.books)} ITEMS IN CATALOG")
            height, width = self.stdscr.getmaxyx()
            box_width = min(62, max(40, width - 10))
            left = max(0, (width - box_width) // 2)
            top = 5
            self.draw_box(top, left, 13, box_width)
            self.center(top + 1, "SEARCH THE PUBLIC CATALOG", self.attr(COLOR_NORMAL, curses.A_BOLD))
            y = top + 3
            for key, _mode, label in SEARCH_MENU:
                self.add(y, left + 6, f"{key}.  {label}", self.attr())
                y += 1
            self.add(y, left + 6, "7.  RECENT ADDITIONS", self.attr())
            self.add(y + 1, left + 6, "8.  HELP", self.attr())
            self.add(y + 2, left + 6, "9.  LOGOFF", self.attr())
            self.footer("TYPE A NUMBER TO SELECT     Q=LOGOFF")
            self.stdscr.refresh()
            key = self.read_key()
            if key in {"q", "Q", "9"}:
                return "quit"
            if key == "7":
                return "recent"
            if key == "8" or key == "?":
                outcome = self.help_screen()
                if outcome == "quit":
                    return "quit"
                continue
            for number, mode, _label in SEARCH_MENU:
                if key == number:
                    return mode

    def mode_label(self, mode: str) -> str:
        labels = {
            "title": "TITLE",
            "author": "AUTHOR",
            "subject": "SUBJECT / CLASSIFICATION",
            "series": "SERIES",
            "isbn": "ISBN / IDENTIFIER",
            "keyword": "KEYWORD",
            "recent": "RECENT ADDITIONS",
        }
        return labels.get(mode, mode.upper())

    def search_form(self, mode: str) -> str | None:
        query = ""
        examples = {
            "title": "EXAMPLE: dragons at crumbling castle",
            "author": "EXAMPLE: pratchett, terry",
            "subject": "EXAMPLE: artificial intelligence OR 006.3",
            "series": "EXAMPLE: discworld",
            "isbn": "EXAMPLE: 9780544466593",
            "keyword": "EXAMPLE: geology children hardcover",
        }
        while True:
            self.draw_header(f"{self.mode_label(mode)} SEARCH")
            height, width = self.stdscr.getmaxyx()
            box_width = min(72, max(40, width - 8))
            box_height = 7
            top = max(5, (height - box_height) // 2 - 2)
            left = max(0, (width - box_width) // 2)
            self.draw_box(top, left, box_height, box_width)
            self.add(top + 1, left + 3, f"ENTER {self.mode_label(mode)}:", self.attr(COLOR_NORMAL, curses.A_BOLD))
            input_width = box_width - 8
            prompt = clip(query, input_width)
            self.add(top + 3, left + 4, " " * input_width, self.attr(COLOR_REVERSE))
            self.add(top + 3, left + 4, prompt, self.attr(COLOR_REVERSE, curses.A_BOLD))
            self.add(top + 5, left + 3, examples.get(mode, ""), self.attr(COLOR_DIM, curses.A_DIM))
            self.footer("ENTER=SEARCH   B=BACK   SO=START OVER   ?=HELP   Q=LOGOFF")
            self.stdscr.refresh()
            key_code = self.stdscr.getch()
            if key_code in (curses.KEY_ENTER, 10, 13):
                command = query.strip().upper()
                if command == "B":
                    return None
                if command == "SO":
                    return "__home__"
                if command == "Q":
                    return "__quit__"
                if command == "?":
                    outcome = self.help_screen()
                    if outcome == "quit":
                        return "__quit__"
                    if outcome == "home":
                        return "__home__"
                    continue
                if query.strip():
                    return query.strip()
                continue
            if key_code in (curses.KEY_BACKSPACE, 127, 8):
                query = query[:-1]
                continue
            if key_code == 27:
                return None
            try:
                char = chr(key_code)
            except ValueError:
                continue
            if char == "?":
                outcome = self.help_screen()
                if outcome == "quit":
                    return "__quit__"
                if outcome == "home":
                    return "__home__"
                continue
            if char in "\x03\x04":
                return "__quit__"
            if char.isprintable() and len(query) < 160:
                query += char

    def show_results(self, mode: str, query: str, results: list[SearchResult]) -> None:
        page = 0
        while True:
            self.draw_header(self.result_subtitle(mode, query, len(results)))
            height, width = self.stdscr.getmaxyx()
            usable_rows = max(1, min(9, height - 10))
            total_pages = max(1, (len(results) + usable_rows - 1) // usable_rows)
            page = max(0, min(page, total_pages - 1))
            start = page * usable_rows
            visible = results[start : start + usable_rows]
            y = 5
            self.add(y, 1, f"PAGE {page + 1} OF {total_pages}", self.attr(COLOR_NORMAL, curses.A_BOLD))
            if not results:
                self.center(y + 4, "NO ITEMS FOUND", self.attr(COLOR_ALERT, curses.A_BOLD))
                self.center(y + 6, "TRY A DIFFERENT SEARCH TERM", self.attr())
            else:
                self.add(y + 2, 1, "NO. AUTHOR                 YEAR  FORMAT/LIBRARY   TITLE", self.attr(COLOR_REVERSE))
                self.draw_cells(y + 3, 1, curses.ACS_HLINE, max(0, width - 2), self.attr())
                for index, result in enumerate(visible, start=1):
                    book = result.book
                    row_y = y + 3 + index
                    fmt = format_display(book)
                    author = book.primary_author or join_display(book.authors)
                    title_width = max(12, width - 52)
                    line = (
                        f"{index:>2}. "
                        f"{clip(author, 22):<22} "
                        f"{clip(book.date, 5):<5} "
                        f"{clip(fmt, 16):<16} "
                        f"{clip(book.title, title_width)}"
                    )
                    self.add(row_y, 1, line, self.attr())
            self.footer("N=NEXT   P=PREV   #=DETAIL   B=BACK   SO=START OVER   ?=HELP   Q=LOGOFF")
            self.stdscr.refresh()
            key = self.read_key()
            if key in {"q", "Q"}:
                self.running = False
                return
            if key in {"b", "B", "\x1b"}:
                return
            if key == "?":
                outcome = self.help_screen()
                if outcome == "quit":
                    self.running = False
                    return
                if outcome == "home":
                    return
                continue
            if key in {"s", "S"}:
                second = self.read_key()
                if second in {"o", "O"}:
                    return
                continue
            if key in {"n", "N", " "} and page + 1 < total_pages:
                page += 1
                continue
            if key in {"p", "P"} and page > 0:
                page -= 1
                continue
            if key.isdigit():
                number = int(key)
                if 1 <= number <= len(visible):
                    outcome = self.detail_screen(visible[number - 1].book)
                    if outcome == "quit":
                        self.running = False
                        return
                    if outcome == "home":
                        return

    def result_subtitle(self, mode: str, query: str, count: int) -> str:
        if mode == "recent":
            return f"RECENT ADDITIONS - {count} ITEMS"
        return f"{self.mode_label(mode)} SEARCH FOR: {query.upper()} - {count} ITEMS FOUND"

    def detail_lines(self, book: Book) -> list[str]:
        rows = [
            ("RECORD", book.book_id),
            ("TITLE", book.title),
            ("AUTHOR", book.primary_author),
            ("ALL AUTHORS", join_display(book.authors)),
            ("PUBLICATION", book.publication),
            ("DATE", book.date),
            ("FORMAT", format_display(book)),
            ("COPIES", book.copies),
            ("ISBN/ID", join_display(book.isbns)),
            ("COLLECTIONS", join_display(book.collections)),
            ("SERIES", join_display(book.series)),
            ("GENRE", join_display(book.genre)),
            ("DDC", join_display([*book.ddc_codes, *book.ddc_words])),
            ("LCC", join_display(book.lcc_codes)),
            ("ENTRY DATE", book.entrydate),
            ("SOURCE", book.source),
            ("SUMMARY", book.summary),
        ]
        lines: list[str] = []
        for label, value in rows:
            if not value:
                continue
            wrapped = textwrap.wrap(value, width=62) or [""]
            lines.append(f"{label:<12} {wrapped[0]}")
            for extra in wrapped[1:]:
                lines.append(f"{'':<12} {extra}")
        return lines

    def detail_screen(self, book: Book) -> str:
        offset = 0
        lines = self.detail_lines(book)
        while True:
            self.draw_header("ITEM DETAIL")
            height, _ = self.stdscr.getmaxyx()
            visible_rows = max(1, height - 8)
            offset = max(0, min(offset, max(0, len(lines) - visible_rows)))
            self.add(5, 1, clip(book.title.upper(), 76), self.attr(COLOR_NORMAL, curses.A_BOLD))
            y = 7
            for line in lines[offset : offset + visible_rows]:
                self.add(y, 3, line, self.attr())
                y += 1
            more = "MORE" if offset + visible_rows < len(lines) else "END"
            self.footer(f"N=DOWN   P=UP   B=BACK   SO=START OVER   ?=HELP   Q=LOGOFF   {more}")
            self.stdscr.refresh()
            key = self.read_key()
            if key in {"q", "Q"}:
                return "quit"
            if key in {"b", "B", "\x1b"}:
                return "back"
            if key == "?":
                outcome = self.help_screen()
                if outcome in {"quit", "home"}:
                    return outcome
                continue
            if key in {"s", "S"}:
                second = self.read_key()
                if second in {"o", "O"}:
                    return "home"
                continue
            if key in {"n", "N", " ", "j"}:
                offset += max(1, visible_rows - 1)
            if key in {"p", "P", "k"}:
                offset -= max(1, visible_rows - 1)

    def help_screen(self) -> str:
        lines = [
            "THIS CATALOG SEARCHES THE LOCAL LIBRARYTHING EXPORT AND CALIBRE EBOOK DATABASES.",
            "EBOOK RECORDS SHOW DANIEL OR CELESTE IN THE FORMAT COLUMN.",
            "",
            "SEARCH OPTIONS",
            "  1 TITLE       MATCHES WORDS IN ITEM TITLES.",
            "  2 AUTHOR      MATCHES PRIMARY AND ADDITIONAL AUTHORS.",
            "  3 SUBJECT     MATCHES DDC, LCC, GENRE, AND COLLECTION TEXT.",
            "  4 SERIES      MATCHES SERIES FIELDS.",
            "  5 ISBN        MATCHES ISBN, EAN, ASIN, AND RECORD IDS.",
            "  6 KEYWORD     MATCHES ALL INDEXED BIBLIOGRAPHIC TEXT.",
            "  7 RECENT      SORTS BY LIBRARYTHING ENTRY DATE.",
            "",
            "COMMANDS",
            "  ENTER SEARCHES.  NUMBER KEYS OPEN RECORDS.",
            "  N AND P MOVE THROUGH PAGES.  B GOES BACK.",
            "  SO RETURNS TO THE MAIN MENU.  Q LOGS OFF.",
        ]
        while True:
            self.draw_header("HELP")
            for y, line in enumerate(lines, start=5):
                self.add(y, 4, line, self.attr(COLOR_NORMAL, curses.A_BOLD if line and not line.startswith(" ") else 0))
            self.footer("B=BACK   SO=START OVER   Q=LOGOFF")
            self.stdscr.refresh()
            key = self.read_key()
            if key in {"q", "Q"}:
                return "quit"
            if key in {"b", "B", "\x1b"}:
                return "back"
            if key in {"s", "S"}:
                second = self.read_key()
                if second in {"o", "O"}:
                    return "home"

    def goodbye(self) -> None:
        self.draw_header("SESSION ENDED")
        self.center(8, "THANK YOU FOR USING THE PUBLIC ACCESS CATALOG", self.attr(COLOR_NORMAL, curses.A_BOLD))
        self.center(10, "PRESS ANY KEY TO CLOSE THIS TERMINAL SESSION", self.attr())
        self.stdscr.refresh()
        self.stdscr.getch()

    def read_key(self) -> str:
        code = self.stdscr.getch()
        if code == 27:
            return "\x1b"
        if 0 <= code <= 0x10FFFF:
            try:
                return chr(code)
            except ValueError:
                return ""
        return ""


def parse_ebook_database_arg(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("Use NAME=PATH, for example DANIEL=metadata-dmd.db")
    name, path = value.split("=", 1)
    name = name.strip().upper()
    path = path.strip()
    if not name or not path:
        raise argparse.ArgumentTypeError("Use NAME=PATH, for example DANIEL=metadata-dmd.db")
    return name, path


def print_check(books: list[Book], path: str, ebook_databases: Iterable[tuple[str, str]]) -> None:
    ebook_counts: dict[str, int] = {}
    for book in books:
        if book.ebook_library:
            ebook_counts[book.ebook_library] = ebook_counts.get(book.ebook_library, 0) + 1
    print(f"CATALOG: {path}")
    print(f"ITEMS:   {len(books)}")
    for library_name, db_path in ebook_databases:
        count = ebook_counts.get(library_name.upper(), 0)
        print(f"EBOOKS:  {library_name.upper()} {count} ({db_path})")
    if books:
        print(f"FIRST:   {books[0].title} / {books[0].primary_author}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dynix-style LibraryThing catalog TUI")
    parser.add_argument("--catalog", help="Path to LibraryThing JSON export")
    parser.add_argument(
        "--ebook-db",
        action="append",
        type=parse_ebook_database_arg,
        metavar="NAME=PATH",
        help="Add a Calibre metadata DB with a display name, for example DANIEL=metadata-dmd.db",
    )
    parser.add_argument("--no-ebooks", action="store_true", help="Do not auto-load Calibre ebook databases")
    parser.add_argument("--theme", choices=("amber", "green"), default="amber")
    parser.add_argument("--check", action="store_true", help="Load the catalog and print a summary")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        catalog_path = args.catalog or find_default_catalog()
        catalog_dir = os.path.dirname(os.path.abspath(catalog_path)) or "."
        ebook_databases = [] if args.no_ebooks else default_ebook_databases(catalog_dir)
        ebook_databases.extend(args.ebook_db or [])
        books = load_combined_catalog(catalog_path, ebook_databases)
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"catpac: {exc}", file=sys.stderr)
        return 2

    if args.check:
        print_check(books, catalog_path, ebook_databases)
        return 0

    try:
        curses.wrapper(lambda stdscr: DynixApp(stdscr, books, catalog_path, args.theme).run())
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
