(function () {
  const COLS = 80;
  const ROWS = 25;
  const encoder = new TextEncoder();
  const state = document.getElementById("link-state");
  const terminalHost = document.getElementById("terminal");

  const term = new Terminal({
    cols: COLS,
    rows: ROWS,
    cursorBlink: true,
    cursorStyle: "block",
    disableStdin: false,
    scrollback: 0,
    fontFamily: "VT323, monospace",
    fontSize: 22,
    lineHeight: 1.0,
    letterSpacing: 0,
    allowTransparency: false,
    customGlyphs: true,
    theme: {
      background: "#170c00",
      foreground: "#ff8d00",
      cursor: "#ff8d00",
      cursorAccent: "#170c00",
      selectionBackground: "#ff8d00",
      selectionForeground: "#170c00",
      black: "#170c00",
      red: "#ff5f3f",
      green: "#ff8d00",
      yellow: "#ffb043",
      blue: "#ff8d00",
      magenta: "#ff8d00",
      cyan: "#ffb043",
      white: "#ffd27a",
      brightBlack: "#6d3f12",
      brightRed: "#ff765d",
      brightGreen: "#ffb043",
      brightYellow: "#ffd27a",
      brightBlue: "#ffb043",
      brightMagenta: "#ffb043",
      brightCyan: "#ffd27a",
      brightWhite: "#fff1c7"
    }
  });

  function setState(text, className) {
    state.textContent = text;
    state.className = className;
  }

  const fontsReady = document.fonts
    ? Promise.race([
        document.fonts.ready,
        new Promise((resolve) => window.setTimeout(resolve, 1200))
      ])
    : Promise.resolve();

  fontsReady.then(startTerminal);

  function startTerminal() {
    if (window.CanvasAddon && window.CanvasAddon.CanvasAddon) {
      term.loadAddon(new window.CanvasAddon.CanvasAddon());
    }
    term.open(terminalHost);
    term.focus();
    lockScreenSize();
    window.setTimeout(lockScreenSize, 300);

    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${proto}//${window.location.host}/ws`);
    socket.binaryType = "arraybuffer";

    socket.addEventListener("open", () => {
      setState("ONLINE", "connected");
      term.focus();
    });

    socket.addEventListener("message", (event) => {
      if (event.data instanceof ArrayBuffer) {
        term.write(new Uint8Array(event.data));
      } else {
        term.write(event.data);
      }
    });

    socket.addEventListener("close", () => {
      setState("OFFLINE", "disconnected");
      term.write("\r\n\r\nCONNECTION CLOSED\r\n");
    });

    socket.addEventListener("error", () => {
      setState("ERROR", "disconnected");
    });

    term.onData((data) => {
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(encoder.encode(data));
      }
    });
  }

  window.addEventListener("resize", lockScreenSize);
  document.addEventListener("click", () => term.focus());

  function lockScreenSize() {
    const dims = term._core?._renderService?.dimensions?.css?.canvas;
    if (!dims || !dims.width || !dims.height) {
      return;
    }
    terminalHost.style.width = `${dims.width}px`;
    terminalHost.style.height = `${dims.height}px`;
  }
})();
