---
name: todo-groups
description: Refresh the Todo groups - put every open ticket that sits in no group into an existing or a new group, rebuild the groups record, and print the table of groups with the open counts. Also create, rename or delete a group. Trigger - "refresh the groups", "refresh the ticket groups", "group the new tickets", "update the groups", "regroup", "which tickets are in no group", "table of groups", "print the groups", "rename the group", "delete the group".
---

# Todo groups

A group holds open tickets that one session can work together. Only open tickets count: a ticket
in Verification or Done leaves its group. `groups.store` in `.octopus/project.json` selects the
store. The steps differ for each store.

## Every trigger does the whole refresh

A request for the table, the groups or a regroup runs the steps and then prints the table. A table
with tickets in no group is wrong.

## `map` store (the default)

`TODO/.groups.json` maps a group name to `group:` slugs and ticket ids. `TODO/GROUPS.md` is
generated from it. Each `autoGroups` entry is one more group: every open ticket with its tag. A
ticket in an auto group also sits in its normal group.

1. `python .octopus/todo.py groups`. It rebuilds `GROUPS.md` and lists each open ticket in no
   group: id, difficulty, slug, title, and the groups where its `related` and `blocked-by`
   tickets sit. The last lines name each group outside 5 to 10 tickets.
2. Choose a group for each ticket:
   - the group of its relations, when the output names one;
   - else the group whose slugs cover the same subject. `graph` lists every slug with its tickets;
   - a ticket with no slug gets one first: `link <id> --group <slug>`. Reuse a slug when the
     subject matches. A new slug is kebab-case and names the subject;
   - when no group covers the subject, a new name creates one.
3. `python .octopus/todo.py groups --set "<group>" <slug or id>...`. A slug places every ticket
   with that slug, so prefer it. An id places one ticket. Each item leaves any other group.
4. Keep every group between 5 and 10 tickets. Split a group over 10 with `--set` into two names.
   Merge a group under 5 into the nearest group.
5. `python .octopus/todo.py check`, then `python .octopus/todo.py groups --table`.

## `files` store

Each group is `TODO/groups/<slug>.md`, and each ticket names its group in `group:`. With
`groups.required`, `check` fails an open ticket in no group.

1. `python .octopus/todo.py groups`. It prints each group with its count per board, and the open
   tickets in no group. `--json` gives the same as data.
2. Choose a group for each ticket by its subject and its relations. When no group fits:
   `python .octopus/todo.py group <slug> --title "..." --description "..."`.
3. `python .octopus/todo.py regroup <ids...> <slug>`.
4. `python .octopus/todo.py check`, then `python .octopus/todo.py groups`.

## Other group commands

| Request | Call |
|---|---|
| create or retitle a group | `group <slug> --title "..."` |
| rename a group | `group <slug> --rename <new>` |
| delete a group | `group <slug> --delete`; `--into <other>` first moves its open tickets (files store) |
| run a group | the `todo-run-group` skill |

## Report

One line per placed ticket, `T0001 -> <group>`, and one line per split or merged group. Then the
table as the command printed it, without the rows that have 0 open tickets. Nothing else.
