# Browser game: hard rules

*Always applies. The browser-game pack of Octopus AI installs this file. The rules keep the game rules
portable: the core can move to another engine, and the view is replaceable.*

## 1. The core is pure

`src/core` holds the rules of the game. It imports no engine library (for example `phaser`), it
touches no DOM (`window`, `document`, `localStorage`), it uses no host platform (`node:*`, `fs`,
`path`, `process`), and it never calls `Math.random`.

- **Reason:** the core is the part that moves to another engine. It takes data in and emits
  events out, and nothing else.
- **Check:** ESLint enforces it. `eslint.core-purity.js` holds the block for `src/core/**`, and
  `npm run lint` fails on a violation.

## 2. The view is an adapter

`src/view` reads the game state, consumes the events the core emits, and sends commands to the
core. It never holds a rule.

- **Reason:** a rule in the view does not move with the core, and a second engine has to
  find it and write it again.

## 3. Content is data

Every value from the design tables lives in `content/*.json`: tiles, items, enemies, events and
balance numbers. Never hardcode one of them in TypeScript.

- **Reason:** a designer changes the game by editing data, and the core stays the same.
- **Check:** a validate command reads every content file and exits 1 on an error. Run
  `npm run content:validate` before you say a change works.

## 4. Every player-visible string is a locale key

Every string a player can see is a key in `locales/en.json`. The code holds the key and never
the text.

- **Reason:** a translation is a new locale file, and no code changes.

## 5. The simulation is deterministic

The same seed and the same command log give the same run. Every draw comes from a named RNG
stream, one stream for each system (for example `layout`, `deck.items`, `checks`, `ai`). Never
draw from one shared stream.

- **Reason:** a new roll in one system then does not change the results of another system, and
  a bug report with a seed and a command log replays exactly.
- **Check:** ESLint forbids `Math.random` in `src/core`. A test runs one seed twice and compares
  the hash of the two final states.

## 6. A seed replays a level, and bots play without a screen

A `?seed=<n>` in the page URL starts the level of that seed. A headless play command runs whole
runs with bots and no renderer, for example `npm run play -- --seed 42`, and `--seeds <n>`
sweeps a range of seeds.

- **Reason:** a seed makes a level on screen repeatable, and the headless run measures a rule
  change in seconds, before anybody plays by hand.

## 7. Scratch files and memory stay inside the project

Nothing a session writes lives outside the project folder. Project facts go in
`.agents/rules/`, the tickets or the docs, never in a memory file. Scratch files go in `.claude/scratch/`, which version control ignores. Never
write to the system temp folder or the user profile.

- **Reason:** a file outside the project is lost to the next session and to the owner.

## Before you say a change works

```
npm run content:validate
npm run typecheck
npm run lint
npm test
```
