/*
 * AMBER — static, serverless edition.
 *
 * The Python TUI (amber.py) is split in two: a pure data/search layer and the
 * curses DynixApp UI. This file ports both to the browser. The data layer is
 * precomputed into catalog.json at build time (build_catalog.py), so here we
 * only re-implement (1) the query-time scoring from score_book/search_books and
 * (2) the DynixApp screen rendering, driving xterm.js with a fixed 80x25 cell
 * grid instead of a server-side PTY.
 */
(function () {
  "use strict";

  const COLS = 80;
  const ROWS = 25;

  const params = new URLSearchParams(window.location.search);
  const themeName = params.get("theme") === "green" ? "green" : "amber";
  document.body.dataset.theme = themeName;

  const linkState = document.getElementById("link-state");
  const terminalHost = document.getElementById("terminal");

  // --- xterm themes (mirror web/app.js amber theme; green is the analog) ---
  const THEMES = {
    amber: {
      term: {
        background: "#170c00", foreground: "#ff8d00", cursor: "#ff8d00", cursorAccent: "#170c00",
        selectionBackground: "#ff8d00", selectionForeground: "#170c00",
        black: "#170c00", red: "#ff8d00", green: "#ff8d00", yellow: "#ffb043", blue: "#ff8d00",
        magenta: "#ff8d00", cyan: "#ffb043", white: "#ffd27a",
        brightBlack: "#6d3f12", brightRed: "#ffb043", brightGreen: "#ffb043", brightYellow: "#ffd27a",
        brightBlue: "#ffb043", brightMagenta: "#ffb043", brightCyan: "#ffd27a", brightWhite: "#fff1c7"
      },
      fg: "33", bg: "43" // phosphor = curses COLOR_YELLOW pair -> SGR yellow
    },
    green: {
      term: {
        background: "#001403", foreground: "#2bff5b", cursor: "#2bff5b", cursorAccent: "#001403",
        selectionBackground: "#2bff5b", selectionForeground: "#001403",
        black: "#001403", red: "#2bff5b", green: "#2bff5b", yellow: "#74ff95", blue: "#2bff5b",
        magenta: "#2bff5b", cyan: "#74ff95", white: "#bdffce",
        brightBlack: "#11591f", brightRed: "#74ff95", brightGreen: "#74ff95", brightYellow: "#bdffce",
        brightBlue: "#74ff95", brightMagenta: "#74ff95", brightCyan: "#bdffce", brightWhite: "#e3ffe9"
      },
      fg: "32", bg: "42"
    }
  };
  const theme = THEMES[themeName];

  // SGR escape prefixes for each curses-equivalent attribute.
  const SGR = {
    normal: `\x1b[0;${theme.fg}m`,
    bold: `\x1b[0;1;${theme.fg}m`,
    dim: `\x1b[0;2;${theme.fg}m`,
    reverse: `\x1b[0;30;${theme.bg}m`,
    reverseBold: `\x1b[0;1;30;${theme.bg}m`,
    bar: `\x1b[0;1;30;${theme.bg}m`
  };

  // ===================================================================
  // Search engine — ported from amber.py (tokenize/score/search_books).
  // The book records already carry precomputed search_fields/compact_ids,
  // so only the query side is normalized here.
  // ===================================================================

  function normalizeText(text) {
    const m = String(text == null ? "" : text).toLowerCase().match(/[a-z0-9]+/g);
    return m ? m.join(" ") : "";
  }

  function normalizeCompact(text) {
    const m = String(text == null ? "" : text).toLowerCase().match(/[a-z0-9]+/g);
    return m ? m.join("") : "";
  }

  function tokenizeQuery(query) {
    return normalizeText(query).split(" ").filter(Boolean);
  }

  function tokenMatches(term, token) {
    if (term === token) return true;
    if (/^\d+$/.test(term)) return false;
    return token.startsWith(term);
  }

  function seqStarts(tokens, terms) {
    if (tokens.length < terms.length) return false;
    for (let i = 0; i < terms.length; i++) {
      if (tokens[i] !== terms[i]) return false;
    }
    return true;
  }

  function seqContains(tokens, terms) {
    if (!terms.length || terms.length > tokens.length) return false;
    for (let i = 0; i <= tokens.length - terms.length; i++) {
      let ok = true;
      for (let j = 0; j < terms.length; j++) {
        if (tokens[i + j] !== terms[j]) { ok = false; break; }
      }
      if (ok) return true;
    }
    return false;
  }

  function fieldMatchScore(text, terms, phrase) {
    if (!text || !terms.length) return 0;
    const tokens = text.split(" ");
    if (phrase && text === phrase) return 900;
    if (phrase && seqStarts(tokens, terms)) return 760;
    if (phrase && seqContains(tokens, terms)) return 620;
    if (terms.every((t) => tokens.indexOf(t) !== -1)) return 430;
    if (terms.every((t) => tokens.some((tok) => tokenMatches(t, tok)))) return 300;
    const matched = terms.filter((t) => tokens.some((tok) => tokenMatches(t, tok))).length;
    if (matched) return matched * 35;
    return 0;
  }

  function scoreBook(book, mode, query) {
    const terms = tokenizeQuery(query);
    const phrase = normalizeText(query);
    if (!terms.length) return 0;

    if (mode === "isbn") {
      const compact = normalizeCompact(query);
      if (compact && book.compact_ids.indexOf(compact) !== -1) return 10000 + compact.length;
      return 0;
    }

    if (mode === "title" || mode === "author" || mode === "subject" || mode === "series") {
      const primary = fieldMatchScore(book.search_fields[mode] || "", terms, phrase);
      if (primary < 300) return 0;
      const secondary = fieldMatchScore(book.search_fields.keyword || "", terms, phrase);
      return primary * 10 + secondary;
    }

    if (mode === "keyword") {
      const weights = { title: 8, author: 7, isbn: 7, series: 6, subject: 5, keyword: 1 };
      let score = 0;
      for (const field in weights) {
        score += fieldMatchScore(book.search_fields[field] || "", terms, phrase) * weights[field];
      }
      return score;
    }

    return 0;
  }

  function dateKey(text) {
    const m = /^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?/.exec(text || "");
    if (!m) return [0, 0, 0];
    return [parseInt(m[1], 10), parseInt(m[2] || "1", 10), parseInt(m[3] || "1", 10)];
  }

  function lc(s) { return (s || "").toLowerCase(); }

  function searchBooks(books, mode, query) {
    if (mode === "recent") {
      const recent = books.slice().sort((a, b) => {
        const da = dateKey(a.entrydate), db = dateKey(b.entrydate);
        for (let i = 0; i < 3; i++) { if (da[i] !== db[i]) return db[i] - da[i]; }
        const ta = lc(a.title), tb = lc(b.title);
        if (ta !== tb) return ta < tb ? 1 : -1; // reverse=True -> title descending too
        return 0;
      });
      return recent.map((book) => ({ book: book, score: 1 }));
    }

    const results = [];
    for (let i = 0; i < books.length; i++) {
      const score = scoreBook(books[i], mode, query);
      if (score > 0) results.push({ book: books[i], score: score });
    }
    results.sort((A, B) => {
      if (B.score !== A.score) return B.score - A.score;
      const ta = lc(A.book.title), tb = lc(B.book.title);
      if (ta !== tb) return ta < tb ? -1 : 1;
      const pa = lc(A.book.primary_author), pb = lc(B.book.primary_author);
      if (pa !== pb) return pa < pb ? -1 : 1;
      if (A.book.book_id !== B.book.book_id) return A.book.book_id < B.book.book_id ? -1 : 1;
      return 0;
    });
    return results;
  }

  // ===================================================================
  // Display helpers — ported from amber.py.
  // ===================================================================

  function clip(text, width) {
    if (width <= 0) return "";
    text = String(text == null ? "" : text);
    if (text.length <= width) return text;
    if (width === 1) return text.slice(0, 1);
    return text.slice(0, width - 1) + "~";
  }

  function padEnd(text, width) {
    text = String(text);
    return text.length >= width ? text : text + " ".repeat(width - text.length);
  }

  function padStart(text, width) {
    text = String(text);
    return text.length >= width ? text : " ".repeat(width - text.length) + text;
  }

  function joinDisplay(values, empty) {
    const text = (values || []).filter(Boolean).join("; ");
    return text || (empty || "");
  }

  function formatDisplay(book) {
    const formats = (book.formats || []).join("/");
    if (book.ebook_library) {
      return [book.ebook_library, formats || "EBOOK"].filter(Boolean).join(" ");
    }
    const copies = book.copies ? " x" + book.copies : "";
    return (formats || "ITEM") + copies;
  }

  function wrapText(value, width) {
    // Greedy word wrap mirroring textwrap.wrap (break_long_words=True).
    const words = String(value || "").split(/\s+/).filter(Boolean);
    const lines = [];
    let line = "";
    for (let word of words) {
      while (word.length > width) {
        if (line) { lines.push(line); line = ""; }
        lines.push(word.slice(0, width));
        word = word.slice(width);
      }
      if (!line) {
        line = word;
      } else if (line.length + 1 + word.length <= width) {
        line += " " + word;
      } else {
        lines.push(line);
        line = word;
      }
    }
    if (line) lines.push(line);
    return lines;
  }

  const SEARCH_MENU = [
    ["1", "title", "TITLE SEARCH"],
    ["2", "author", "AUTHOR SEARCH"],
    ["3", "subject", "SUBJECT / CLASSIFICATION SEARCH"],
    ["4", "series", "SERIES SEARCH"],
    ["5", "isbn", "ISBN / IDENTIFIER SEARCH"],
    ["6", "keyword", "KEYWORD SEARCH"]
  ];

  const MODE_LABELS = {
    title: "TITLE",
    author: "AUTHOR",
    subject: "SUBJECT / CLASSIFICATION",
    series: "SERIES",
    isbn: "ISBN / IDENTIFIER",
    keyword: "KEYWORD",
    recent: "RECENT ADDITIONS"
  };

  const EXAMPLES = {
    title: "EXAMPLE: dragons at crumbling castle",
    author: "EXAMPLE: pratchett, terry",
    subject: "EXAMPLE: artificial intelligence OR 006.3",
    series: "EXAMPLE: discworld",
    isbn: "EXAMPLE: 9780544466593",
    keyword: "EXAMPLE: geology children hardcover"
  };

  const HELP_LINES = [
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
    "  SO RETURNS TO THE MAIN MENU.  Q LOGS OFF."
  ];

  // ===================================================================
  // Screen: a fixed 80x25 cell grid flushed to xterm.js (mirrors curses).
  // ===================================================================

  const grid = [];
  for (let r = 0; r < ROWS; r++) {
    const row = [];
    for (let c = 0; c < COLS; c++) row.push({ ch: " ", style: "normal" });
    grid.push(row);
  }

  function clearGrid() {
    for (let r = 0; r < ROWS; r++) {
      for (let c = 0; c < COLS; c++) { grid[r][c].ch = " "; grid[r][c].style = "normal"; }
    }
  }

  function putCell(y, x, ch, style) {
    if (y < 0 || y >= ROWS || x < 0 || x >= COLS) return;
    grid[y][x].ch = ch;
    grid[y][x].style = style || "normal";
  }

  function put(y, x, text, style) {
    if (y < 0 || y >= ROWS || x >= COLS) return;
    text = String(text);
    if (x < 0) { text = text.slice(-x); x = 0; }
    if (!text) return;
    text = clip(text, COLS - x);
    for (let i = 0; i < text.length && x + i < COLS; i++) {
      grid[y][x + i].ch = text[i];
      grid[y][x + i].style = style || "normal";
    }
  }

  function center(y, text, style) {
    put(y, Math.max(0, Math.floor((COLS - String(text).length) / 2)), text, style);
  }

  function fillLine(y, style) {
    if (y < 0 || y >= ROWS) return;
    for (let x = 0; x < COLS; x++) { grid[y][x].ch = " "; grid[y][x].style = style || "normal"; }
  }

  function hline(y, x, count, style) {
    if (y < 0 || y >= ROWS || count <= 0) return;
    const end = Math.min(COLS, x + count);
    for (let c = Math.max(0, x); c < end; c++) { grid[y][c].ch = "─"; grid[y][c].style = style || "normal"; }
  }

  function box(top, left, height, width, style) {
    if (height < 2 || width < 2) return;
    putCell(top, left, "┌", style);
    hline(top, left + 1, width - 2, style);
    putCell(top, left + width - 1, "┐", style);
    for (let y = top + 1; y < top + height - 1; y++) {
      putCell(y, left, "│", style);
      putCell(y, left + width - 1, "│", style);
    }
    const bottom = top + height - 1;
    putCell(bottom, left, "└", style);
    hline(bottom, left + 1, width - 2, style);
    putCell(bottom, left + width - 1, "┘", style);
  }

  function pad2(n) { return String(n).padStart(2, "0"); }

  function drawHeader(subtitle) {
    clearGrid();
    const now = new Date();
    const left = `${pad2(now.getMonth() + 1)}/${pad2(now.getDate())}/${now.getFullYear()}`;
    const right = `${pad2(now.getHours())}:${pad2(now.getMinutes())}`;
    fillLine(0, "bar");
    put(0, 1, left, "bar");
    center(0, catalog.app_title, "bar");
    put(0, COLS - right.length - 2, right, "bar");
    center(2, catalog.module_title, "bold");
    if (subtitle) center(3, subtitle, "normal");
  }

  function drawFooter(text) {
    fillLine(ROWS - 2, "bar");
    put(ROWS - 2, 1, text, "bar");
    put(ROWS - 1, 1, " ", "normal");
  }

  let term = null;
  function flush() {
    const out = ["\x1b[?25l", "\x1b[?7l", "\x1b[H"];
    for (let r = 0; r < ROWS; r++) {
      out.push(`\x1b[${r + 1};1H`);
      let cur = null;
      let line = "";
      for (let c = 0; c < COLS; c++) {
        const cell = grid[r][c];
        if (cell.style !== cur) { line += SGR[cell.style]; cur = cell.style; }
        line += cell.ch;
      }
      out.push(line);
    }
    out.push("\x1b[0m");
    term.write(out.join(""));
  }

  // ===================================================================
  // Screen drawers — one per DynixApp screen.
  // ===================================================================

  function drawMenu() {
    drawHeader(`${catalog.count} ITEMS IN CATALOG`);
    const boxWidth = Math.min(62, Math.max(40, COLS - 10));
    const left = Math.max(0, Math.floor((COLS - boxWidth) / 2));
    const top = 5;
    box(top, left, 13, boxWidth, "normal");
    center(top + 1, "SEARCH THE PUBLIC CATALOG", "bold");
    let y = top + 3;
    for (const item of SEARCH_MENU) {
      put(y, left + 6, `${item[0]}.  ${item[2]}`, "normal");
      y++;
    }
    put(y, left + 6, "7.  RECENT ADDITIONS", "normal");
    put(y + 1, left + 6, "8.  HELP", "normal");
    put(y + 2, left + 6, "9.  LOGOFF", "normal");
    drawFooter("TYPE A NUMBER TO SELECT     Q=LOGOFF");
  }

  function drawForm() {
    const label = MODE_LABELS[ui.mode] || ui.mode.toUpperCase();
    drawHeader(`${label} SEARCH`);
    const boxWidth = Math.min(72, Math.max(40, COLS - 8));
    const boxHeight = 7;
    const top = Math.max(5, Math.floor((ROWS - boxHeight) / 2) - 2);
    const left = Math.max(0, Math.floor((COLS - boxWidth) / 2));
    box(top, left, boxHeight, boxWidth, "normal");
    put(top + 1, left + 3, `ENTER ${label}:`, "bold");
    const inputWidth = boxWidth - 8;
    put(top + 3, left + 4, " ".repeat(inputWidth), "reverse");
    put(top + 3, left + 4, clip(ui.query, inputWidth), "reverseBold");
    put(top + 5, left + 3, EXAMPLES[ui.mode] || "", "dim");
    drawFooter("ENTER=SEARCH   B=BACK   SO=START OVER   ?=HELP   Q=LOGOFF");
  }

  function resultSubtitle() {
    if (ui.mode === "recent") return `RECENT ADDITIONS - ${ui.results.length} ITEMS`;
    return `${MODE_LABELS[ui.mode]} SEARCH FOR: ${ui.query.toUpperCase()} - ${ui.results.length} ITEMS FOUND`;
  }

  function usableRows() { return Math.max(1, Math.min(9, ROWS - 10)); }
  function totalPages() { return Math.max(1, Math.ceil(ui.results.length / usableRows())); }

  function drawResults() {
    drawHeader(resultSubtitle());
    const rows = usableRows();
    const pages = totalPages();
    ui.page = Math.max(0, Math.min(ui.page, pages - 1));
    const start = ui.page * rows;
    const visible = ui.results.slice(start, start + rows);
    const y = 5;
    put(y, 1, `PAGE ${ui.page + 1} OF ${pages}`, "bold");
    if (!ui.results.length) {
      center(y + 4, "NO ITEMS FOUND", "bold");
      center(y + 6, "TRY A DIFFERENT SEARCH TERM", "normal");
    } else {
      put(y + 2, 1, "NO. AUTHOR                 YEAR  FORMAT/LIBRARY   TITLE", "reverse");
      hline(y + 3, 1, Math.max(0, COLS - 2), "normal");
      const titleWidth = Math.max(12, COLS - 52);
      for (let index = 1; index <= visible.length; index++) {
        const book = visible[index - 1].book;
        const fmt = formatDisplay(book);
        const author = book.primary_author || joinDisplay(book.authors);
        const line =
          padStart(index, 2) + ". " +
          padEnd(clip(author, 22), 22) + " " +
          padEnd(clip(book.date, 5), 5) + " " +
          padEnd(clip(fmt, 16), 16) + " " +
          clip(book.title, titleWidth);
        put(y + 3 + index, 1, line, "normal");
      }
    }
    drawFooter("N=NEXT   P=PREV   #=DETAIL   B=BACK   SO=START OVER   ?=HELP   Q=LOGOFF");
  }

  function detailLines(book) {
    const rows = [
      ["RECORD", book.book_id],
      ["TITLE", book.title],
      ["AUTHOR", book.primary_author],
      ["ALL AUTHORS", joinDisplay(book.authors)],
      ["PUBLICATION", book.publication],
      ["DATE", book.date],
      ["FORMAT", formatDisplay(book)],
      ["COPIES", book.copies],
      ["ISBN/ID", joinDisplay(book.isbns)],
      ["COLLECTIONS", joinDisplay(book.collections)],
      ["SERIES", joinDisplay(book.series)],
      ["GENRE", joinDisplay(book.genre)],
      ["DDC", joinDisplay([].concat(book.ddc_codes, book.ddc_words))],
      ["LCC", joinDisplay(book.lcc_codes)],
      ["ENTRY DATE", book.entrydate],
      ["SOURCE", book.source],
      ["SUMMARY", book.summary]
    ];
    const lines = [];
    for (const [label, value] of rows) {
      if (!value) continue;
      const wrapped = wrapText(value, 62);
      const first = wrapped.length ? wrapped[0] : "";
      lines.push(padEnd(label, 12) + " " + first);
      for (let i = 1; i < wrapped.length; i++) lines.push(padEnd("", 12) + " " + wrapped[i]);
    }
    return lines;
  }

  function drawDetail() {
    drawHeader("ITEM DETAIL");
    const visibleRows = Math.max(1, ROWS - 9);
    const lines = ui.detailLines;
    ui.detailOffset = Math.max(0, Math.min(ui.detailOffset, Math.max(0, lines.length - visibleRows)));
    put(5, 1, clip(ui.detailBook.title.toUpperCase(), 76), "bold");
    let y = 7;
    for (const line of lines.slice(ui.detailOffset, ui.detailOffset + visibleRows)) {
      put(y, 3, line, "normal");
      y++;
    }
    const more = ui.detailOffset + visibleRows < lines.length ? "MORE" : "END";
    drawFooter(`N=DOWN   P=UP   B=BACK   SO=START OVER   ?=HELP   Q=LOGOFF   ${more}`);
  }

  function drawHelp() {
    drawHeader("HELP");
    for (let i = 0; i < HELP_LINES.length; i++) {
      const line = HELP_LINES[i];
      const style = line && line[0] !== " " ? "bold" : "normal";
      put(5 + i, 4, line, style);
    }
    drawFooter("B=BACK   SO=START OVER   Q=LOGOFF");
  }

  function drawGoodbye() {
    drawHeader("SESSION ENDED");
    center(8, "THANK YOU FOR USING THE PUBLIC ACCESS CATALOG", "bold");
    center(10, "PRESS ANY KEY TO RETURN TO THE MAIN MENU", "normal");
    drawFooter("");
  }

  // ===================================================================
  // State machine — mirrors DynixApp.run() control flow.
  // ===================================================================

  const ui = {
    state: "menu",
    mode: "title",
    query: "",
    results: [],
    page: 0,
    detailBook: null,
    detailLines: [],
    detailOffset: 0,
    helpReturn: "menu",
    pendingSO: false
  };

  function render() {
    switch (ui.state) {
      case "menu": drawMenu(); break;
      case "form": drawForm(); break;
      case "results": drawResults(); break;
      case "detail": drawDetail(); break;
      case "help": drawHelp(); break;
      case "goodbye": drawGoodbye(); break;
    }
    flush();
  }

  function openForm(mode) { ui.mode = mode; ui.query = ""; ui.state = "form"; render(); }

  function runSearch() {
    ui.results = searchBooks(catalog.books, ui.mode, ui.query);
    ui.page = 0;
    ui.state = "results";
    render();
  }

  function openRecent() {
    ui.mode = "recent";
    ui.query = "";
    ui.results = searchBooks(catalog.books, "recent");
    ui.page = 0;
    ui.state = "results";
    render();
  }

  function openDetail(book) {
    ui.detailBook = book;
    ui.detailLines = detailLines(book);
    ui.detailOffset = 0;
    ui.state = "detail";
    render();
  }

  function openHelp(returnTo) { ui.helpReturn = returnTo; ui.pendingSO = false; ui.state = "help"; render(); }
  function goMenu() { ui.pendingSO = false; ui.state = "menu"; render(); }
  function goGoodbye() { ui.pendingSO = false; ui.state = "goodbye"; render(); }

  // Two-key "SO" (start over). Returns true if the key was consumed by an SO.
  function handleSO(key) {
    if (ui.pendingSO) {
      ui.pendingSO = false;
      if (key === "s" || key === "S") { ui.pendingSO = true; return true; }
      if (key === "o" || key === "O") { goMenu(); return true; }
      return false;
    }
    if (key === "s" || key === "S") { ui.pendingSO = true; return true; }
    return false;
  }

  function onMenuKey(key) {
    if (key === "q" || key === "Q" || key === "9") return goGoodbye();
    if (key === "7") return openRecent();
    if (key === "8" || key === "?") return openHelp("menu");
    for (const item of SEARCH_MENU) {
      if (key === item[0]) return openForm(item[1]);
    }
  }

  function onResultsKey(key) {
    if (key === "q" || key === "Q") return goGoodbye();
    if (key === "b" || key === "B" || key === "\x1b") return goMenu();
    if (key === "?") return openHelp("results");
    if (handleSO(key)) return;
    const pages = totalPages();
    if ((key === "n" || key === "N" || key === " ") && ui.page + 1 < pages) { ui.page++; return render(); }
    if ((key === "p" || key === "P") && ui.page > 0) { ui.page--; return render(); }
    if (/^[0-9]$/.test(key)) {
      const number = parseInt(key, 10);
      const visible = ui.results.slice(ui.page * usableRows(), ui.page * usableRows() + usableRows());
      if (number >= 1 && number <= visible.length) return openDetail(visible[number - 1].book);
    }
  }

  function onDetailKey(key) {
    if (key === "q" || key === "Q") return goGoodbye();
    if (key === "b" || key === "B" || key === "\x1b") { ui.state = "results"; return render(); }
    if (key === "?") return openHelp("detail");
    if (handleSO(key)) return;
    const step = Math.max(1, Math.max(1, ROWS - 9) - 1);
    if (key === "n" || key === "N" || key === " " || key === "j") { ui.detailOffset += step; return render(); }
    if (key === "p" || key === "P" || key === "k") { ui.detailOffset -= step; return render(); }
  }

  function onHelpKey(key) {
    if (key === "q" || key === "Q") return goGoodbye();
    if (key === "b" || key === "B" || key === "\x1b") { ui.state = ui.helpReturn; return render(); }
    if (handleSO(key)) return;
  }

  function onFormChar(ch) {
    if (ch === "\r" || ch === "\n") {
      const command = ui.query.trim().toUpperCase();
      if (command === "B") return goMenu();
      if (command === "SO") return goMenu();
      if (command === "Q") return goGoodbye();
      if (command === "?") return openHelp("form");
      if (ui.query.trim()) return runSearch();
      return;
    }
    if (ch === "\x7f" || ch === "\b") { ui.query = ui.query.slice(0, -1); return render(); }
    if (ch === "\x1b") return goMenu();
    if (ch === "?") return openHelp("form");
    if (ch === "\x03" || ch === "\x04") return goGoodbye();
    if (ch >= " " && ch !== "\x7f" && ui.query.length < 160) { ui.query += ch; return render(); }
  }

  function onData(data) {
    if (ui.state === "form") {
      // ESC alone is a command; ESC-prefixed sequences (arrows) are ignored.
      if (data === "\x1b") return onFormChar("\x1b");
      if (data.length > 1 && data[0] === "\x1b") return;
      for (const ch of data) onFormChar(ch);
      return;
    }

    if (ui.state === "goodbye") return goMenu();

    // Command screens take one key token; ignore arrow/escape sequences.
    let key = data;
    if (data === "\x1b") key = "\x1b";
    else if (data.length > 1 && data[0] === "\x1b") return;
    else if (data.length > 1) key = data[0];

    switch (ui.state) {
      case "menu": return onMenuKey(key);
      case "results": return onResultsKey(key);
      case "detail": return onDetailKey(key);
      case "help": return onHelpKey(key);
    }
  }

  // ===================================================================
  // Boot.
  // ===================================================================

  let catalog = null;

  function setState(text, className) {
    linkState.textContent = text;
    linkState.className = className || "";
  }

  function lockScreenSize() {
    const dims = term._core && term._core._renderService && term._core._renderService.dimensions
      && term._core._renderService.dimensions.css && term._core._renderService.dimensions.css.canvas;
    if (!dims || !dims.width || !dims.height) return;
    terminalHost.style.width = `${dims.width}px`;
    terminalHost.style.height = `${dims.height}px`;
  }

  function startTerminal() {
    term = new Terminal({
      cols: COLS,
      rows: ROWS,
      cursorBlink: false,
      cursorStyle: "block",
      disableStdin: false,
      scrollback: 0,
      fontFamily: "VT323, monospace",
      fontSize: 22,
      lineHeight: 1.0,
      letterSpacing: 0,
      allowTransparency: false,
      customGlyphs: true,
      theme: theme.term
    });
    if (window.CanvasAddon && window.CanvasAddon.CanvasAddon) {
      term.loadAddon(new window.CanvasAddon.CanvasAddon());
    }
    term.open(terminalHost);
    term.focus();
    lockScreenSize();
    window.setTimeout(lockScreenSize, 300);
    term.onData(onData);

    setState("READY", "connected");
    render();
  }

  window.addEventListener("resize", lockScreenSize);
  document.addEventListener("click", () => { if (term) term.focus(); });

  const fontsReady = document.fonts
    ? Promise.race([document.fonts.ready, new Promise((r) => window.setTimeout(r, 1200))])
    : Promise.resolve();

  Promise.all([
    fetch("catalog.json").then((r) => {
      if (!r.ok) throw new Error(`catalog.json: HTTP ${r.status}`);
      return r.json();
    }),
    fontsReady
  ])
    .then(([data]) => {
      catalog = data;
      startTerminal();
    })
    .catch((err) => {
      setState("ERROR", "disconnected");
      const msg = document.createElement("p");
      msg.style.color = "var(--paper)";
      msg.style.font = "1.2rem VT323, monospace";
      msg.textContent = "FAILED TO LOAD CATALOG: " + err.message;
      terminalHost.appendChild(msg);
    });
})();
