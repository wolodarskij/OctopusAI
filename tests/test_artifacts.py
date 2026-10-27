"""The Artifacts view: the manifest, the file guard, the Markdown renderer and `/view/`.
python -m unittest tests.test_artifacts"""

import json
import threading
import urllib.error
import urllib.parse
import urllib.request

from support import FixtureCase

import artifacts
import todo_server  # noqa: E402


class Manifest(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("Arts")
        (self.root / "Docs" / "Game").mkdir(parents=True)
        (self.root / "Docs" / "Game" / "page.html").write_text(
            "<html><head><title>The Page</title></head><body>x</body></html>", encoding="utf-8")
        (self.root / "Docs" / "other.html").write_text("<title>Other</title>", encoding="utf-8")
        (self.root / "Docs" / "index.md").write_text("# Wiki home\n\ntext\n", encoding="utf-8")

    def test_add_reads_the_title_and_found_drops_the_page(self):
        e = artifacts.add(self.root, "Docs/Game/page.html", description="one  line", ticket="T0001")
        self.assertEqual(e["title"], "The Page")
        self.assertEqual(e["description"], "one line")
        self.assertEqual([f["path"] for f in artifacts.found(self.root, {})], ["Docs/other.html"])
        d = json.loads((self.root / ".octopus" / "artifacts.json").read_text(encoding="utf-8"))
        self.assertEqual(d["artifacts"][0]["path"], "Docs/Game/page.html")

    def test_add_again_updates_the_entry(self):
        artifacts.add(self.root, "Docs/Game/page.html", date="2026-01-01")
        artifacts.add(self.root, "Docs/Game/page.html", title="New", date="2026-01-02")
        rows = artifacts.load(self.root)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["title"], rows[0]["date"]), ("New", "2026-01-02"))

    def test_url_entry_needs_https(self):
        artifacts.add(self.root, "https://claude.ai/code/artifact/x", title="Remote")
        self.assertEqual(artifacts.load(self.root)[0]["url"], "https://claude.ai/code/artifact/x")
        with self.assertRaises(ValueError):
            artifacts.add(self.root, "http://example.com/x")

    def test_remove(self):
        artifacts.add(self.root, "Docs/other.html")
        self.assertTrue(artifacts.remove(self.root, "Docs/other.html"))
        self.assertFalse(artifacts.remove(self.root, "Docs/other.html"))
        self.assertEqual(artifacts.load(self.root), [])

    def test_wiki_lists_the_index_first(self):
        (self.root / "Docs" / "a.md").write_text("# A\n", encoding="utf-8")
        self.assertEqual(artifacts.wiki(self.root, {}), ["Docs/index.md", "Docs/a.md"])

    def test_safe_path_refuses_what_is_closed(self):
        (self.root / ".git").mkdir(exist_ok=True)
        (self.root / ".git" / "config").write_text("x", encoding="utf-8")
        self.assertIsNotNone(artifacts.safe_path(self.root, "Docs/other.html"))
        for rel in ("../x", "Docs/../../x", str(self.root / "Docs" / "other.html"), "/etc/passwd",
                    ".octopus/project.json", ".git/config", "Docs", "Docs/Game",
                    "C:/Windows/win.ini", "Docs/other.html:stream"):
            self.assertIsNone(artifacts.safe_path(self.root, rel), rel)
        with self.assertRaises(ValueError):
            artifacts.add(self.root, ".octopus/project.json")
        with self.assertRaises(ValueError):
            artifacts.add(self.root, "../outside.html")

    def test_render_md_escapes_raw_html_and_bad_links(self):
        page = artifacts.render_md("# T\n\n<script>alert(1)</script> [x](javascript:alert(1)) "
                                   "[ok](../index.md)\n")
        self.assertNotIn("<script>", page.split("<main>")[1])
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn('href="javascript', page)
        self.assertIn('<a href="../index.md">ok</a>', page)


class ViewRoute(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.make("View Me")
        (self.root / "Docs").mkdir()
        (self.root / "Docs" / "page.html").write_text("<title>P</title>", encoding="utf-8")
        (self.root / "Docs" / "index.md").write_text("# Home\n", encoding="utf-8")
        self.httpd = todo_server.make_server(0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def get(self, path, headers=None):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.httpd.server_address[1], path),
                                     headers=headers or {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def view(self, rel, key=None):
        return self.get("/view/%s/%s/%s" % (key or todo_server.VIEW_TOKEN,
                                            urllib.parse.quote("View Me"), rel))

    def test_a_file_comes_with_the_sandbox_policy(self):
        st, h, body = self.view("Docs/page.html")
        self.assertEqual(st, 200)
        self.assertIn("sandbox", h["Content-Security-Policy"])
        self.assertIn(b"<title>P</title>", body)

    def test_markdown_comes_rendered(self):
        st, h, body = self.view("Docs/index.md")
        self.assertEqual(st, 200)
        self.assertTrue(h["Content-Type"].startswith("text/html"))
        self.assertIn(b'<h1 id="home">Home</h1>', body)

    def test_closed_paths_are_404(self):
        for rel in ("..%2Foutside.txt", "Docs/..%2F..%2Fx", ".octopus/project.json", "Docs"):
            self.assertEqual(self.view(rel)[0], 404, rel)

    def test_the_board_key_does_not_open_view(self):
        self.assertEqual(self.view("Docs/page.html", key=todo_server.TOKEN)[0], 403)

    def test_the_view_key_does_not_open_the_api(self):
        st, _, _ = self.get("/api/artifacts?project=View%20Me",
                            {"X-Todo-Token": todo_server.VIEW_TOKEN})
        self.assertEqual(st, 403)
        st, _, body = self.get("/api/artifacts?project=View%20Me",
                               {"X-Todo-Token": todo_server.TOKEN})
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body)["found"][0]["path"], "Docs/page.html")
