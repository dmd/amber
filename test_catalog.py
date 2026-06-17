#!/usr/bin/env -S uv run --script
# /// script
# dependencies = []
# ///

import json
import os
import sqlite3
import tempfile
import unittest

import catalog


SAMPLE_CATALOG = {
    "100": {
        "title": "Dragons at Crumbling Castle",
        "primaryauthor": "Pratchett, Terry",
        "authors": [
            {"lf": "Pratchett, Terry", "fl": "Terry Pratchett", "role": "Author"}
        ],
        "isbn": {"0": "0544466594", "2": "9780544466593"},
        "publication": "Clarion Books (2015), 352 pages",
        "date": "2015",
        "collections": ["Your library"],
        "ddc": {"code": ["[Fic]"], "wording": ["Fantasy fiction"]},
        "lcc": {"code": "PZ7.P8865Dr 2015"},
        "series": ["Dragons"],
        "genre": ["Fiction", "Children's Books"],
        "entrydate": "2021-02-11",
        "format": [{"code": "1.1", "text": "Paper Book"}],
        "copies": "1",
        "summary": "Dragons at Crumbling Castle by Terry Pratchett (2015)",
    },
    "200": {
        "title": "Why Machines Learn",
        "primaryauthor": "Ananthaswamy, Anil",
        "authors": [{"lf": "Ananthaswamy, Anil", "fl": "Anil Ananthaswamy"}],
        "isbn": {"0": "0593185765", "2": "9780593185766"},
        "publication": "Dutton (2025), 496 pages",
        "date": "2025",
        "collections": ["Your library"],
        "ddc": {"code": ["006.3"], "wording": ["Artificial Intelligence"]},
        "lcc": {"code": "Q325.5 .A56"},
        "genre": ["Nonfiction", "Technology"],
        "entrydate": "2026-01-19",
        "format": [{"code": "1.1.1", "text": "Paperback"}],
        "copies": "1",
        "summary": "The elegant math behind modern AI.",
    },
    "300": {
        "title": "Machine Learning for Dragons",
        "primaryauthor": "Example, Morgan",
        "authors": [{"lf": "Example, Morgan", "fl": "Morgan Example"}],
        "isbn": {"2": "9780000000000"},
        "publication": "Example Press (2020)",
        "date": "2020",
        "collections": ["Wishlist"],
        "ddc": {"code": ["006.31"], "wording": ["Machine learning"]},
        "lcc": [],
        "series": ["Applied Myths"],
        "genre": ["Nonfiction"],
        "entrydate": "2022-05-03",
        "format": [{"code": "1.1.2", "text": "Hardcover"}],
        "copies": "2",
        "summary": "Machine learning explained with fantasy examples.",
    },
    "400": {
        "title": "Moonlight for Lin",
        "primaryauthor": "Lin, Grace",
        "authors": [{"lf": "Lin, Grace", "fl": "Grace Lin", "role": "Author"}],
        "isbn": {"2": "9781111111111"},
        "publication": "Test House (2023)",
        "date": "2023",
        "collections": ["Your library"],
        "ddc": {"code": ["Fic"], "wording": ["Fiction"]},
        "lcc": [],
        "genre": ["Fiction"],
        "entrydate": "2024-08-14",
        "format": [{"code": "1.1", "text": "Paper Book"}],
        "copies": "1",
        "summary": "A sample book for author-prefix search tests.",
    },
}


def create_sample_calibre_db(path):
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE books (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                sort TEXT,
                timestamp TIMESTAMP,
                pubdate TIMESTAMP,
                series_index REAL,
                author_sort TEXT,
                path TEXT,
                uuid TEXT
            );
            CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT, sort TEXT, link TEXT);
            CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INTEGER, author INTEGER);
            CREATE TABLE data (id INTEGER PRIMARY KEY, book INTEGER, format TEXT, uncompressed_size INTEGER, name TEXT);
            CREATE TABLE identifiers (id INTEGER PRIMARY KEY, book INTEGER, type TEXT, val TEXT);
            CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT, link TEXT);
            CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY, book INTEGER, tag INTEGER);
            CREATE TABLE series (id INTEGER PRIMARY KEY, name TEXT, sort TEXT, link TEXT);
            CREATE TABLE books_series_link (id INTEGER PRIMARY KEY, book INTEGER, series INTEGER);
            CREATE TABLE publishers (id INTEGER PRIMARY KEY, name TEXT, sort TEXT, link TEXT);
            CREATE TABLE books_publishers_link (id INTEGER PRIMARY KEY, book INTEGER, publisher INTEGER);
            CREATE TABLE comments (id INTEGER PRIMARY KEY, book INTEGER, text TEXT);

            INSERT INTO books
                (id, title, sort, timestamp, pubdate, series_index, author_sort, path, uuid)
            VALUES
                (1, 'Electronic Moon', 'Electronic Moon', '2026-02-03 04:05:06+00:00',
                 '2024-04-10 00:00:00+00:00', 1.0, 'Coder, Ada',
                 'Ada Coder/Electronic Moon (1)', 'sample-uuid');
            INSERT INTO authors VALUES (1, 'Ada Coder', 'Coder, Ada', '');
            INSERT INTO books_authors_link VALUES (1, 1, 1);
            INSERT INTO data VALUES (1, 1, 'EPUB', 123, 'Electronic Moon');
            INSERT INTO data VALUES (2, 1, 'PDF', 456, 'Electronic Moon');
            INSERT INTO identifiers VALUES (1, 1, 'isbn', '9781234567890');
            INSERT INTO tags VALUES (1, 'Science Fiction', '');
            INSERT INTO books_tags_link VALUES (1, 1, 1);
            INSERT INTO series VALUES (1, 'Lunar Files', 'Lunar Files', '');
            INSERT INTO books_series_link VALUES (1, 1, 1);
            INSERT INTO publishers VALUES (1, 'Orbit Test Press', 'Orbit Test Press', '');
            INSERT INTO books_publishers_link VALUES (1, 1, 1);
            INSERT INTO comments VALUES (1, 1, '<p>A <b>bold</b> ebook summary.</p>');
            """
        )
        conn.commit()
    finally:
        conn.close()


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.books = [
            catalog.normalize_record(book_id, record)
            for book_id, record in SAMPLE_CATALOG.items()
        ]

    def test_load_catalog_from_top_level_object(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "librarything_ddrucker_sample.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(SAMPLE_CATALOG, handle)

            books = catalog.load_catalog(path)

        self.assertEqual(4, len(books))
        self.assertEqual("100", books[0].book_id)
        self.assertEqual("Dragons at Crumbling Castle", books[0].title)

    def test_normalizes_mixed_librarything_shapes(self):
        book = self.books[0]

        self.assertEqual("Pratchett, Terry", book.primary_author)
        self.assertIn("Terry Pratchett (Author)", book.authors)
        self.assertEqual(("0544466594", "9780544466593"), book.isbns)
        self.assertEqual(("Paper Book",), book.formats)
        self.assertEqual(("[Fic]",), book.ddc_codes)
        self.assertEqual(("PZ7.P8865Dr 2015",), book.lcc_codes)

    def test_title_search_matches_and_ranks_title_first(self):
        results = catalog.search_books(self.books, "title", "dragons")

        self.assertEqual(["100", "300"], [result.book.book_id for result in results])

    def test_author_search_uses_primary_and_full_author_names(self):
        by_last = catalog.search_books(self.books, "author", "Pratchett")
        by_full = catalog.search_books(self.books, "author", "Terry Pratchett")
        by_initial = catalog.search_books(self.books, "author", "lin, g")
        by_full_name = catalog.search_books(self.books, "author", "lin, grace")

        self.assertEqual("100", by_last[0].book.book_id)
        self.assertEqual("100", by_full[0].book.book_id)
        self.assertEqual("400", by_initial[0].book.book_id)
        self.assertEqual("400", by_full_name[0].book.book_id)

    def test_isbn_search_ignores_punctuation(self):
        results = catalog.search_books(self.books, "isbn", "978-0544-466593")

        self.assertEqual(["100"], [result.book.book_id for result in results])

    def test_series_subject_and_keyword_searches(self):
        series = catalog.search_books(self.books, "series", "applied myths")
        subject = catalog.search_books(self.books, "subject", "006.3")
        keyword = catalog.search_books(self.books, "keyword", "modern AI")

        self.assertEqual(["300"], [result.book.book_id for result in series])
        self.assertEqual(["200"], [result.book.book_id for result in subject])
        self.assertEqual(["200"], [result.book.book_id for result in keyword])

    def test_recent_additions_sort_by_entrydate_descending(self):
        results = catalog.search_books(self.books, "recent")

        self.assertEqual(["200", "400", "300", "100"], [result.book.book_id for result in results])

    def test_empty_and_no_result_searches(self):
        self.assertEqual([], catalog.search_books(self.books, "title", ""))
        self.assertEqual([], catalog.search_books(self.books, "keyword", "not-present"))


class CalibreCatalogTests(unittest.TestCase):
    def test_load_calibre_catalog_marks_and_indexes_ebook_records(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "metadata-dmd.db")
            create_sample_calibre_db(path)

            books = catalog.load_calibre_catalog(path, "DANIEL")

        self.assertEqual(1, len(books))
        book = books[0]
        self.assertEqual("calibre-daniel:1", book.book_id)
        self.assertEqual("Electronic Moon", book.title)
        self.assertEqual("Ada Coder", book.primary_author)
        self.assertEqual(("9781234567890", "isbn:9781234567890"), book.isbns)
        self.assertEqual(("EPUB", "PDF"), book.formats)
        self.assertEqual("2024", book.date)
        self.assertEqual("2026-02-03", book.entrydate)
        self.assertEqual("EBOOK: DANIEL", book.ebook_marker)
        self.assertIn("EBOOK: DANIEL", book.collections)
        self.assertEqual("DANIEL EPUB/PDF", catalog.format_display(book))
        self.assertEqual(("Lunar Files",), book.series)
        self.assertEqual(("Science Fiction",), book.genre)
        self.assertEqual("A bold ebook summary.", book.summary)

        self.assertEqual(["calibre-daniel:1"], [r.book.book_id for r in catalog.search_books(books, "title", "moon")])
        self.assertEqual(["calibre-daniel:1"], [r.book.book_id for r in catalog.search_books(books, "isbn", "978-1234567890")])
        self.assertEqual(["calibre-daniel:1"], [r.book.book_id for r in catalog.search_books(books, "keyword", "daniel")])

    def test_load_combined_catalog_adds_librarything_and_calibre(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = os.path.join(tmpdir, "librarything_ddrucker_sample.json")
            db_path = os.path.join(tmpdir, "metadata-cad.db")
            with open(json_path, "w", encoding="utf-8") as handle:
                json.dump({"100": SAMPLE_CATALOG["100"]}, handle)
            create_sample_calibre_db(db_path)

            books = catalog.load_combined_catalog(json_path, [("CELESTE", db_path)])

        self.assertEqual(2, len(books))
        self.assertEqual(["100", "calibre-celeste:1"], [book.book_id for book in books])
        self.assertEqual("EBOOK: CELESTE", books[1].ebook_marker)


if __name__ == "__main__":
    unittest.main()
