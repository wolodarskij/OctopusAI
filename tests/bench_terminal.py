"""Time the board's terminal window on one live session.   A script, not a test.

    python tests/bench_terminal.py <ticket-id> [--mode m] [--n 20] [--echo]

Serves the board in-process for this project, the way `test_board_api.py` does, and times:

    start          the first `/api/screen` after the server is up: lazy imports, first worker
    find_session   `T.find_session`, the scan that resolves one session
    screen         `T.screen_session` called directly
    send           `T.send_session(s, "", [])` called directly: an empty send writes nothing
    /api/screen    `POST /api/screen` end to end, as the page polls it
    /api/send      `POST /api/send` with nothing to type, as the page sends a key

`--echo` types one `x` into the session, polls `/api/screen` until the screen changes, then
sends `Backspace`. Use it only on a session that is idle at its prompt.

The file name does not start with `test_`, so `unittest discover` does not pick it up.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _d in ("core/engine", "core/win", "core/hooks"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import project  # noqa: E402
import todo as T  # noqa: E402
import todo_server  # noqa: E402


def ms(seconds):
    return seconds * 1000.0


def timed(fn, n):
    out = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        out.append(ms(time.perf_counter() - t0))
    return out


def row(name, xs):
    xs = sorted(xs)
    p90 = xs[min(len(xs) - 1, int(round(0.9 * (len(xs) - 1))))]
    return "| %-14s | %4d | %8.1f | %8.1f | %8.1f |" % (name, len(xs), xs[0],
                                                        statistics.median(xs), p90)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("id", help="the ticket id of a live session")
    ap.add_argument("--mode", default=None, help="work, plan, review or verify")
    ap.add_argument("--n", type=int, default=20, help="calls per row")
    ap.add_argument("--echo", action="store_true", help="time one key to its echo")
    ap.add_argument("--project", default=str(ROOT), help="the project root (default: Octopus AI)")
    args = ap.parse_args(argv)

    root = project.resolve(args.project)
    name = project.load(root).name
    httpd = todo_server.make_server(0, str(root))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    body = {"id": args.id, "mode": args.mode}

    def call(action, extra=None):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/api/%s?project=%s" % (port, action, name),
            data=json.dumps(dict(body, **(extra or {}))).encode(),
            headers={"X-Todo-Token": todo_server.TOKEN})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    rows = []
    try:
        rows.append(row("start", timed(lambda: call("screen"), 1)))
        tok = project.use(root)
        try:
            s = T.find_session(args.id, args.mode)
            if s is None:
                print("error: no live session for %s" % args.id, file=sys.stderr)
                return 1
            print("session %s  shell %s  claude %s  own %s" % (s["key"], s["shell"], s["claude"],
                                                               bool(s.get("own"))))
            rows.append(row("find_session", timed(lambda: T.find_session(args.id, args.mode),
                                                  args.n)))
            rows.append(row("screen", timed(lambda: T.screen_session(s), args.n)))
            rows.append(row("send", timed(lambda: T.send_session(s, "", []), args.n)))
        finally:
            project.reset(tok)
        rows.append(row("/api/screen", timed(lambda: call("screen"), args.n)))
        rows.append(row("/api/send", timed(lambda: call("send", {"text": "", "keys": []}),
                                           args.n)))
        print("")
        print("| %-14s | %4s | %8s | %8s | %8s |" % ("step", "n", "min ms", "median", "p90"))
        print("|%s|%s|%s|%s|%s|" % ("-" * 16, "-" * 6, "-" * 10, "-" * 10, "-" * 10))
        for r in rows:
            print(r)

        if args.echo:
            before = call("screen")["screen"]["lines"]
            t0 = time.perf_counter()
            call("send", {"text": "x", "keys": []})
            echo = None
            while time.perf_counter() - t0 < 10:
                if call("screen")["screen"]["lines"] != before:
                    echo = ms(time.perf_counter() - t0)
                    break
            call("send", {"text": "", "keys": ["Backspace"]})
            print("")
            print("key to echo: %s" % ("%.0f ms" % echo if echo is not None
                                       else "no echo within 10 s"))
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
