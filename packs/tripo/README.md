# The `tripo` pack

The skill for the Tripo CLI: 3D models, textures, rigs and preset animations from a prompt or an image. The pack
contract is `packs/PACKS.md`.

## What the pack holds

| Path | What it is |
|---|---|
| `ruleset.json` | the title and the payload |
| `skills/tripo-cli/SKILL.md` | the skill: install and login, the rules of a paid task, the commands, the record of each task. Install copies it to `<project>/.agents/skills/tripo-cli/` |

## Setup

1. `npm install -g tripo-cli` (Node.js 20 or later).
2. `tripo login --region ov|cn`, or `TRIPO_API_KEY` in the environment.
3. `tripo doctor` checks the key, the region, the network and the balance.

The CLI ships its own agent guide for its version: `tripo docs`, and `tripo docs --topic <topic>` for one part.
The skill names the topics, so it stays correct when the CLI changes.

The `model-sprites` pack turns a Tripo model into palette pixel sprites.
