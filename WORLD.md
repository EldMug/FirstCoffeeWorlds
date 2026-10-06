# World Builder

## Opening Questions

1. Are you interested in building a world, including history, legends, places, and maps?
2. Are you interested in creating dungeons using the DND 5.5 ruleset?
3. Are you interested in creating characters?

Use the answers to guide follow-up questions. Do not create directories or files from these answers unless the user explicitly asks.

An explicit dungeon request already answers the broad interest choice: go directly to Dungeons, retaining all supplied answers. Repository development and CLI maintenance use `wd/fcwagent.md` if present, without worldbuilding opening questions.

## Dungeons

If the user wants to create dungeons, use DND 5.5 and load `dungeon-builder` as routed by `AGENTS.md`. See [the dungeon guide](.opencode/skills/dungeon-builder/guide.md) for the executable workflow and exact schema, including lazy hidden runtime installation when needed. Mechanics use the approved 2024 Basic Rules / SRD 5.2.1 pairing; generated design choices are labeled separately.

Do not create files or directories unless the user explicitly asks.

Ask one question at a time.

Collect only missing answers, one at a time. Existing answers are not a new questionnaire. Collect:

1. What level range is the dungeon for?
2. How many characters are expected?
3. What general size should the dungeon be on a grid?
4. What kind of dungeon is it?
5. What is the dungeon's purpose?
6. What tone should it have?
7. How should treasure placement work?
8. Should it connect to existing people, places, items, or history?
9. What is the dungeon's name? This determines its `dungeons/<slug>/` destination.

A range alone is not an exact roster. Before generation, resolve `party_levels` to 2–6 integers from 1–20: ask for each character's level, or a user-chosen target level and party count to expand into a roster. Never silently choose the range's midpoint or maximum. Map dungeon type to `constructed` or `cave` layout and `crypt`, `stronghold`, or `cave` theme, retaining descriptive purpose and tone. Supplied world connections/facts are context; inspect relevant canon before proposing compatible additions.

For grid size, offer examples such as:

1. Small: about 20x20 squares
2. Medium: about 40x40 squares
3. Large: about 60x60 squares
4. Huge: about 80x80 squares
5. Custom size

Default 40x40; supported dimensions are independently 20–80 squares at 5 feet per square. Fit constraints can reject otherwise valid requests; do not silently reduce an explicit room count, roster, difficulty, or creature footprint.

For treasure placement, offer examples such as:

1. Sparse and hidden
2. Balanced across the dungeon
3. Guarded by monsters
4. Mostly in the final room
5. Random placement
6. Custom placement

Defaults: automatic room count; 40% combat / 10% traps / remainder exploration; ordinary fights 60% Low / 40% Moderate; optional High climax off; one blocking gate, maximum two different mechanisms; zero secret rooms (one optional secret room uses a gate slot); mixed nuisance/deadly traps; balanced treasure placement, two magic items, and party-wide coins of 100/500/2,500/10,000 GP by the lowest member's tier (1–4/5–10/11–16/17–20). These quantities, pacing, and mixed-party tier choices are product defaults, not official per-dungeon allowances. Explicit zero overrides are valid.

### Brief and Artifact Approval

Propose the brief before generation. Include name/slug, exact roster, size, layout/theme, purpose/tone, treasure, world context, seed/defaults, selected checkout and `dungeons/<slug>/` destination, new revision with old current preserved in `revisions/`, and print policy: GM/player PNGs, one-page Letter map PDFs fitted portrait/landscape, and multipage portrait Letter GM-key PDF plus Markdown/JSON/manifest. The guide lists the full nine-file bundle and catalog index.

If the user has already explicitly approved the brief and requested files, execute that approved generation without a second confirmation or a development `execute` gate. Missing required values still need resolution, one question at a time. Questions alone never create artifacts. Once requested, use structured JSON via CLI stdin `-`, or a permitted brief artifact file. The CLI neither stages nor commits nor pushes Git files.

### Narrative and Revisions

Explicit dungeon building may propose new history in the dungeon key, labeled proposed. Artifact approval does not approve it as world canon or authorize missing `@` definition headings. `LINKS.md` remains the sole link authority; use `world-links` for link edits and approvals. Link scans never invent lore.

Enrich a copy of published `dungeon.json`; preserve canonical source mechanics, complete snapshots, geometry, IDs, and budgets unless the user explicitly requests a validated mechanical edit. Narrative proposals without an artifact-edit request stay in conversation. Validate the copy and `render` it to publish an author revision, preserving the prior current bundle in `revisions/`. `render` never rerolls; `regenerate` creates a fresh base and archives old enrichment. Never manually modify manifest-managed current files.
