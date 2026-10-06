"""Verified offline source catalog, source closure, and 2024 table helpers."""
from copy import deepcopy
from functools import lru_cache
import hashlib
from importlib.resources import files
import json
from pathlib import Path
import unicodedata

from . import model

RARITIES = ('common', 'uncommon', 'rare', 'very-rare', 'legendary')
CONTENT_FILES = {'monsters.json': 'monster', 'spells.json': 'spell',
                 'glossary.json': 'rule', 'equipment.json': 'equipment',
                 'traps.json': 'trap', 'treasure.json': 'item'}
MONSTERS = frozenset('skeleton zombie shadow ghoul specter ghast mummy wight ghost wraith '
                     'vampire-spawn vampire iron-golem bandit guard bandit-captain guard-captain '
                     'hobgoblin-warrior hobgoblin-captain gladiator assassin stone-giant fire-giant '
                     'balor giant-rat giant-bat dire-wolf giant-spider ankheg ogre ettin troll '
                     'hydra behir purple-worm troll-limb'.split())
SPELLS = frozenset('arcane-lock charm-person command etherealness greater-restoration remove-curse spider-climb'.split())
ITEMS = frozenset('potion-of-healing potion-of-climbing goggles-of-night cloak-of-protection '
                 'potion-of-healing-superior ring-of-protection potion-of-healing-supreme '
                 'belt-of-giant-strength-fire belt-of-giant-strength-storm ring-of-invisibility'.split())
TRAPS = frozenset('falling-net hidden-pit collapsing-roof poisoned-darts'.split())


class CatalogIssue(ValueError):
    def __init__(self, code, path, context):
        self.code, self.path, self.context = code, path, context
        super().__init__(f'{code} {path}: {context}')


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _hash(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _validate(value, definition, path):
    validator = model.schema_validator('catalog').evolve(
        schema={'$ref': f'urn:fcw:catalog:v1#/$defs/{definition}'})
    error = next(validator.iter_errors(value), None)
    if error:
        suffix = ''.join(f'[{part}]' if type(part) is int else f'.{part}' for part in error.absolute_path)
        raise CatalogIssue('E_SCHEMA', path + suffix, error.message)


def _resources(root):
    result = {}
    def visit(directory, prefix=''):
        for child in sorted(directory.iterdir(), key=lambda p: p.name):
            name = prefix + child.name
            if child.is_dir():
                visit(child, name + '/')
            elif child.name.endswith(('.json', '.txt')):
                raw = child.read_bytes()
                if child.name.endswith('.json'):
                    try:
                        result[name] = model.parse_json(raw)
                    except model.ValidationIssue as error:
                        raise CatalogIssue(error.code, name + error.path, str(error)) from None
                else:
                    result[name] = unicodedata.normalize('NFC', raw.decode('utf-8').replace('\r\n','\n').replace('\r','\n'))
    try:
        visit(root)
    except (OSError, UnicodeError) as error:
        raise CatalogIssue('E_RESOURCE', '$', str(error)) from None
    return result


def resource_digest(resources):
    """Hash normalized resource-name/value map; omit only manifest.digest."""
    normalized = model.parse_json(_canonical(resources))
    if 'catalog-manifest.json' in normalized:
        normalized['catalog-manifest.json'].pop('digest', None)
    return _hash(normalized)


def load_catalog(resource_root=None):
    """Load fresh verified catalog. Optional filesystem path supports offline audits."""
    root = files('fcw_dungeon').joinpath('data') if resource_root is None else Path(resource_root)
    resources = _resources(root)
    required = set(CONTENT_FILES) | {'themes.json','rules.json','catalog-manifest.json','attribution.txt'}
    required |= {f'schemas/{name}.schema.json' for name in ('common','brief','catalog','dungeon','manifest')}
    missing = required - resources.keys()
    if missing:
        raise CatalogIssue('E_RESOURCE', '$', f'missing resources: {sorted(missing)}')
    manifest, themes, tables = (resources[name] for name in ('catalog-manifest.json','themes.json','rules.json'))
    for value, definition, path in ((manifest,'catalog_manifest','catalog-manifest.json'),
                                    (themes,'themes_file','themes.json'),(tables,'rules_file','rules.json')):
        _validate(value, definition, path)
    entries = {}
    for filename, kind in CONTENT_FILES.items():
        content = resources[filename]
        _validate(content, 'content_file', filename)
        for i, entry in enumerate(content['entries']):
            identifier = entry['id']
            path = f'{filename}.entries[{i}]'
            if identifier in entries:
                raise CatalogIssue('E_DUPLICATE', path, identifier)
            if entry['kind'] != kind or not identifier.startswith(kind + ':'):
                raise CatalogIssue('E_KIND', path, f'{identifier} must have kind {kind}')
            if hashlib.sha256(entry['rules_text'].encode('utf-8')).hexdigest() != entry['source']['text_sha256']:
                raise CatalogIssue('E_TEXT_DIGEST', path + '.source.text_sha256', identifier)
            if any(page > 364 for page in entry['source']['pages']):
                raise CatalogIssue('E_SOURCE', path + '.source.pages', 'page exceeds pinned SRD')
            entries[identifier] = entry
    for kind, expected in (('monster',MONSTERS),('spell',SPELLS),('item',ITEMS),('trap',TRAPS)):
        actual = {e['id'].split(':',1)[1] for e in entries.values() if e['kind']==kind}
        if actual != expected:
            raise CatalogIssue('E_INVENTORY', kind, f'missing {sorted(expected-actual)}; unexpected {sorted(actual-expected)}')
    if entries.keys() != manifest['entries'].keys():
        raise CatalogIssue('E_INVENTORY', 'catalog-manifest.json.entries', 'inventory does not match source records')
    packs = themes['packs']
    memberships = {identifier: [] for identifier in entries}
    for theme, pack in packs.items():
        for field, kind in (('monsters','monster'),('traps','trap'),('items','item')):
            for identifier in pack[field]:
                if identifier not in entries or entries[identifier]['kind'] != kind or identifier=='monster:troll-limb':
                    raise CatalogIssue('E_MEMBERSHIP', f'themes.json.packs.{theme}.{field}', identifier)
                memberships[identifier].append(theme)
    catalog = {'catalog_version': manifest['catalog_version'], 'digest': manifest['digest'],
               'entries': entries, 'packs': packs, 'tables': tables, 'attribution': resources['attribution.txt']}
    dependency_closure(entries, catalog=catalog)
    for identifier, entry in entries.items():
        record = manifest['entries'][identifier]
        if sorted(record['packs']) != sorted(memberships[identifier]):
            raise CatalogIssue('E_MEMBERSHIP', f'catalog-manifest.json.entries.{identifier}.packs', 'membership mismatch')
        if _hash(entry) != record['digest']:
            raise CatalogIssue('E_RECORD_DIGEST', f'catalog-manifest.json.entries.{identifier}.digest', identifier)
    if resource_digest(resources) != manifest['digest']:
        raise CatalogIssue('E_CATALOG_DIGEST', 'catalog-manifest.json.digest', 'resource digest mismatch')
    _validate(catalog, 'catalog', '$')
    return catalog


def dependency_closure(ids, *, catalog=None):
    """Sorted unique IDs, including roots; legitimate source cycles visit once."""
    if catalog is None:
        catalog = load_catalog()
    entries, visited = catalog['entries'], set()
    pending = [(identifier, '<selection>') for identifier in ids]
    while pending:
        identifier, parent = pending.pop()
        if identifier in visited:
            continue
        if identifier not in entries:
            raise CatalogIssue('E_DEPENDENCY', f'entries.{parent}.dependencies', f'missing {identifier} required by {parent}')
        visited.add(identifier)
        pending.extend((dependency,identifier) for dependency in entries[identifier]['dependencies'])
    return sorted(visited)


def snapshot_sources(ids, *, catalog=None):
    """Fresh ID-to-SourceEntry map for full selected dependency closure."""
    if catalog is None:
        catalog = load_catalog()
    return {identifier: deepcopy(catalog['entries'][identifier])
            for identifier in dependency_closure(ids,catalog=catalog)}


@lru_cache(maxsize=1)
def _tables():
    return load_catalog()['tables']


def _roster(roster):
    if type(roster) is not list or not 2 <= len(roster) <= 6:
        raise ValueError('roster must contain 2–6 levels')
    for level in roster:
        tier_for_level(level)


def encounter_budget(roster, difficulty, *, catalog=None):
    """Sum exact per-level XP rows; mixed levels are generator extension."""
    _roster(roster)
    if difficulty not in ('low','moderate','high'):
        raise ValueError('difficulty must be low, moderate, or high')
    tables = _tables() if catalog is None else catalog['tables']
    return sum(tables['xp_budgets'][str(level)][difficulty] for level in roster)


def tier_for_level(level):
    if type(level) is not int or not 1 <= level <= 20:
        raise ValueError('level must be integer 1–20')
    return 1 if level <= 4 else 2 if level <= 10 else 3 if level <= 16 else 4


def party_tier(roster):
    """Lowest-member tier; mixed-level trap/treasure choice is product rule."""
    _roster(roster)
    return tier_for_level(min(roster))


def rarity_for_roll(tier, roll, *, catalog=None):
    """Map source d100 roll (1–100) to actual rarity ID."""
    if type(tier) is not int or not 1 <= tier <= 4 or type(roll) is not int or not 1 <= roll <= 100:
        raise ValueError('tier must be integer 1–4 and roll integer 1–100')
    tables = _tables() if catalog is None else catalog['tables']
    weights = tables['rarity_weights'][str(tier)]
    if sum(weights) != 100:
        raise CatalogIssue('E_TABLE', f'tables.rarity_weights.{tier}', 'weights must total 100')
    for rarity, weight in zip(RARITIES,weights):
        roll -= weight
        if roll <= 0:
            return rarity


def draw_rarity(roster, stream, *, catalog=None):
    """Draw with existing rng.Stream; never relabel an item's source rarity."""
    tier = party_tier(roster)
    return rarity_for_roll(tier,stream.randbelow(100)+1,catalog=catalog)
