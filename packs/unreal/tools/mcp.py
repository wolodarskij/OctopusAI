"""Minimal unreal-mcp client over the HTTP endpoint, via curl.

A script talks to the editor's MCP server directly, without the Claude Code MCP client.
urllib gets an empty body back from tools/call; curl works, so this shells out to it.

The endpoint is `mcpUrl` of `.octopus/unreal.json`; the default is
`http://127.0.0.1:8000/mcp`, the port the editor's ModelContextProtocol plugin uses.

Responses are SSE-framed (`event: message` / `data: {...}`). The editor keeps the
stream open after the answer, so the reader ends curl at the first complete message.
"""
import contextlib, json, subprocess, sys, tempfile, threading, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402

URL = "http://127.0.0.1:8000/mcp"
if _project.optional_project_root() is not None:
    URL = _project.setting("unreal", "mcpUrl", URL)
_sid = {"v": None}


@contextlib.contextmanager
def _curl_lines(cmd, limit):
    """Run curl and yield its output lines, decoded and without the line end.
    Kill curl on exit. Raise TimeoutExpired when curl runs past `limit` seconds."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    expired = []
    timer = threading.Timer(limit, lambda: (expired.append(True), proc.kill()))
    timer.start()
    try:
        yield (l.decode("utf-8", "replace").rstrip("\r\n")
               for l in iter(proc.stdout.readline, b""))
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stdout.close()
        if expired:
            raise subprocess.TimeoutExpired(cmd, limit)


def _post(payload):
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f)
    try:
        cmd = ["curl", "-s", "-N", "-X", "POST", URL,
               "-H", "Content-Type: application/json",
               "-H", "Accept: application/json, text/event-stream"]
        if _sid["v"]:
            cmd += ["-H", f"Mcp-Session-Id: {_sid['v']}"]
        cmd += ["--data-binary", "@" + path]
        data, other = [], []
        with _curl_lines(cmd, 300) as lines:
            for line in lines:
                if line.startswith("data:"):
                    data.append(line[6:] if line.startswith("data: ") else line[5:])
                elif not line:
                    if data:
                        break
                elif not line.startswith(("event:", "id:", ":")):
                    other.append(line)
    finally:
        os.unlink(path)
    lines = data or other
    if not lines:
        raise RuntimeError("empty response from MCP")
    return json.loads("\n".join(lines))


def connect():
    """Establish a session; the id comes back as a response header."""
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                              "clientInfo": {"name": "sweep", "version": "1"}}}, f)
    try:
        cmd = ["curl", "-s", "-N", "-D", "-", "-o", os.devnull, "-X", "POST", URL,
               "-H", "Content-Type: application/json",
               "-H", "Accept: application/json, text/event-stream",
               "--data-binary", "@" + path]
        with _curl_lines(cmd, 120) as lines:
            for line in lines:
                if line.lower().startswith("mcp-session-id:"):
                    _sid["v"] = line.split(":", 1)[1].strip()
                    break
    finally:
        os.unlink(path)
    if not _sid["v"]:
        raise RuntimeError("no Mcp-Session-Id returned")
    return _sid["v"]


def tool(toolset, name, args, rid=99):
    d = _post({"jsonrpc": "2.0", "id": rid, "method": "tools/call",
               "params": {"name": "call_tool",
                          "arguments": {"toolset_name": toolset, "tool_name": name,
                                        "arguments": args}}})
    if "error" in d:
        raise RuntimeError(f"{name}: {json.dumps(d['error'])[:300]}")
    r = d.get("result", {})
    txt = (r.get("content") or [{}])[0].get("text", "")
    if r.get("isError"):
        raise RuntimeError(f"{name}: {txt[:300]}")
    try:
        return json.loads(txt).get("returnValue")
    except Exception:
        return txt


BP = "editor_toolset.toolsets.blueprint.BlueprintTools"
ASSET = "editor_toolset.toolsets.asset.AssetTools"
OBJ = "editor_toolset.toolsets.object.ObjectTools"
