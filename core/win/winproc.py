"""winproc.py - the process and window calls the Agent Board needs, on whatever machine runs it.

Every caller imports `winproc`. On Windows the module it gets is `winproc_nt` (kernel32 and
user32 through ctypes); on macOS and Linux it is `core/posix/posixproc.py`, which answers the
same calls from libproc or `/proc`. The module replaces itself in `sys.modules`, so a caller,
and a test that patches `winproc.processes`, holds the real module and nothing in between.
"""

import os
import sys

if os.name == "nt":
    import winproc_nt as _impl
else:
    _posix = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "posix")
    if _posix not in sys.path:
        sys.path.insert(0, _posix)
    import posixproc as _impl

sys.modules[__name__] = _impl
