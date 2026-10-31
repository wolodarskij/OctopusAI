---
name: tripo-cli
description: Make 3D assets with the Tripo CLI (`tripo`) - a model from a text prompt or an image, a texture, a rig, preset animations, a concept image, a format conversion - download the result, and record each task next to the asset. Use when asked to "make a 3D model", "generate a model with Tripo", "rig it", "animate it", "retarget a walk", "make a reference image", "re-roll the model", or when a `tripo` command fails on login, credits or a parameter. Also use to install and log in the CLI.
---

# The Tripo CLI

`tripo` turns a prompt, an image or a model file into local files: the model, a `preview.png` and a `task.json`.
Every generation costs credits of the owner's Tripo account.

## Set up

1. Install it once, with Node.js 20 or later: `npm install -g tripo-cli`.
2. Check it: `tripo doctor`. It names a missing key, a key of the wrong region, and the balance.
3. With no key, log in yourself. Ask the owner which Tripo site the account is on: `ov` (developers.tripo3d.ai,
   e-mail sign-in) or `cn` (developers.tripo3d.com). Then:

   ```bash
   tripo login --region ov --no-wait
   ```

   It prints a URL and a code and exits. Give both to the owner, and ask the owner to authorize the key there.
   When the owner says it is done, run the same command again: `"status":"logged_in"`. Never ask the owner to
   paste a key into the chat. `TRIPO_API_KEY` in the environment also works.

## The rules

- **Put `TRIPO_CLIENT_APP=<your AI provider>` on each command:** `claude` for Claude Code, `codex` for Codex.
- **Run each `tripo make`, `tripo generate` and `tripo anim` in the background.** A task runs for minutes and
  the command blocks until it ends. Give it no timeout shorter than 15 minutes, and do not stop it because a task
  id showed in the log.
- **Validate before you spend:** the same command with `--dry-run --json` costs nothing and prints the plan, the
  model, the parameters and the errors. Run the real command only when `valid` is true.
- **Read stdout as the result.** With `--json`, stdout is one JSON line: `task_id`, `output_dir`, `model_file`,
  `preview`, `credits_consumed`. Read `preview.png` to judge the model; `tripo redo <task>` makes it again with a
  new seed.
- **Act on the exit code:** 0 done · 2 a wrong parameter · 3 login (run `tripo doctor`) · 4 no credits (tell
  the owner to run `tripo topup`) · 5 content policy · 6 the task failed, credits refunded (on `timeout` it still
  runs: `tripo task watch <id>`, never `redo`) · 7 network (check `tripo balance` before you run it again: the task
  can exist) · 9 rate limit (wait, then again).
- **Never invent a parameter.** `-p key=value` passes any key to the API. Use only the keys of
  `tripo docs --topic commands/generate` and `tripo docs --topic commands/process`.
- **Keep the default model** (`tripo-v3.1`, or `tripo-p1` for a low face count) unless the owner names another.
- **Record each task next to the asset.** Keep the model, `preview.png` and `task.json` in the asset's source
  folder, and add one row to its `SOURCE.md`: the file, the task id, the full command and the prompt. A later
  session makes the asset again from that row.

## The commands

| Job | Command |
|---|---|
| a model from a prompt | `tripo make "<prompt>" --model tripo-v3.1 -p face_limit=5000 -p texture=true -o <dir> --name <name> --no-open --yes --json` |
| a low-poly model | `tripo make "<prompt>" -p face_limit=6000 -o <dir> --name <name> --no-open --yes --json` |
| a model from an image, rigged | `tripo make <image.png> --then rig -p face_limit=20000 -p texture=true -o <dir> --yes --json` |
| a model from 2 to 4 views | `tripo make front.png back.png -o <dir> --yes --json` |
| a game preset | `tripo make "<prompt>" --for game-mobile` (or `game-pc`, `anim`, `toy`, `ar-web`, `film`, `print`) |
| an image from a prompt | `tripo generate text-to-image "<prompt>"` |
| an image changed by a prompt, with reference images | `tripo generate image-to-image <image> [<image> ...] --prompt "<prompt>"` (the prompt names them `image[1]`, `image[2]`) |
| can it be rigged | `tripo anim check <task or file>` |
| a rig | `tripo anim rig <task> --rig-type biped --out-format glb` |
| preset animations on a rig, in place | `tripo anim retarget <rig task> --animation preset:walk preset:fall --animate-in-place --out-format glb -o <dir>` (at most 5, each billed) |
| a new texture | `tripo model texture <task or file>` |
| another format | `tripo make @last --then convert:fbx` or `tripo model convert <task>` |
| an existing model file into Tripo | `tripo model import <file.glb>` |
| a task again with a new seed | `tripo redo <task>` |
| the files of a finished task | `tripo task get <task> --download -o <dir>` |
| the credits | `tripo balance`, `tripo usage` |

A task reference is a task id, `@last`, or `@<name>` from `--name`. Steps chain with `--then`, such as
`--then texture,rig,convert:fbx`, and a pipe passes a task to the next command:
`tripo make cat.png --json | tripo anim rig --json`.

## The CLI's own docs

`tripo docs` prints the agent guide of the installed version. `tripo docs --topic <topic>` prints one part:
`commands/make`, `commands/generate`, `commands/process`, `commands/task`, `commands/batch`, `examples/game-asset`,
`examples/animation`, `common-errors`. Read the part before you use a command that this page does not show.

## Next

A rigged and animated GLB becomes palette pixel sprites with the `model-sprites` pack, when the project has it.
