"""Where the T3D text exports live: the directory `t3dDir` of `.octopus/blueprint.json`, relative to
the project root. The default is `T3D`.

Keep the directory outside `Saved/`. The engine mounts nothing there, so no package can import a
file in it. The project's asset-text export toolset moves every export into it."""
import os

import _project

ROOT = str(_project.project_root())
T3D_DIR = os.path.join(ROOT, _project.setting("blueprint", "t3dDir", "T3D"))
