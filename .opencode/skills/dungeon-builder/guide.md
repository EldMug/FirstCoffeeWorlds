# Dungeon Builder

Linux, Python >=3.11,<3.15. `fcw-dungeon` builds DND 5.5 dungeons and publishes validated GM/player maps, complete offline GM keys, and canonical JSON. Shared `AGENTS.md` routes actual dungeon requests to this canonical skill; OpenCode's default `link-curator` invokes `dungeon-builder`. Quit and restart OpenCode after agent/skill changes; config-time files load once.

## Install and Commands

After an actual dungeon request needs the CLI, install from the checkout root into the hidden runtime environment. Maintenance and briefing alone do not trigger installation:

```sh
python3 -m venv .opencode/skills/dungeon-builder/runtime/.venv
.opencode/skills/dungeon-builder/runtime/.venv/bin/python -m pip install .opencode/skills/dungeon-builder/runtime
.opencode/skills/dungeon-builder/runtime/.venv/bin/fcw-dungeon --help
```

With that environment activated, commands are below. Run from the selected checkout root, or pass its absolute path with `--root`; the hidden installation directory is not the output root:

```text
fcw-dungeon brief-template
fcw-dungeon generate --brief <file|-> [--seed uint64] [--root checkout]
fcw-dungeon validate --dungeon <file|->
fcw-dungeon render --dungeon <file|-> [--root checkout]
fcw-dungeon regenerate --from <dungeon.json> [--brief <file|->] [--seed uint64] [--root checkout]
python -m fcw_dungeon <same arguments>
```

`-` reads JSON from stdin for `--brief` and `--dungeon`; `--from` takes a file. Help and `brief-template` print to stdout without creating a dungeon. `validate` is read-only. `generate`, `render`, and `regenerate` are explicit publication commands, with no interactive wizard. For agent use, approve the proposed brief and request artifacts first. Already approved generation needs no second confirmation. Questions or narrative proposals alone create no files.

Default root is the nearest current-directory ancestor with `.git`; outside a checkout, supply `--root` pointing to an existing nonsymlink directory. All bundles go beneath that root's `dungeons/`; there is no arbitrary output-folder flag. Seed is unsigned 64-bit, **0–18446744073709551615**, including zero. Generate uses flag seed, then brief seed, then a newly chosen seed. Regenerate uses a fresh seed unless `--seed` is supplied, even if the saved/revised brief has a seed. The resolved seed is saved and reported. Render uses no randomness.

Exit 0 means success, 2 invalid input/data, 1 generation, dependency, render-fit/font/image, or publication failure. Errors identify a field/code or constraint without a traceback. Successful publication reports current path, revision, and GM seed. The CLI does not stage, commit, or push.

## Brief Input and Normalization

Only `name` and `party_levels` must be supplied before normalization. Collect existing WORLD subjects (party, size, type, purpose, tone, treasure, connections) and name without reasking supplied answers. A level range is not a roster: obtain each level or a user-chosen target and count before generation.

Example structured input, usable via stdin after approval:

```json
{
  "name": "Ash Vault",
  "party_levels": [3, 3, 3, 3],
  "grid": {"width": 40, "height": 40},
  "layout": "constructed",
  "theme": "crypt",
  "purpose": "Recover the relic.",
  "tone": "mysterious",
  "treasure": {"placement": "guarded"},
  "world_context": {"references": [], "facts": []},
  "seed": 0
}
```

Use `fcw-dungeon brief-template` for a complete normalized example. JSON is UTF-8, object-only, strict about duplicate/unknown keys, nonfinite numbers, integer types (no booleans/floats for integers), and depth above 128. Brief input limit is 1 MiB; dungeon input limit 16 MiB. Brief strings normalize to NFC and LF; authored dungeon CLI input retains original saved strings after validation. Packaged Draft 2020-12 schemas use local references only: `.opencode/skills/dungeon-builder/runtime/src/fcw_dungeon/data/schemas/{brief,common,dungeon,catalog,manifest}.schema.json` from the checkout root.

| Field | Default / supported contract |
| --- | --- |
| `schema_version` | `1`; canonical schema/generator/catalog versions are all `1` |
| `name`, `slug` | Name 1–80 characters, nonblank, no control characters; slug 1–64 lowercase alphanumeric groups separated by hyphens. Omitted slug derives from ASCII-normalized name; supply explicit slug if empty or colliding. Traversal/separators fail. |
| `party_levels` | Required ordered array of 2–6 integer levels 1–20 |
| `grid` | Width/height `40` each; independently 20–80; `feet_per_square:5` only |
| `layout`, `theme` | `constructed`, `crypt`; layout `constructed` or `cave`; theme `crypt`, `stronghold`, or `cave`. Cave layout defaults theme to cave. |
| `purpose`, `tone` | `Explore the dungeon and discover its objective.`, `mysterious`; nonempty, maximum 4,096 / 256 characters |
| `room_count` | `null` automatic; explicit integer 4–48 |
| `combat_percent`, `trap_percent` | `40`, `10`; each 0–100, sum <=100; remainder exploration |
| `climax` | `false`; `true` replaces one combat room with High, requiring a rounded combat slot |
| `gate_count`, `secret_rooms` | `1`, `0`; gates 0–2, secrets 0–1 and <=gate count; two gates require different mechanisms |
| `trap_severity` | `mixed`; also `nuisance`, `deadly` |
| `pixels_per_square` | `64`; integer 16–128, subject to total pixel cap |
| `seed` | `null` before generation; optional uint64 |
| `world_context` | `{"references":[],"facts":[]}`; each array <=64 strings, each <=4,096 characters; supplied canon/context, not approval for new definitions |
| `treasure` | See below; partial nested inputs receive missing defaults |

Treasure normalizes to `{"placement":"balanced","magic_items":2,"coin_gp":<tier allowance>,"rarity_override":null,"custom":[]}`. `magic_items` allows 0–20; coins 0–1,000,000,000 GP party-wide. Omitted/null coins resolve from the lowest member's tier: levels 1–4 -> 100 GP, 5–10 -> 500, 11–16 -> 2,500, 17–20 -> 10,000. Explicit zero is preserved. These quantities are **per-dungeon product defaults**, not source campaign-tier totals (11/34/30/25 items). `rarity_override` is null or common/uncommon/rare/very-rare/legendary; no Artifact generation.

Without a rarity override, generated rarity uses the source d100 table, then selects an item with its actual catalog rarity. Percentages below are source probabilities; choosing the lowest member's tier for a mixed party is a product choice.

| Lowest level tier | Common | Uncommon | Rare | Very Rare | Legendary |
| --- | --- | --- | --- | --- | --- |
| 1–4 | 54 | 37 | 9 | 0 | 0 |
| 5–10 | 30 | 51 | 17 | 2 | 0 |
| 11–16 | 11 | 23 | 36 | 23 | 7 |
| 17–20 | 0 | 0 | 20 | 44 | 36 |

Placement: `sparse_hidden` selects one nonentrance room; `balanced` cycles nonentrance rooms; `guarded` uses combat rooms; `final_room` uses objective room; `random` uses seeded eligible rooms; `custom` takes allocations `{room_id,coin_gp,magic_item_ids}`. Custom totals must equal the allowance and cover each T001… instance exactly once with valid room IDs; noncustom modes require `custom:[]`. Guarded nonzero rewards require a combat slot. Placement does not alter quantity or source item rarity. Choices exhaust unique items within the chosen rarity pool before repeating that pool.

## Fit, Pacing, and Progression

Automatic room target is half-up `0.3 * sqrt(width*height)`, clamped 4–48: square 20/40/60/80 grids target 6/12/18/24 rooms. Combat and traps use half-up counts, traps capped to remaining rooms. Ordinary fights choose Low 60% / Moderate 40%; High is optional climax only. Mixed traps choose nuisance/deadly 50/50, then a matching source trap. Geometry, pacing, placement, and these weights are product choices.

Generation has **50 complete attempts total**. Automatic counts may reduce after the first ten attempts, down to four; the requested brief retains `room_count:null` and actual counts are saved separately. Explicit counts never reduce. Encounter search limits groups to three definition types, initial monsters to twice party count, 100,000 candidate sets, 10,000 placement nodes per group and 100,000 per room. Actual XP must be 60–100% of budget. Source footprints are Small/Medium 1×1, Large 2×2, Huge 3×3, Gargantuan 4×4, with nonoverlap and one entry square per PC. Traps try 100 trigger candidates; simple safe-bypass path search is bounded to 10,000 nodes.

An otherwise schema-valid request can fail `E_LAYOUT_FIT` or encounter/trap fitting. High-level rosters on small grids, dense explicit counts, source-size packing, or bounded safe paths can exhaust searches. Errors report the failed constraints; choose a larger grid or explicitly revise the brief. Never silently lower difficulty, roster, explicit count, budget coverage, or monster size.

Constructed/cave layouts include real connected rooms/passages, a physical alternate-route loop, doors, entrance and objective. Gates are keyed locks, persistent levers, or secret doors; two must differ. Secret doors consume a gate slot. Prerequisites and mundane trap bypass/escape points must be reachable without self-locks/cycles; a gate clue and stated solution appear in the GM key. This proves reachable actions, not that every player will solve a puzzle or succeed at a check.

Exact encounter budgets sum source per-character Low/Moderate/High rows, without 2014 count multipliers. Mixed-level summation is a **generator extension**: `[1,3]` budgets are 200/300/500; four level-1 characters 200/300/400; five level-3 characters Moderate 1,125. GM keys retain contributions, target/actual XP, placements, and source cautions. XP arithmetic alone does not guarantee difficulty; terrain, action economy, high CR, and unusual abilities still matter. Lowest-member trap/item tier is a conservative mixed-party product choice.

## Sources and Offline Contents

Required baseline: [2024 Basic Rules](https://www.dndbeyond.com/sources/dnd/br-2024), paired with official [SRD 5.2.1](https://media.dndbeyond.com/compendium-images/srd/5.2/SRD_CC_v5.2.1.pdf) for distributable full rule text. PDF SHA256: `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87`. Catalog digest: `cceef40b94231f0f9de32b4e8ad8947f818fd12f6c6529d8c7972246693de9f7`. Relevant stat blocks, spells, equipment, glossary, traps, and treasure were cross-checked against their recorded Basic Rules sections. This is not SRD 5.1/2014.

Base variants only: no mechanical lair benefits. Vampire uses 10,000 XP and three legendary uses/resistances, not its lair variant. Bandit Captain retains source Pistol mechanics. Variant-size entries preserve original wording while placement selects the supported base size. Poisoned Darts' pinned SRD states its fewer-than-three-activations reset clause; the Basic Rules web page omits that sentence. Full pinned SRD wording is retained, not silently reconciled by invention. Tier trap features include the selected source scaling and complete operating wording.

Three curated theme packs cover crypt, stronghold, and cave. Canonical `sources` contains complete trusted selected-entry dependency closure: stat blocks, spells, conditions/rules, equipment, traps, and items, with edition, PDF pages, URLs, hashes, and metadata. The key's deduplicated appendices reproduce the **whole source bodies**, not summaries or links alone. No runtime web retrieval or PDF parser is needed. Complete snapshots must remain intact for authored rendering; rehashed/truncated/foreign sources and unsupported versions/digests fail validation, without silent migration.

Attribution is packaged and printed in both GM keys:

> This work includes material from the System Reference Document 5.2.1 (“SRD 5.2.1”) by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2.1 is licensed under the Creative Commons Attribution 4.0 International License, available at https://creativecommons.org/licenses/by/4.0/legalcode.

## Bundle, Sharing, and Printing

```text
dungeons/
  Dungeons.md                 # tracked, unignored managed index + user prose
  ash-vault/                 # latest current files directly here
    brief.json
    dungeon.json             # sole canonical export input
    manifest.json
    map-gm.png
    map-player.png
    map-gm.pdf
    map-player.pdf
    room-key.md
    room-key.pdf
    revisions/
      r0001/                 # complete prior nine-file bundle
      r0002/
```

Only **player map** PNG/PDF files are designed for sharing. They retain visible floor, ordinary doors, grid, entrance, name and scale; hide secret room/tunnel/door floor and its boundary. They omit room IDs, locks/reveal types, monsters, traps, treasure, objectives, GM seed/counters, and sources. Canonical JSON, brief, manifest, index, GM maps and keys are GM material.

GM maps have room numbers keyed to R001…, contents and treasure markers, gate/control/objective/climax marks, trap trigger/effect/bypass areas. Key documents include every room's geometry, role, narrative, exact encounters, complete trap mechanics, rewards, gates/clues/solutions and proposed lore, followed by full source appendices and attribution.

Each map PDF is **one US Letter page**, aspect-preserving whole-map fit: portrait 612×792 or landscape 792×612 points, larger scale wins, portrait on a tie. It is vector drawing, not an embedded raster, with 36pt margins, 24pt title/18pt footer reservations, and >=6pt labels/title. This is whole-map fit, not miniature-scale printing. GM-key PDF is multipage **portrait Letter on every page**, 36pt margins, Vera 10pt/13pt body leading, 14pt headings, wrapped/splittable tables and page numbers.

PNG dimensions include title/borders/legend: **(width+2)×pixels_per_square by (height+3)×pixels_per_square**. Default 40×40/64 -> 2688×2752; 80×80/64 -> 5248×5312 (27,877,376 pixels). Maximum allocation is **32,000,000 pixels**; 80×80/128 fails `E_IMAGE_SIZE` before allocation. Set the brief's `pixels_per_square` lower; no CLI resolution flag exists.

Both renderers use pinned ReportLab-distributed `Vera.ttf` / `VeraBd.ttf`, not host fallbacks. Verified SHA256s: `c4c45690b345435b2cba52ecabe275f05e49b389b39fe68ad03afbb551288d3d` and `cc037385e4d55bfde89b13e03091ee93bf40c0c52ddd391ff031ab276f13b8e9`. Supported Latin examples: `Élan Ørn Ægir œil naïve — – − • “quoted” ‘name’`. Every rendered string is checked against actual glyph maps. Unsupported characters (e.g. emoji U+1F600) fail `E_GLYPH` explicitly; changed/missing fonts fail `E_FONT`. No silent boxes or substitutions. Supported text that cannot fit print constraints fails `E_RENDER_FIT`. User/source markup renders literally; Markdown line endings normalize locally without changing saved authored JSON.

## Enrichment, Render, and Regenerate

After an explicit edit/artifact request, copy current `dungeon.json` outside the manifest-managed current bundle before editing. Example from the selected checkout, with an authorized `authored.json` copy:

```sh
cp dungeons/ash-vault/dungeon.json authored.json
# Edit the copy's narrative fields, then:
fcw-dungeon validate --dungeon authored.json
fcw-dungeon render --dungeon authored.json
# A separately requested fresh base, optionally with a revised brief:
fcw-dungeon regenerate --from dungeons/ash-vault/dungeon.json --seed 1
```

Preserve IDs, geometry, budgets, mechanics and complete source snapshots unless explicitly requesting a validated mechanical edit. Narrative enrichment can alter descriptions/product-choice features, objective description, and separately identified lore proposals. A proposal is `{"id":"L001","text":"Proposed history.","related_links":[],"status":"proposed"}`. New history can appear in the proposed section of the dungeon key; generation does not approve it as canon or create missing world definitions. Existing supplied facts stay canon; read relevant world documents first. `LINKS.md` is the sole world-link authority, with `world-links` for its edits and approvals. Ordinary `@` mentions remain legal; authored definition headings are rejected. Narrative-only proposals without an artifact-edit request stay in conversation.

Render validates explicit saved data and publishes it **without rerolling**. Old current goes to its numbered revision; current remains above history. Regenerate generates a fresh base from saved or explicitly revised brief; old enrichment survives in the archive and is not attached to new room IDs. Same normalized brief/seed/generator/catalog reproduces base canonical data; seed does not reconstruct authored prose. Manifest records operation, input/parent digests, runtime/font versions, UTC time, revision and eight artifact hashes. It is not self-hashed. No cross-environment byte-identical PNG/PDF promise.

## Publication and Conservative Recovery

Publisher verifies current and complete archives before replacement, holds a permanent Linux advisory flock, stages exports, and journals multi-file promotion. Individual renames are atomic; the whole bundle is a recovery transaction, not one atomic rename. The managed index has one slug-sorted row per dungeon and only replaces its `<!-- fcw-dungeon:catalog:start -->` / `<!-- fcw-dungeon:catalog:end -->` region, preserving outside prose. Different display-name/slug identity collisions, malformed markers, symlinks or unexpected edits are rejected.

Never manually change current manifest-managed files. `E_CURRENT_CHANGED`, `E_HISTORY`, or catalog errors mean stop, preserve bytes, and inspect rather than overwrite drift. A copied author input is the normal edit path. A killed noncommitted transaction restores prior state (or initial absence); verified COMMITTED recovery retains publication. Next publication performs pending recovery under lock. Library callers may use `storage.recover_pending(root)`; there is no CLI recovery flag/subcommand.

Manual changes during interruption produce `E_RECOVERY_USER_CHANGED`; journals, backups and user files are retained. `E_JOURNAL` indicates invalid/unowned recovery metadata; `E_COMMIT_UNCERTAIN` indicates commit/abort durability could not be confirmed. Do not delete recovery data or guess a successful result. Back up affected files and inspect the retained state before resolving conflicts and retrying. Incomplete inert `.fcw-dungeon-txn/bootstrap/` receipts are retained and do not block later publication; unknown active transaction content is conservatively preserved/rejected. Do not unlink the permanent `.fcw-dungeon.lock` inode. Lock contention fails rather than running two publishers concurrently. Git operations and plan archival are separate user actions.

## Development Verification

Local tests/fixtures live under ignored `wd/tests/`; they are deliberately absent from fresh clones and are not runtime dependencies. With those local tests available, run from the checkout root:

```sh
python3 -m venv wd/.venv
wd/.venv/bin/python -m pip install -e '.opencode/skills/dungeon-builder/runtime[test]'
PYTHONPATH=wd wd/.venv/bin/python -m unittest discover -s wd/tests -t wd -v
wd/.venv/bin/python -m pip wheel --no-deps .opencode/skills/dungeon-builder/runtime --wheel-dir wd/wheelhouse
python3 -m venv wd/.wheel-venv
wd/.wheel-venv/bin/python -m pip install 'wd/wheelhouse/fcw_dungeon-0.0.10.0-py3-none-any.whl[test]'
```

Tests use pypdf only for audits; runtime pins are Pillow 12.3.0, ReportLab 5.0.1, jsonschema 4.26.0. Some Markdown export/catalog tests additionally invoke the existing host `markdown-it-py` CommonMark oracle from `/usr/lib/python3/dist-packages` in isolated subprocesses. That oracle is **not** installed by the test extra or used at runtime, so a fresh host without it cannot run those tests as-is. Wheel runtime acceptance should run outside the source tree with `PYTHONPATH` unset, inspect installed imports/resources, and guard socket connections during library generation/rendering. Use an owned temporary checkout for publication tests, never the actual repository's dungeon destination.

Test discovery imports `wd/tests/__init__.py`, which makes inherited local-test paths absolute for children whose cwd changes. This does not affect installed runtime imports.
