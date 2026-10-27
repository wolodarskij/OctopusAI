---
name: docs-progress
description: Show documentation progress — what has been processed, percent complete, what is next, and whether anything is stuck. Use when the user asks how far along the docs are, what is left, what is being worked on next, or whether anything is stuck.
---

# Documentation progress

This skill reports against `Docs/_worklist.json`. That file is the **source of truth** for the
`unreal-project-docs` run. The skill does not read the markdown tree for the counts. When the worklist
and the tree disagree, the check below shows it.

## Show progress

Run this from `{{PROJECT}}`:

```bash
python - << 'EOF'
import json, os
w = json.load(open("Docs/_worklist.json", encoding="utf-8"))
t = w["targets"]
by = {}
for name, e in t.items():
    by.setdefault(e["status"], []).append(name)
done = len(by.get("done", []))
weight = lambda e: 8 if e["treatment"] == "full" else 1
eff_all = sum(weight(e) for e in t.values())
eff_done = sum(weight(e) for e in t.values() if e["status"] == "done")
print("targets %d / %d  (%.1f%%)" % (done, len(t), 100.0 * done / max(1, len(t))))
print("effort  %.1f%%  (a full directory counts 8 times a data page)" % (100.0 * eff_done / max(1, eff_all)))
print("pages written", sum(len(e.get("files") or []) for e in t.values()))
for s in ("pending", "in-progress", "blocked"):
    rows = by.get(s, [])
    kinds = {}
    for n in rows:
        kinds[t[n]["treatment"]] = kinds.get(t[n]["treatment"], 0) + 1
    print("%-12s %4d  %s" % (s, len(rows), kinds))
last = sorted((e.get("processedUtc") or "", n) for n, e in t.items() if e["status"] == "done")[-5:]
print("last done:", [n for _, n in last])
nxt = sorted((-(t[n].get("depth") or 0), weight(t[n]), n) for n in by.get("pending", []))[:3]
print("next up:  ", [n for _, _, n in nxt])
EOF
```

It prints: targets done / total, the progress weighted by effort (a `full` directory counts 8 times a
single `data` page, so the number shows work, not row count), pages written, the remainder split by
status and treatment, the last five completed targets, and the next three. The next targets are the
deepest first, then the cheapest.

Run it **at the start of every batch** as a check. Show its output only when the user asks where
things stand.

## Check for problems

Compare the worklist with the disk, and report each of these:

| Problem | Test | Meaning |
|---|---|---|
| `IN-PROGRESS` | `status` is `in-progress` | A run stopped in the middle of a target. The directory can be half-written. Delete it and do it again, or set the entry back to `pending` |
| `DONE BUT FILES MISSING` | a path in `files` does not exist under `Docs/` | The worklist claims files that are not on disk |
| `DONE BUT NO FILES RECORDED` | `status` is `done` and `files` is empty | The target was marked done without its output |
| `PENDING BUT OUTPUT EXISTS` | `status` is `pending` and `Docs/<dir>` exists | Half-written output from a stopped run. Delete it before you write again |
| `COUNTS DRIFT` | `counts.done` is not the number of `done` targets | The summary counts are wrong |

A check that finds a problem must exit non-zero, so that a batch stops on it.

## How a batch changes the worklist

"Did I already do this?" must be a mechanical check, not a judgement.

- **Before you write a target**, set its `status` to `in-progress`. If the status is already `done`,
  stop: that target needs a `reprocess`, and a reprocess is destructive (see `unreal-project-docs`).
  If output already exists for a `pending` target, a stopped run left it; delete it first.
- **After the target is complete**, set `status` to `done`, set `processedUtc`, and record in `files`
  the real list of files. Make the list from the directory on disk, not from what the run claims it
  wrote. Do not mark a target done when its file count is below the minimum of its tier.
- Update `counts` in the same write.

Keep a pattern ledger (`Docs/_patterns.json`) for a finding that occurs in many classes, for example an
empty tick or a min/max pair with no validation. Argue the finding in full one time, and after that
*cite* it. The ledger keeps the instance counts exact across batches, and it survives a context
compaction because it is on disk.

## Reading the numbers

- **targets %** counts entries. It moves fast on `data` and `summary` pages and slowly on directories.
- **effort %** weights `full` targets 8 times. This is the correct number. The one or two very large
  classes of a project (a main character or an AnimBP with 200 or more graphs) each take a full
  session.

A large gap between the two percentages means that recent batches took the cheap targets. That is the
intended order: leaf data pages first, so that the class pages that refer to them can link to them.
But early in the run, the target count shows more progress than there is.

## Related

- `unreal-project-docs` — the skill that writes the documentation.
- `Docs/_worklist.json` — `legacyPages` and `completionTest` define what "finished" means, when the
  project uses them: every target `done` **and** every old flat page retired. Zero pending alone is not
  sufficient.
