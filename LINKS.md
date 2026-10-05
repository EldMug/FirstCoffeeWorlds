# Links

Use links to connect people, places, events, factions, and important items across world documents.

## Definitions

A link is defined only when it appears in a Markdown heading.

Examples:

```md
## @Lara
## @Town
## @(assassination of the king)
## @(Crown of Ash)
```

## Mentions

Mentions can appear anywhere in prose.

Explicit mentions use `@`:

- `@Lara`
- `@Town`
- `@(assassination of the king)`

Implicit mentions are proper nouns:

- `Lara`
- `Town`
- `Crown of Ash`
- `Blackstone Keep`

## Link Scan

When asked to scan links:

1. Scan all Markdown files.
2. Treat headings with `@Name` or `@(Name)` as definitions.
3. Treat `@Name`, `@(Name)`, and likely proper nouns in prose as mentions.
4. Be aggressive first.
5. Report mentions that do not have definitions.
6. Report empty definition entries.
7. Propose edits before applying them.
8. Ask questions before creating missing definitions.
9. Apply only approved edits.
10. Do not invent lore.

## Empty Entry Scan

A definition is empty if:

- the heading has no text before the next heading
- the heading only has blank lines
- the heading only has placeholders like `TODO`, `TBD`, or `fill this in later`
- the heading only has metadata with no prose

Report empty entries separately.

## Learning Rules

When the user accepts a candidate, treat it as a real link going forward.

When the user rejects a candidate, add it to ignored terms.

## Scan Output

Use this shape when reporting a scan:

```md
## Link Scan

Defined:
- `@Lara`

Missing candidates:
- `Town` probably place
- `Crown of Ash` probably item

Empty entries:
- `@(Crown of Ash)` in the file where it is defined

Proposed edits:
- Change `Town` to `@Town`.
- Change `Crown of Ash` to `@(Crown of Ash)`.

Questions:
1. Is `Town` a place?
2. Is `Crown of Ash` an important item?
3. Apply these edits?
```

## Known Links

## Ignored Terms
