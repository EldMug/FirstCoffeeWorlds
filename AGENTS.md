# Agent Instructions

Start by reading `README.md`.

For repository development, read `wd/fcwagent.md` if present. Development requests do not trigger worldbuilding opening questions.

For actual dungeon briefing, generation, maps, enrichment, revision, or re-rendering, load `dungeon-builder` through the tool's available skill mechanism and follow `WORLD.md`. Canonical skill: `.opencode/skills/dungeon-builder/SKILL.md`; guide: `.opencode/skills/dungeon-builder/guide.md`. If this location is not discovered by the tool, explicitly load/read these files using its supported mechanism. Retain supplied answers and ask only missing requirements, one at a time.

Install the hidden runtime only when a dungeon request needs the CLI, following the guide. Run from the selected checkout root or supply `--root` explicitly. Development/maintenance does not trigger runtime onboarding.

This is the single shared instruction file. OpenCode loads it through project configuration; Codex supports repository `AGENTS.md`. For Claude/Gemini and other tools without that discovery, explicitly tell the tool at startup to read `AGENTS.md`; do not assume automatic loading of this file or the `.opencode/` skill.

For world-link behavior, read `LINKS.md`.

If available, use `link-curator` as the default agent.

Do not duplicate or override `LINKS.md` rules here.
