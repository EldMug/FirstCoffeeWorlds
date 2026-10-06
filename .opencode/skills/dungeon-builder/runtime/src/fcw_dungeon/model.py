"""JSON wire contracts and normalization."""

import json
import math
import re
import unicodedata
from datetime import datetime
from functools import lru_cache
from importlib.resources import files

from jsonschema import Draft202012Validator, FormatChecker, validators
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource
from referencing.jsonschema import DRAFT202012

BRIEF_MAX_BYTES = 1024 * 1024
DOCUMENT_MAX_BYTES = 16 * 1024 * 1024


class ValidationIssue(ValueError):
    def __init__(self, code, path, message):
        self.code = code
        self.path = path
        super().__init__(f"{code} {path}: {message}")


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationIssue("E_JSON_DUPLICATE", "$", f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValidationIssue("E_JSON_NUMBER", "$", f"nonfinite number {value}")


def _fresh(value, path="$", depth=0):
    """Copy JSON types only; normalize text without rewriting object keys."""
    if depth > 128:
        raise ValidationIssue("E_JSON_DEPTH", path, "nesting exceeds 128 levels")
    if type(value) is dict:
        result = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ValidationIssue("E_JSON_TYPE", path, "object keys must be strings")
            try:
                key.encode("utf-8")
            except UnicodeError:
                raise ValidationIssue("E_UTF8", path, "invalid Unicode object key") from None
            result[key] = _fresh(item, f"{path}.{key}", depth + 1)
        return result
    if type(value) is list:
        return [_fresh(item, f"{path}[{i}]", depth + 1) for i, item in enumerate(value)]
    if type(value) is str:
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise ValidationIssue("E_UTF8", path, "invalid Unicode text") from None
        return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ValidationIssue("E_JSON_TYPE", path, "expected finite JSON value")


def parse_json(data, *, max_bytes=DOCUMENT_MAX_BYTES):
    """Bounded strict UTF-8 JSON object parsing; no I/O or schema validation."""
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValidationIssue("E_INPUT_LIMIT", "$", "limit must be a positive integer")
    if type(data) not in (str, bytes):
        raise ValidationIssue("E_JSON_TYPE", "$", "expected UTF-8 bytes or text")
    try:
        size = len(data) if type(data) is bytes else len(data.encode("utf-8"))
        if size > max_bytes:
            raise ValidationIssue("E_INPUT_SIZE", "$", f"input exceeds {max_bytes} bytes")
        text = data.decode("utf-8") if type(data) is bytes else data
        value = json.loads(text, object_pairs_hook=_object_pairs, parse_constant=_reject_constant)
    except UnicodeError:
        raise ValidationIssue("E_UTF8", "$", "input must be valid UTF-8") from None
    except json.JSONDecodeError as error:
        raise ValidationIssue("E_JSON_SYNTAX", "$", f"invalid JSON at line {error.lineno}, column {error.colno}") from None
    except RecursionError:
        raise ValidationIssue("E_JSON_DEPTH", "$", "JSON nesting is too deep") from None
    except ValueError as error:
        if isinstance(error, ValidationIssue):
            raise
        raise ValidationIssue("E_JSON_NUMBER", "$", "invalid JSON number") from None
    if type(value) is not dict:
        raise ValidationIssue("E_JSON_TYPE", "$", "top-level value must be an object")
    return _fresh(value)


def parse_brief(data):
    return parse_json(data, max_bytes=BRIEF_MAX_BYTES)


def parse_document(data):
    return parse_json(data, max_bytes=DOCUMENT_MAX_BYTES)


def _local_only(uri):
    raise NoSuchResource(ref=uri)


_StrictValidator = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine("integer", lambda checker, value: type(value) is int),
)


@lru_cache(maxsize=1)
def _schemas():
    resources = files("fcw_dungeon").joinpath("data/schemas")
    schemas = {name: json.loads(resources.joinpath(f"{name}.schema.json").read_text(encoding="utf-8"))
               for name in ("common", "brief", "dungeon", "catalog", "manifest")}
    # Keep the dialect in files, but prevent jsonschema.evolve from replacing
    # our strict integer checker when following a whole-resource reference.
    registry = Registry(retrieve=_local_only).with_resources(
        (schema["$id"], Resource.from_contents(
            {key: value for key, value in schema.items() if key != "$schema"},
            default_specification=DRAFT202012)) for schema in schemas.values())
    return schemas, registry


def _is_utc_datetime(value):
    if not isinstance(value, str):
        return True
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]+)?Z", value):
        return False
    datetime.fromisoformat(value)
    return True


def _is_source_uri(value):
    if not isinstance(value, str):
        return True
    # Source schemas pin an HTTPS DNS origin. Validate its URI components using
    # RFC 3986 pchar syntax; raw spaces, controls and malformed escapes fail.
    pchar = r"(?:[-A-Za-z0-9._~!$&'()*+,;=:@]|%[0-9A-Fa-f]{2})"
    query = rf"(?:{pchar}|[/?])*"
    return re.fullmatch(
        rf"https?://[A-Za-z0-9.-]+(?::[0-9]+)?(?:/{pchar}*)*(?:\?{query})?(?:#{query})?", value) is not None


def _format_checker():
    # Register only our schema formats; never depend on optional format extras.
    checker = FormatChecker(formats=())
    checker.checks("date-time", raises=ValueError)(_is_utc_datetime)
    checker.checks("uri")(_is_source_uri)
    return checker


def schema_validator(name):
    """Reusable strict validator, with exclusively packaged references."""
    schemas, registry = _schemas()
    return _StrictValidator(schemas[name], registry=registry, format_checker=_format_checker())


def validate_schema(value, name):
    value = _fresh(value)
    error = next(schema_validator(name).iter_errors(value), None)
    if error is not None:
        path = "$" + "".join(f"[{part}]" if type(part) is int else f".{part}" for part in error.absolute_path)
        raise ValidationIssue("E_SCHEMA", path, error.message)


@lru_cache(maxsize=1)
def _rules():
    return json.loads(files("fcw_dungeon").joinpath("data/rules.json").read_text(encoding="utf-8"))


def normalize_brief(raw):
    """Return a fresh normalized Brief v1; preserve requested automatic modes."""
    brief = _fresh(raw)
    if type(brief) is not dict:
        raise ValidationIssue("E_SCHEMA", "$", "brief must be an object")
    defaults = {
        "schema_version": 1, "layout": "constructed",
        "purpose": "Explore the dungeon and discover its objective.", "tone": "mysterious",
        "room_count": None, "combat_percent": 40, "trap_percent": 10,
        "climax": False, "gate_count": 1, "secret_rooms": 0, "trap_severity": "mixed",
        "pixels_per_square": 64, "seed": None,
    }
    for key, default in defaults.items():
        brief.setdefault(key, default)
    brief.setdefault("theme", "cave" if brief["layout"] == "cave" else "crypt")
    for key, nested in {
        "grid": {"width": 40, "height": 40, "feet_per_square": 5},
        "treasure": {"placement": "balanced", "magic_items": 2, "coin_gp": None,
                     "rarity_override": None, "custom": []},
        "world_context": {"references": [], "facts": []},
    }.items():
        brief.setdefault(key, {})
        if type(brief[key]) is dict:
            for field, default in nested.items():
                brief[key].setdefault(field, default)
    if "slug" not in brief:
        name = brief.get("name")
        if type(name) is not str:
            raise ValidationIssue("E_SCHEMA", "$.name", "name is required and must be text")
        ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
        brief["slug"] = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")[:64].rstrip("-")
    validate_schema(brief, "brief")
    if not brief["name"].strip():
        raise ValidationIssue("E_SCHEMA", "$.name", "name must not be blank")
    treasure = brief["treasure"]
    if treasure["coin_gp"] is None:
        lowest = min(brief["party_levels"])
        tier = 1 if lowest <= 4 else 2 if lowest <= 10 else 3 if lowest <= 16 else 4
        treasure["coin_gp"] = _rules()["coin_allowances"][str(tier)]
    if brief["combat_percent"] + brief["trap_percent"] > 100:
        raise ValidationIssue("E_BRIEF_ROLES", "$.trap_percent", "combat plus traps exceeds 100 percent")
    if brief["secret_rooms"] > brief["gate_count"]:
        raise ValidationIssue("E_BRIEF_GATES", "$.secret_rooms", "secret rooms exceed gate slots")
    combat = role_counts(room_target(brief), brief)["combat"]
    if brief["climax"] and not combat:
        raise ValidationIssue("E_BRIEF_CLIMAX", "$.climax", "climax requires a rounded combat room")
    if treasure["placement"] == "guarded" and (treasure["coin_gp"] or treasure["magic_items"]) and not combat:
        raise ValidationIssue("E_BRIEF_TREASURE", "$.treasure.placement", "guarded rewards require a rounded combat room")
    if treasure["placement"] != "custom" and treasure["custom"]:
        raise ValidationIssue("E_BRIEF_TREASURE", "$.treasure.custom", "allocations require custom placement")
    return brief


def room_target(brief):
    if brief["room_count"] is not None:
        return brief["room_count"]
    area = brief["grid"]["width"] * brief["grid"]["height"]
    return max(4, max((n for n in range(1, 49) if (10*n - 5)**2 <= 9*area), default=0))


def role_counts(room_count, brief):
    combat = (room_count * brief["combat_percent"] + 50) // 100
    trap = min((room_count * brief["trap_percent"] + 50) // 100, room_count - combat)
    return {"combat": combat, "trap": trap, "exploration": room_count - combat - trap}


def validate_dungeon(dungeon):
    """Validate saved canonical data against geometry, progression and trusted SRD.

    No seed replay is used to validate authored room descriptions. Source
    mechanics, physical placements and frozen brief constraints stay mandatory.
    """
    validate_schema(dungeon, "dungeon")
    dungeon = _fresh(dungeon)
    brief = normalize_brief(dungeon['brief'])
    _require(brief == dungeon['brief'], '$.brief', 'canonical brief must already be normalized')
    _require(dungeon['seed']==brief['seed'], '$.seed', 'seed differs from effective brief')
    catalog = _trusted_catalog()
    _require(dungeon['catalog_digest']==catalog['digest'], '$.catalog_digest', 'unsupported trusted catalog digest', 'E_SOURCE')
    geometry, contents = dungeon['geometry'], dungeon['contents']
    rooms, floor, distances = _validate_geometry(geometry,brief)
    roots, hazards, required = _validate_contents(contents,geometry,brief,catalog,rooms,floor,distances)
    from . import progression, rules
    progression.validate_progression(geometry,avoid_cells=[list(p) for p in hazards],required_points=[list(p) for p in required])
    expected_ids = set(rules.dependency_closure(roots,catalog=catalog))
    _require(set(dungeon['sources'])==expected_ids,'$.sources','snapshot must be exact complete source dependency closure','E_SOURCE')
    for identifier,entry in dungeon['sources'].items():
        _require(entry==catalog['entries'][identifier],f'$.sources.{identifier}',
                 'snapshot differs from complete trusted source record/text/hash','E_SOURCE')
    lore_ids = set()
    for i,proposal in enumerate(dungeon['lore_proposals']):
        path = f'$.lore_proposals[{i}]'
        _require(proposal['id'] not in lore_ids,path+'.id','duplicate lore proposal identifier')
        lore_ids.add(proposal['id'])
        _require(proposal['status']=='proposed',path+'.status','lore must remain proposed')
        _no_definitions(proposal['text'],path+'.text')


@lru_cache(maxsize=1)
def _trusted_catalog():
    from .rules import load_catalog
    return load_catalog()


def _require(condition, path, message, code='E_SEMANTIC'):
    if not condition:
        raise ValidationIssue(code,path,message)


def _cell_set(points, floor, path):
    cells = set(map(tuple,points))
    _require(len(cells)==len(points),path,'duplicate points')
    _require(cells <= floor,path,'points outside actual floor/grid')
    return cells


def _ids(values, field, path, prefix=None):
    result = {v[field]:v for v in values}
    _require(len(result)==len(values),path,'duplicate identifiers','E_REFERENCE')
    if prefix:
        _require([v[field] for v in values]==[f'{prefix}{i:03d}' for i in range(1,len(values)+1)],path,
                 'identifiers/order must be sequential','E_REFERENCE')
    return result


def _no_definitions(text, path, *, heading=False):
    """LINKS.md: body mentions are legal; Markdown heading links define names.

    Room titles become headings when rendered. Body text is checked for ATX
    and Setext headings, excluding fenced/indented code examples.
    """
    link = re.compile(r'(?<![\\\w])@(?:\([^\n)]+\)|\w[\w-]*)')
    def check(value):
        _require(link.search(value) is None,path,
                 'generated/proposed prose cannot create @definition headings','E_LORE')
    if heading:
        check(text)
        return
    quote = re.compile(r' {0,3}> ?')
    item = re.compile(r'( {0,3})([-+*]|[0-9]{1,9}[.)])( +|$)')
    atx_heading = re.compile(r' {0,3}#{1,6}(?: +|$)(.*)')
    thematic = re.compile(r' {0,3}(?:(?:\* *){3,}|(?:- *){3,}|(?:_ *){3,})')
    fence_marker = re.compile(r' {0,3}(`{3,}|~{3,})(.*)')
    def opener(line):
        marker = fence_marker.fullmatch(line)
        if marker and (marker[1][0]!='`' or '`' not in marker[2]):
            return marker[1]
        return None
    def starts_block(line):
        return (atx_heading.fullmatch(line) or thematic.fullmatch(line) or
                quote.match(line) or item.match(line) or opener(line))

    # Container identities belong to individual quote blocks/list items. A
    # same-looking sibling is a new parent, not continuation of its fence or
    # Setext paragraph. List widths are relative to the enclosing container.
    containers, fence, paragraph, paragraph_parent = [], None, [], ()
    for line_number, raw in enumerate(text.splitlines()):
        line, pos, matched = raw.expandtabs(4), 0, []
        for container in containers:
            kind, width, _ = container
            marker = quote.match(line,pos) if kind=='quote' else None
            if marker:
                pos = marker.end()
            elif kind=='list' and (not line[pos:].strip() or line[pos:pos+width]==' '*width):
                pos = min(len(line),pos+width)
            else:
                break
            matched.append(container)

        lazy = (len(matched)<len(containers) and paragraph and fence is None and
                line[pos:].strip() and not starts_block(line[pos:]))
        if lazy:
            # CommonMark lazy continuation is paragraph text, never a Setext
            # underline outside its parent's marker/indentation.
            matched = containers[:]
        elif fence and tuple(matched)!=fence[1]:
            fence = None  # An unclosed fence auto-closes at its parent boundary.

        if fence:
            markup = fence[0]
            if re.fullmatch(r' {0,3}'+re.escape(markup[0])+r'{'+str(len(markup))+r',} *',line[pos:]):
                fence = None
            containers = matched
            continue  # Code contents cannot introduce new containers/headings.

        if not lazy:
            while not thematic.fullmatch(line,pos):
                marker = quote.match(line,pos)
                if marker:
                    matched.append(('quote',0,line_number))
                    pos = marker.end()
                    continue
                marker = item.match(line,pos)
                if not marker:
                    break
                padding = len(marker[3])
                padding = 1 if padding>4 or not line[marker.end():] else padding
                width = len(marker[1])+len(marker[2])+padding
                matched.append(('list',width,line_number))
                pos += width

        containers, line = matched, line[pos:]
        parent = tuple(containers)
        if parent!=paragraph_parent:
            paragraph = []
        paragraph_parent = parent
        marker = opener(line)
        if marker:
            fence, paragraph = (marker,parent), []
            continue
        if not line.strip():
            paragraph = []
            continue
        if line.startswith('    ') and not paragraph:
            continue
        atx = atx_heading.fullmatch(line) if not lazy else None
        if atx:
            check(atx[1])
            paragraph = []
        elif paragraph and not lazy and re.fullmatch(r' {0,3}(?:=+|-+) *',line):
            check('\n'.join(paragraph))
            paragraph = []
        elif thematic.fullmatch(line):
            paragraph = []
        else:
            paragraph.append(line)


def _validate_geometry(geometry, brief):
    from .layout import cell_distances, has_route_loop
    from . import progression
    width,height = geometry['width'],geometry['height']
    _require((width,height)==(brief['grid']['width'],brief['grid']['height']), '$.geometry.width','dimensions differ from brief')
    _require(len(geometry['rows'])==height and all(len(row)==width for row in geometry['rows']),
             '$.geometry.rows','row dimensions differ from grid')
    floor = progression.floor_cells(geometry)
    _require(all(0<x<width-1 and 0<y<height-1 for x,y in floor),'$.geometry.rows','outer border must be solid')
    rooms = _ids(geometry['rooms'],'id','$.geometry.rooms','R')
    _require(len(rooms)==geometry['actual_room_count'],'$.geometry.actual_room_count','actual room count differs from rooms')
    target = room_target(brief)
    _require(len(rooms)==target if brief['room_count'] is not None else 4<=len(rooms)<=target,
             '$.geometry.actual_room_count','actual count violates explicit/auto request')
    anchors = [r['anchor'] for r in geometry['rooms']]
    _require(anchors==sorted(anchors,key=lambda p:(p[1],p[0])),'$.geometry.rooms','rooms must follow anchor (y,x) order')
    all_rooms, envelopes, room_sets = set(),[],{}
    for i,room in enumerate(geometry['rooms']):
        path = f'$.geometry.rooms[{i}]'
        cells = _cell_set(room['cells'],floor,path+'.cells')
        anchor = tuple(room['anchor'])
        _require(anchor in cells,path+'.anchor','anchor must belong to chamber')
        _require(set(cell_distances(cells,anchor))==cells,path+'.cells','chamber must be cardinal-connected')
        _require(not cells & all_rooms,path+'.cells','chambers overlap')
        all_rooms |= cells
        room_sets[room['id']] = cells
        xs,ys = zip(*cells)
        w,h = max(xs)-min(xs)+1,max(ys)-min(ys)+1
        envelope = {(x,y) for y in range(min(ys),max(ys)+1) for x in range(min(xs),max(xs)+1)}
        _require(all(not envelope & (old | {(x+dx,y+dy) for x,y in old for dx,dy in ((0,-1),(-1,0),(1,0),(0,1))})
                     for old in envelopes),path+'.cells','room envelopes must have separating wall/rock')
        envelopes.append(envelope)
        if brief['layout']=='constructed':
            _require(3<=w<=16 and 3<=h<=16 and cells==envelope,path+'.cells','constructed room must be 3–16 rectangle')
        else:
            core = any({(x+dx,y+dy) for dx in range(3) for dy in range(3)} <= cells for x,y in cells)
            _require(5<=w<=16 and 5<=h<=16 and cells!=envelope and core,path+'.cells','cave needs irregular envelope and clear 3x3 core')
        _require(room['required'] != room['secret'],path+'.required','nonsecret rooms required; secret rooms optional')
    _require(sum(r['secret'] for r in rooms.values())==brief['secret_rooms'],'$.geometry.rooms','secret count differs from brief')
    _require(not rooms['R001']['secret'] and tuple(geometry['entrance']) in room_sets['R001'],
             '$.geometry.entrance','entrance must lie in visible R001')
    objective = rooms.get(geometry['objective_room'])
    _require(objective is not None and objective['required'] and not objective['secret'],
             '$.geometry.objective_room','objective must reference nonsecret required room')
    passages = _ids(geometry['passages'],'id','$.geometry.passages','P')
    interiors, union, pairs = [],all_rooms.copy(),set()
    secret_union = set().union(*(room_sets[r['id']] for r in rooms.values() if r['secret']))
    for i,passage in enumerate(geometry['passages']):
        path = f'$.geometry.passages[{i}]'
        a,b = passage['from_room'],passage['to_room']
        _require(a in rooms and b in rooms and a!=b,path,'passage endpoints must resolve distinctly','E_REFERENCE')
        pair = tuple(sorted((a,b)))
        _require(pair not in pairs,path,'duplicate endpoint connection')
        pairs.add(pair)
        cells = _cell_set(passage['cells'],floor,path+'.cells')
        line = passage['centerline']
        _cell_set(line,floor,path+'.centerline')
        _require(len(line)>1 and all(abs(x-u)+abs(y-v)==1 for (x,y),(u,v) in zip(line,line[1:])),
                 path+'.centerline','centerline must have cardinal consecutive steps')
        expanded = {(x+dx,y+dy) for x,y in line for dx in range(passage['width']) for dy in range(passage['width'])}
        _require(cells==expanded,path+'.cells','passage cells differ from exact width expansion')
        _require(tuple(line[0]) in room_sets[a] and tuple(line[-1]) in room_sets[b],path+'.centerline','centerline must join declared endpoints')
        for room_id, envelope in zip(rooms,envelopes):
            if room_id not in (a,b):
                moat = envelope | {(x+dx,y+dy) for x,y in envelope for dx,dy in ((0,-1),(-1,0),(1,0),(0,1))}
                _require(not cells & moat,path+'.cells','unplanned passage/nonendpoint chamber contact')
        interior = cells-all_rooms
        for previous in interiors:
            moat = previous | {(x+dx,y+dy) for x,y in previous for dx,dy in ((0,-1),(-1,0),(1,0),(0,1))}
            _require(not interior & moat,path+'.cells','passage interiors cross or touch outside endpoints')
        interiors.append(interior)
        _require(passage['secret']==(rooms[a]['secret'] or rooms[b]['secret']),path+'.secret','secret passage flag mismatches endpoints')
        if passage['secret']:
            secret_union |= cells
        union |= cells
    _require(union==floor,'$.geometry.rows','actual floor must equal chamber/passage cell union')
    distances = cell_distances(floor,geometry['entrance'])
    _require(set(distances)==floor,'$.geometry.rows','actual floor is disconnected')
    _require(has_route_loop(geometry['rooms'],geometry['passages']),'$.geometry.passages','no physical nonsecret route cycle')
    for room in rooms.values():
        if room['secret']:
            _require(sum(room['id'] in (p['from_room'],p['to_room']) for p in passages.values())==1,
                     '$.geometry.passages','secret room must have one exclusive leaf passage')
    visible_rooms = set().union(*(room_sets[r['id']] for r in rooms.values() if not r['secret']))
    mask = _cell_set(geometry['secret_cells'],floor,'$.geometry.secret_cells')
    _require(mask==secret_union-visible_rooms,'$.geometry.secret_cells','exclusive secret mask must exclude visible endpoint chamber cells')
    _require(set(cell_distances(floor-mask,geometry['entrance']))==floor-mask,
             '$.geometry.secret_cells','secret mask disconnects visible floor')
    doors = _ids(geometry['doors'],'id','$.geometry.doors','D')
    owned = set()
    for i,door in enumerate(geometry['doors']):
        _cell_set(door['cells'],floor,f'$.geometry.doors[{i}].cells')
        passage = progression.door_passage(geometry,door,f'$.geometry.doors[{i}].cells')
        _require(passage['id'] not in owned,f'$.geometry.doors[{i}]','duplicate door on passage')
        owned.add(passage['id'])
    _require(owned==set(passages),'$.geometry.doors','every passage needs its complete ordinary/gated span')
    _ids(geometry['gates'],'id','$.geometry.gates','G')
    _require(len(geometry['gates'])==brief['gate_count'],'$.geometry.gates','gate count differs from brief')
    return rooms,floor,distances


def _validate_contents(contents, geometry, brief, catalog, rooms, floor, distances):
    from collections import Counter
    from . import content, progression, rules
    entries = _ids(contents['rooms'],'room_id','$.contents.rooms')
    _require(set(entries)==set(rooms),'$.contents.rooms','one content entry required per actual room','E_REFERENCE')
    objective = tuple(contents['objective_point'])
    _require(objective in set(map(tuple,rooms[geometry['objective_room']]['cells'])),
             '$.contents.objective_point','objective point must belong to objective chamber')
    _require(contents['objective_description'].strip(),'$.contents.objective_description','objective description must be nonblank')
    _no_definitions(contents['objective_description'],'$.contents.objective_description')
    requested = dict(role_counts(room_target(brief),brief),item=brief['treasure']['magic_items'],coin=brief['treasure']['coin_gp'])
    actual = dict(role_counts(len(rooms),brief),item=brief['treasure']['magic_items'],coin=brief['treasure']['coin_gp'])
    _require(contents['requested_counts']==requested,'$.contents.requested_counts','requested count targets differ from original auto/explicit brief')
    _require(contents['actual_counts']==actual,'$.contents.actual_counts','actual targets differ from room/reward request')
    _require(Counter(r['role'] for r in entries.values())=={k:v for k,v in role_counts(len(rooms),brief).items() if v},
             '$.contents.rooms','role counts do not match half-up disjoint targets')
    roots, hazards, required, instances = set(),set(),{objective,tuple(geometry['entrance'])},set()
    control_points = {tuple(g['prerequisite_point']) for g in geometry['gates']}
    high_rooms = []
    for i,room_content in enumerate(contents['rooms']):
        path = f'$.contents.rooms[{i}]'
        room = rooms[room_content['room_id']]
        cells = set(map(tuple,room['cells']))
        mouths = set(map(tuple,progression.room_entries(geometry,room)))
        required |= mouths
        for key in ('title','description'):
            _require(room_content[key].strip(),path+'.'+key,'room text must be nonblank')
            _no_definitions(room_content[key],path+'.'+key,heading=key=='title')
        encounter, trap = room_content['encounter'],room_content['trap']
        _require((encounter is not None)==(room_content['role']=='combat') and (trap is not None)==(room_content['role']=='trap'),
                 path,'role payloads must be nonnull and disjoint')
        if encounter:
            for j,caution in enumerate(encounter['cautions']):
                _no_definitions(caution,path+f'.encounter.cautions[{j}]')
            difficulty = encounter['target_difficulty']
            if difficulty=='high':
                high_rooms.append(room['id'])
            budget = rules.encounter_budget(brief['party_levels'],difficulty,catalog=catalog)
            _require(encounter['budget']==budget,path+'.encounter.budget','incorrect exact roster budget')
            reserve = _cell_set(encounter['party_entry_cells'],cells,path+'.encounter.party_entry_cells')
            _require(len(reserve)==len(brief['party_levels']),path+'.encounter.party_entry_cells','need one reserved square per PC')
            expected_reserve = set(content._entry_reserve(cells,mouths,len(brief['party_levels'])))
            _require(reserve==expected_reserve,path+'.encounter.party_entry_cells','party reserve must lie near chamber entries')
            occupied, xp, definitions, cautions = reserve | mouths,0,set(),set()
            for j,actor in enumerate(encounter['monsters']):
                actor_path = path+f'.encounter.monsters[{j}]'
                _require(actor['instance_id'] not in instances,actor_path+'.instance_id','instance IDs must be globally unique','E_REFERENCE')
                instances.add(actor['instance_id'])
                sid = actor['catalog_id']
                _require(sid in catalog['packs'][brief['theme']]['monsters'],actor_path+'.catalog_id','monster outside selected source pack','E_SOURCE')
                source = catalog['entries'][sid]
                _require(source['metadata']['variant']=='base',actor_path+'.catalog_id','base variants only','E_SOURCE')
                size = actor['selected_size']
                _require(size in source['metadata']['sizes'] and size in content._SIDES,actor_path+'.selected_size','not an original supported source size')
                footprint = _cell_set(actor['cells'],cells,actor_path+'.cells')
                side = content._SIDES[size]
                x,y = min(p[0] for p in footprint),min(p[1] for p in footprint)
                _require(footprint=={(x+dx,y+dy) for dx in range(side) for dy in range(side)},actor_path+'.cells','wrong source-size square footprint')
                _require(not footprint & occupied,actor_path+'.cells','actor overlaps another actor or party/entry span')
                _require(objective not in footprint,actor_path+'.cells','actor overlaps safe objective')
                _require(not footprint & control_points,actor_path+'.cells','actor overlaps safe gate prerequisite/control')
                occupied |= footprint
                xp += source['metadata']['xp']
                definitions.add(sid)
                cautions.update(source['metadata']['cautions'])
                roots.add(sid)
            _require(len(definitions)<=3 and len(encounter['monsters'])<=2*len(brief['party_levels']),path+'.encounter.monsters','definition/creature limits exceeded')
            _require(encounter['xp']==xp and 3*budget<=5*xp<=5*budget,path+'.encounter.xp','incorrect base-XP sum or <60%/over-budget placement')
            if len(set(brief['party_levels']))>1:
                contributions = [catalog['tables']['xp_budgets'][str(level)][difficulty] for level in brief['party_levels']]
                cautions.add(f'Mixed-level budget summation is a generator extension; per-PC XP contributions: {contributions}.')
            _require(cautions <= set(encounter['cautions']),path+'.encounter.cautions','missing original source/mixed-level cautions')
        if trap:
            trap_path = path+'.trap'
            _require(trap['instance_id'] not in instances,trap_path+'.instance_id','duplicate instance ID','E_REFERENCE')
            instances.add(trap['instance_id'])
            sid = trap['catalog_id']
            _require(sid in catalog['packs'][brief['theme']]['traps'],trap_path+'.catalog_id','trap outside source pack','E_SOURCE')
            source = catalog['entries'][sid]
            _require(trap['tier']==rules.party_tier(brief['party_levels']),trap_path+'.tier','trap must use lowest member tier')
            _require(brief['trap_severity']=='mixed' or source['metadata']['severity']==brief['trap_severity'],trap_path+'.catalog_id','trap severity differs from request')
            trigger = _cell_set(trap['trigger_cells'],cells,trap_path+'.trigger_cells')
            effect = _cell_set(trap['effect_cells'],cells,trap_path+'.effect_cells')
            _require(len(trigger)==1 and trigger <= effect,trap_path+'.trigger_cells','one localized source trigger must belong to effect area')
            bypass = _cell_set(trap['bypass_path'],cells,trap_path+'.bypass_path')
            _require(not trigger & bypass,trap_path+'.bypass_path','safe bypass crosses trigger')
            _require(all(abs(x-u)+abs(y-v)==1 for (x,y),(u,v) in zip(trap['bypass_path'],trap['bypass_path'][1:])),
                     trap_path+'.bypass_path','bypass must be an ordered cardinal path')
            controls = {tuple(g['prerequisite_point']) for g in geometry['gates'] if g['prerequisite_room']==room['id']}
            targets = mouths | controls | {tuple(room['anchor'])}
            if objective in cells:
                targets.add(objective)
            _require(targets <= bypass,trap_path+'.bypass_path','bypass must include every entry and safe control/objective')
            _require(targets <= set(progression.reachable_cells(cells-trigger,min(targets))),trap_path+'.trigger_cells','trigger creates an uncounted mandatory barrier')
            if sid=='trap:falling-net':
                x,y = min(p[0] for p in effect),min(p[1] for p in effect)
                _require(effect=={(x+dx,y+dy) for dx in range(2) for dy in range(2)},trap_path+'.effect_cells','net canopy must be 10-foot square, distinct from trip wire')
            elif sid=='trap:hidden-pit':
                _require(effect==trigger,trap_path+'.effect_cells','pit effect must match lid')
            elif sid=='trap:poisoned-darts':
                x,y = next(iter(trigger))
                lane = {(x,y)}
                for direction in (-1,1):
                    u = x+direction
                    while (u,y) in cells:
                        lane.add((u,y))
                        u += direction
                _require(effect==lane,trap_path+'.effect_cells','dart effect must be full wall-to-wall lane through plate')
                _require((min(p[0] for p in lane)-1,y) not in floor and
                         (max(p[0] for p in lane)+1,y) not in floor,
                         trap_path+'.effect_cells','dart lane must terminate at actual walls, not an open corridor')
            elif sid=='trap:collapsing-roof':
                _require(set(progression.reachable_cells(effect,next(iter(trigger))))==effect,
                         trap_path+'.effect_cells','collapsing roof area must be connected to wire')
            _require(trap['escape_aid']==(content.PIT_ESCAPE if sid=='trap:hidden-pit' else None),trap_path+'.escape_aid','mundane escape must preserve source-permitted aid')
            mechanical = content.trap_mechanics(source,trap['tier'])
            _require(any(f['text']==mechanical and f['source_ids']==[sid] and not f['product_choice'] for f in room_content['features']),
                     path+'.features','complete selected trap mechanics/DCs/reset must be retained','E_SOURCE')
            hazards |= trigger
            roots.add(sid)
        for j,feature in enumerate(room_content['features']):
            feature_path = path+f'.features[{j}]'
            _require(feature['text'].strip(),feature_path+'.text','feature must be nonblank')
            _no_definitions(feature['text'],feature_path+'.text')
            _require(feature['product_choice'] or feature['source_ids'],feature_path,'feature must be source-tagged or clearly product-choice')
            for sid in feature['source_ids']:
                _require(sid in catalog['entries'],feature_path+'.source_ids','unknown source feature reference','E_SOURCE')
                roots.add(sid)
            if not feature['product_choice']:
                valid = any(feature['text']==catalog['entries'][sid]['rules_text'] or (trap and sid==trap['catalog_id'] and
                    feature['text']==content.trap_mechanics(catalog['entries'][sid],trap['tier'])) for sid in feature['source_ids'])
                _require(valid,feature_path+'.text','source-tagged mechanical text differs from trusted complete wording','E_SOURCE')
    _require(not hazards & required,'$.contents.objective_point','objective/entrance/entries must avoid triggers')
    _require(high_rooms==[geometry['objective_room']] if brief['climax'] else not high_rooms,
             '$.contents.rooms','climax must replace exactly one combat fight at objective; ordinary fights low/moderate')
    if not brief['climax']:
        farthest = max((r for r in rooms.values() if r['required']),key=lambda r:(distances[tuple(r['anchor'])],r['id']))
        _require(geometry['objective_room']==farthest['id'],'$.geometry.objective_room','ordinary objective must be farthest required room')
    else:
        from .layout import FitIssue
        chosen = rooms[geometry['objective_room']]
        chosen_order = distances[tuple(chosen['anchor'])],chosen['id']
        for room in rooms.values():
            order = distances[tuple(room['anchor'])],room['id']
            if order <= chosen_order or not room['required'] or entries[room['id']]['role']!='combat':
                continue
            try:
                content.climax_encounter(geometry,room,brief,brief['seed'],catalog)
            except FitIssue:
                continue
            raise ValidationIssue('E_CLIMAX','$.geometry.objective_room','farther High-fitting combat room exists')
    _validate_rewards(contents,geometry,brief,catalog)
    _require(len(contents['treasure'])==actual['item'] and sum(contents['coins'].values())==actual['coin'],
             '$.contents.actual_counts','actual reward totals differ')
    for treasure in contents['treasure']:
        _require(treasure['instance_id'] not in instances,'$.contents.treasure','duplicate global instance ID')
        instances.add(treasure['instance_id'])
        roots.add(treasure['catalog_id'])
    return roots,hazards,required


def _validate_rewards(contents, geometry, brief, catalog):
    """Check actual rewards independently; legal author edits are not seed replay."""
    from . import rules
    request = brief['treasure']
    room_ids = {r['id'] for r in geometry['rooms']}
    items = _ids(contents['treasure'],'instance_id','$.contents.treasure','T')
    _require(len(items)==request['magic_items'],'$.contents.treasure','item quantity differs from request')
    pools = {}
    for sid in catalog['packs'][brief['theme']]['items']:
        rarity = catalog['entries'][sid]['metadata']['rarity']
        pools.setdefault(rarity,set()).add(sid)
    used = {rarity:set() for rarity in pools}
    rarity_order = ('common','uncommon','rare','very-rare','legendary')
    weights = catalog['tables']['rarity_weights'][str(rules.party_tier(brief['party_levels']))]
    for i,item in enumerate(contents['treasure']):
        path = f'$.contents.treasure[{i}]'
        sid = item['catalog_id']
        _require(sid in catalog['packs'][brief['theme']]['items'],path+'.catalog_id','item must be actual selected-pack source entry','E_SOURCE')
        rarity = catalog['entries'][sid]['metadata']['rarity']
        _require(rarity==request['rarity_override'] if request['rarity_override'] else weights[rarity_order.index(rarity)]>0,
                 path+'.catalog_id','actual source rarity contradicts override/source tier table')
        if used[rarity]==pools[rarity]:
            used[rarity].clear()
        _require(sid not in used[rarity],path+'.catalog_id','repeat before actual selected-rarity unique pool exhausted')
        used[rarity].add(sid)
        _require(item['room_id'] in room_ids,path+'.room_id','unknown reward room','E_REFERENCE')
    coins = contents['coins']
    _require(set(coins) <= room_ids,'$.contents.coins','unknown coin room','E_REFERENCE')
    _require(sum(coins.values())==request['coin_gp'],'$.contents.coins','exact party-wide coin total differs from request')
    mode = request['placement']
    eligible = sorted(room_ids-{'R001'})
    if mode=='guarded':
        eligible = sorted(r['room_id'] for r in contents['rooms'] if r['role']=='combat')
    elif mode=='final_room':
        eligible = [geometry['objective_room']]
    if mode=='custom':
        expected_coins,placements = {},{}
        for i,allocation in enumerate(request['custom']):
            path = f'$.brief.treasure.custom[{i}]'
            room = allocation['room_id']
            _require(room in room_ids and room not in expected_coins,path+'.room_id','unknown or duplicate allocation room')
            expected_coins[room] = allocation['coin_gp']
            for identifier in allocation['magic_item_ids']:
                _require(identifier in items and identifier not in placements,path+'.magic_item_ids','unknown or duplicate allocation item')
                placements[identifier] = room
        _require(set(placements)==set(items) and sum(expected_coins.values())==request['coin_gp'],
                 '$.brief.treasure.custom','custom allocations leave items or coins unallocated')
        _require(coins==expected_coins,'$.contents.coins','coin placements differ from exact custom allocations')
        _require(all(item['room_id']==placements[item['instance_id']] for item in items.values()),
                 '$.contents.treasure','item placements differ from exact custom allocations')
    else:
        _require(eligible or not (request['magic_items'] or request['coin_gp']),
                 '$.contents.treasure','nonzero rewards have no eligible rooms')
        _require(all(item['room_id'] in eligible for item in items.values()) and set(coins)<=set(eligible),
                 '$.contents.treasure','reward placement outside eligible rooms')
        if mode=='sparse_hidden':
            placements = {item['room_id'] for item in items.values()} | set(coins)
            _require(len(placements)<=1,'$.contents.treasure','sparse rewards must share one non-entrance room')
        elif mode in ('balanced','guarded','final_room'):
            _require(all(item['room_id']==eligible[i%len(eligible)] for i,item in enumerate(items.values())),
                     '$.contents.treasure','items must cycle across sorted eligible rooms')
            expected_coins = {}
            if request['coin_gp']:
                share,remainder = divmod(request['coin_gp'],len(eligible))
                expected_coins = {room:share+(i<remainder) for i,room in enumerate(eligible)}
            _require(coins==expected_coins,'$.contents.coins','coin split/remainder differs from sorted eligible-room allocation')
        elif mode=='random':
            _require(len(coins)==(1 if request['coin_gp'] else 0),'$.contents.coins','random coin hoard must occupy one eligible room')
    _require(all(item['hidden']==(mode=='sparse_hidden') for item in items.values()),
             '$.contents.treasure','hidden flags differ from placement mode')
