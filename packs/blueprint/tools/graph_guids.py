import io, os, re, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "unreal", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402

_project.optional_project_root()

sys.stdout.reconfigure(encoding='utf-8')

path = sys.argv[1]
raw = io.open(path, 'rb').read()
t = raw.decode('utf-16') if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else raw.decode('utf-8', 'replace')
lines = t.split('\n')

# Walk the object tree; a graph body block is `Begin Object Name="X"` at some depth whose
# matching End Object encloses a GraphGuid at THAT depth (not inside a child object).
stack = []
cur = {}
out = {}
for ln in lines:
    s = ln.strip()
    if s.startswith('Begin Object'):
        m = re.search(r'Name="([^"]+)"', s)
        stack.append(m.group(1) if m else None)
    elif s.startswith('End Object'):
        if stack:
            stack.pop()
    elif s.startswith('GraphGuid=') and stack:
        g = re.match(r'GraphGuid=([0-9A-F]{32})', s)
        if g:
            name = stack[-1]
            # only record the first guid seen at this object's own level
            key = (len(stack), name)
            if name and name not in out:
                out[name] = g.group(1)

for k in sorted(out):
    print('%-32s %s' % (k, out[k]))
