---
name: dungeon-builder
description: Use when a user requests a dungeon brief, dungeon generation, a dungeon map, dungeon narrative enrichment or revision, or re-rendering saved dungeon artifacts.
---

# Dungeon Builder

Turn explicit dungeon intent into a complete approved brief and requested artifacts. Read checkout `WORLD.md` and [guide.md](guide.md) for exact inputs, defaults, source pairing, print policy, and recovery. Shared `AGENTS.md` routes tools to this canonical skill.

## Briefing Contract

Explicit dungeon intent answers startup's interest choice. Retain supplied answers; ask only missing requirements, one at a time. Development/CLI maintenance instead uses `wd/fcwagent.md` if present.

Collect name, party, grid, type, purpose, tone, treasure, and existing-world connections. A range is descriptive: resolve `party_levels` to 2–6 integers 1–20, or expand a user-chosen target level and party count. Never silently select a level. Name supplies the safe folder slug.

Proposed brief includes exact roster, normalized choices/defaults, seed, selected checkout and `dungeons/<slug>/`, full nine-file bundle, current/history/catalog policy, and Letter map / portrait multipage key printing. Defaults: 40×40 (20–80 each dimension), 40% combat/10% traps, High climax off, one gate (maximum two distinct mechanisms), two items plus lowest-tier coins. Heavy constraints and amounts are in the guide.

If brief is explicitly approved and files requested, execute approved generation now; no second PNG confirmation or development execute gate. Missing required values still need resolution. Questionnaire answers alone create no files. Use structured JSON on stdin `-` or an explicitly permitted brief artifact.

## Quick Reference

| Intent | Action |
| --- | --- |
| Brief only | Propose in conversation |
| Approved files | `generate --brief - --root <checkout>`; retains approved brief seed; omitted seed chooses fresh |
| Check saved data | `validate --dungeon <copy.json>` |
| Publish enrichment | Validate copied JSON, then `render --dungeon <copy.json>` |
| Fresh base | `regenerate --from <saved.json>`; fresh seed unless supplied |

When a dungeon request needs the CLI, install from checkout root as shown in the guide: hidden environment `runtime/.venv` beside this skill, package `runtime/`. Maintenance/briefing does not trigger installation. Run installed CLI from selected checkout root or supply `--root` explicitly.

Example after approval, from selected checkout root:

```sh
.opencode/skills/dungeon-builder/runtime/.venv/bin/fcw-dungeon generate --brief - --seed 0 <<'JSON'
{"name":"Ash Vault","party_levels":[3,3,3,3],"grid":{"width":40,"height":40},"layout":"constructed","theme":"crypt","purpose":"Recover the relic.","tone":"mysterious"}
JSON
```

Writes `dungeons/ash-vault/` plus tracked, unignored `dungeons/Dungeons.md`; CLI does not stage/commit/push.

## Enrichment and Common Mistakes

Explicit building may propose compatible new history in `lore_proposals` with `status:"proposed"`. Read relevant canon; artifact approval does not approve world history or missing definitions. `LINKS.md` is sole link authority; use `world-links` for link edits. Link curation/scans invent no lore.

Copy published JSON before edits. Preserve mechanics, IDs, geometry, budgets, and complete source snapshots unless explicitly requested validated edits. Narrative-only proposals stay in conversation without an artifact-edit request. Render publishes an author revision, archiving old current under `revisions/`; regeneration archives enrichment and rerolls a base. Never edit current manifest-managed files manually or confuse seed replay with recovering authored prose.
