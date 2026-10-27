import sys, os, glob, struct

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "unreal", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _project  # noqa: E402

_PROJECT = _project.optional_project_root()
guid_hex = sys.argv[1]
if len(sys.argv) > 2:
    root = sys.argv[2]
elif _PROJECT is not None:
    root = os.path.join(str(_PROJECT), 'Content')
else:
    root = 'Content'

b = bytes.fromhex(guid_hex)
needle = b''.join(struct.pack('<I', struct.unpack('>I', b[i:i + 4])[0]) for i in range(0, 16, 4))

total = 0
files = 0
hits = []
for ext in ('*.uasset', '*.umap'):
    for p in glob.glob(os.path.join(root, '**', ext), recursive=True):
        files += 1
        d = open(p, 'rb').read()
        c = d.count(needle)
        if c:
            hits.append((c, os.path.relpath(p, root)))
            total += c
print('needle:', needle.hex())
print('packages scanned:', files)
print('occurrences:', total, 'in', len(hits), 'package(s)')
for c, p in sorted(hits, reverse=True):
    print('   %2d  %s' % (c, p))
