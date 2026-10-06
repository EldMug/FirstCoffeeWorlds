# First Coffee Worlds

Clone this repository to build a DND 5.5 world.

## Agent Startup

Agents should start here.

Use the shared [AGENTS.md](AGENTS.md) for tool routing. OpenCode loads it through project configuration; Codex supports repository `AGENTS.md`. With Claude/Gemini or other tools lacking that discovery, explicitly request “Read AGENTS.md” at startup, then load/read the referenced canonical skill and guide through the tool's supported mechanism. No tool-specific startup files are required.

For worldbuilding without a stated interest, ask the questions in `WORLD.md`. Explicit dungeon intent goes directly to dungeon briefing; retain supplied answers. For repository development or CLI maintenance, read `wd/fcwagent.md` if present; skip worldbuilding opening questions.

For link behavior, read `LINKS.md`.

If using opencode, this repository includes `link-curator` as the default project agent.

## World Setup

Use `WORLD.md` to guide world, dungeon, and character creation questions.

Do not create directories or files unless the user explicitly asks.

## Dungeon Builder

Build DND 5.5 dungeons from exact party rosters, with GM/player maps and complete offline GM keys. Start with [the dungeon guide](.opencode/skills/dungeon-builder/guide.md) for JSON inputs, CLI commands, source pairing, printing, revisions, and recovery. OpenCode uses the `dungeon-builder` skill through the default `link-curator` agent; other tools use the shared AGENTS routing.

Linux; Python >=3.11,<3.15. After a dungeon request needs the CLI, install in the hidden runtime environment from the checkout root. Briefing and repository maintenance do not trigger installation:

```sh
python3 -m venv .opencode/skills/dungeon-builder/runtime/.venv
.opencode/skills/dungeon-builder/runtime/.venv/bin/python -m pip install .opencode/skills/dungeon-builder/runtime
.opencode/skills/dungeon-builder/runtime/.venv/bin/fcw-dungeon --help
.opencode/skills/dungeon-builder/runtime/.venv/bin/fcw-dungeon brief-template
```

Invoke the installed CLI from the selected checkout root, or supply its absolute path with `--root`. Installation stays hidden; requested output remains `dungeons/<slug>/` at the selected root.

Development environment and full local suite (tests/fixtures under ignored `wd/tests/` are deliberately absent from fresh clones):

```sh
python3 -m venv wd/.venv
wd/.venv/bin/python -m pip install -e '.opencode/skills/dungeon-builder/runtime[test]'
PYTHONPATH=wd wd/.venv/bin/python -m unittest discover -s wd/tests -t wd -v
```

Some tests use an existing host CommonMark oracle in isolated subprocesses; see the guide's verification notes. Runtime generation and rendering need no network after installation. Generation requires an approved brief and explicit artifact request; output lives at `dungeons/<slug>/`, with the tracked, unignored index `dungeons/Dungeons.md`. The CLI does not stage, commit, or push.

After changing OpenCode agents or skills, quit and restart OpenCode to load them.

## Link Curator

The link curator scans Markdown files for people, places, events, factions, and important items.

It proposes link edits before applying them.

It asks questions before creating missing definitions.

It reports empty definition entries.
