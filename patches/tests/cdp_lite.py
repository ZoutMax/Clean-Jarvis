"""A small headless Chrome driver for the frontend tests, standard library only. It serves pages from a local folder on
127.0.0.1 and blocks every other host in Chrome, so these tests cannot reach the live site or the internet."""
import base64, http.server, json, os, shutil, socket, struct, subprocess, tempfile, threading, time, urllib.request

CHROME_PATHS = (r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser")


def find_chrome():
    return next((p for p in CHROME_PATHS if os.path.exists(p)), None)


class _Socket:
    """The client end of a websocket, as much as the DevTools protocol needs."""
    def __init__(self, url):
        hostport, path = url[len("ws://"):].split("/", 1)
        host, port = hostport.split(":")
        self.s = socket.create_connection((host, int(port)), timeout=60)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(("GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                        "Sec-WebSocket-Version: 13\r\n\r\n" % (path, hostport, key)).encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += self.s.recv(1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise RuntimeError("no websocket: %r" % head[:80])

    def _take(self, n):
        out = b""
        while len(out) < n:
            part = self.s.recv(n - len(out))
            if not part:
                raise RuntimeError("Chrome closed the connection")
            out += part
        return out

    def send(self, text):
        data, mask = text.encode(), os.urandom(4)
        n = len(data)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 else bytes([0x80 | 127]) + struct.pack(">Q", n))
        self.s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        data = b""
        while True:
            a, b = self._take(2)
            n = b & 127
            if n == 126:
                n = struct.unpack(">H", self._take(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._take(8))[0]
            body = self._take(n)
            if a & 15 in (0, 1, 2):
                data += body
                if a & 128:
                    return data.decode("utf-8", "replace")


class Browser:
    """files: url path -> absolute file, or a function that answers the request itself (a live camera stream, for instance).
    Tests may change files while the browser runs. Every test gets a clean page through open()."""
    def __init__(self, files):
        self.files = files
        self.dir = tempfile.mkdtemp(prefix="jarvis-chrome-")
        routes = files

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                path = self.path.split("?")[0]
                if path not in routes:
                    self.send_error(404)
                    return
                if callable(routes[path]):
                    routes[path](self)
                    return
                name = routes[path]
                with open(name, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html" if name.endswith(".html") else "image/png" if name.endswith(".png")
                                 else "image/jpeg" if name.endswith(".jpg") else "text/javascript")
                if name.endswith(".jpg"):   # camera frames that a test swaps must never come from the cache
                    self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.server.server_port
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        self.proc = subprocess.Popen([find_chrome(), "--headless=new", "--remote-debugging-port=%d" % port, "--user-data-dir=" + self.dir, "--no-first-run",
                                      "--no-default-browser-check", "--disable-extensions", "--window-size=1100,800", "--use-fake-ui-for-media-stream",
                                      "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required",
                                      "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1", "about:blank"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        tabs = None
        for _ in range(60):
            try:
                tabs = json.load(urllib.request.urlopen("http://127.0.0.1:%d/json" % port, timeout=2))
                if any(t["type"] == "page" for t in tabs):
                    break
            except OSError:
                time.sleep(0.25)
        self.ws = _Socket(next(t for t in tabs if t["type"] == "page")["webSocketDebuggerUrl"])
        self.n = 0
        self.errors = []
        self.cdp("Page.enable")
        self.cdp("Runtime.enable")

    def cdp(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("method") == "Runtime.exceptionThrown":
                self.errors.append(m["params"]["exceptionDetails"].get("exception", {}).get("description") or m["params"]["exceptionDetails"].get("text"))
            if m.get("id") == self.n:
                if "error" in m:
                    raise RuntimeError(m["error"])
                return m.get("result", {})

    def open(self, path):
        self.errors = []
        self.cdp("Page.navigate", url=self.base + path)
        for _ in range(300):   # 15 s: a busy test machine can be slow to start a page
            if self.js("document.readyState === 'complete' && !!window.ready"):
                return
            time.sleep(0.05)
        raise RuntimeError("page did not load: " + path)

    def js(self, expression):
        """Evaluates an expression or an async block (await is allowed) and returns its value."""
        r = self.cdp("Runtime.evaluate", expression="(async () => { return (" + expression + "); })()", awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description") or str(r["exceptionDetails"]))
        return r["result"].get("value")

    def run(self, body):
        """Runs statements, with return, and hands back the value."""
        r = self.cdp("Runtime.evaluate", expression="(async () => {" + body + "})()", awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description") or str(r["exceptionDetails"]))
        return r["result"].get("value")

    def click(self, x, y, hold=0.05):
        self.cdp("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        self.cdp("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=1)
        time.sleep(hold)
        self.cdp("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", clickCount=1)

    def type(self, text):
        self.cdp("Input.insertText", text=text)

    def close(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
        self.ws.s.close()
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.dir, ignore_errors=True)
