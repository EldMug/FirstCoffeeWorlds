"""Fit-aware source encounters, safe traps, room text and exact rewards."""
from collections import Counter, deque
from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations, groupby

from . import rules
from .layout import FitIssue
from .rng import Stream
from . import model, progression
from .layout import cell_distances

GROUP_LIMIT = 100000
GROUP_NODE_LIMIT = 10000
ROOM_NODE_LIMIT = 100000
_SIDES = {'small': 1, 'medium': 1, 'large': 2, 'huge': 3, 'gargantuan': 4}


@dataclass
class _Work:
    groups_enumerated: int = 0
    groups_considered: int = 0
    total_nodes: int = 0
    max_group_nodes: int = 0


def _points(cells, path):
    if type(cells) is not list or any(type(p) is not list or len(p) != 2 or
            any(type(v) is not int or not 0 <= v < 80 for v in p) for p in cells):
        raise FitIssue('E_CONTENT_FIT', path, 'expected grid points [x,y], integers 0–79')
    result = set(map(tuple, cells))
    if len(result) != len(cells):
        raise FitIssue('E_CONTENT_FIT', path, 'duplicate floor/entry points')
    return result


def _entry_reserve(floor, entries, count):
    if not entries <= floor:
        raise FitIssue('E_CONTENT_FIT', '$.entry_cells', 'entry/door-span points must be inside the chamber')
    ordered = sorted(entries or floor, key=lambda p:(p[1],p[0]))
    pending = deque(ordered if entries else ordered[:1])
    visited, reserve = set(pending), []
    while pending and len(reserve) < count:
        x, y = pending.popleft()
        reserve.append((x, y))
        for point in ((x,y-1),(x-1,y),(x+1,y),(x,y+1)):
            if point in floor and point not in visited:
                visited.add(point)
                pending.append(point)
    if len(reserve) != count:
        raise FitIssue('E_CONTENT_FIT', '$.party_entry_cells', f'cannot reserve {count} party squares near entry')
    return reserve


@lru_cache(maxsize=64)
def _candidate_groups(digest, theme, roster, difficulty, budget, monsters):
    """Immutable cached groups; signature includes the actual eligibility data."""
    groups = []
    limit = 2*len(roster)
    eligible = [m for m in monsters if 0 < m[1] <= budget]
    for types in range(1, 4):
        for selected in combinations(eligible, types):
            def enumerate_counts(index, remaining, xp, counts):
                if index == types:
                    if 3*budget <= 5*xp <= 5*budget:
                        if len(groups) >= GROUP_LIMIT:
                            raise FitIssue('E_CONTENT_FIT', '$.encounter', f'candidate limit {GROUP_LIMIT} exhausted')
                        groups.append((xp, types, sum(counts), tuple(zip(selected, counts))))
                    return
                entry = selected[index]
                maximum = min(remaining-(types-index-1), (budget-xp)//entry[1])
                for count in range(1, maximum+1):
                    enumerate_counts(index+1, remaining-count, xp+entry[1]*count, counts+(count,))
            enumerate_counts(0, limit, 0, ())
    return tuple(sorted(groups, key=lambda g:(-g[0],g[1],g[2],g[3])))


def _footprint_options(free, sizes):
    ordered = sorted(free, key=lambda p:(p[1],p[0]))
    bits = {point: 1 << i for i, point in enumerate(ordered)}
    options = {}
    for side in sorted(sizes, reverse=True):
        placements = []
        for x, y in ordered:
            cells = tuple((x+dx, y+dy) for dy in range(side) for dx in range(side))
            if all(p in free for p in cells):
                placements.append((sum(bits[p] for p in cells), cells))
        options[side] = placements
    return options


def _pack(instances, options, work):
    """Largest-first bitmask search; identical footprints avoid permutations."""
    instances.sort(key=lambda m:(-m[3],m[0]))
    nodes, placed = 0, []
    def visit(index, occupied, previous_index):
        nonlocal nodes
        if index == len(instances):
            return True
        side = instances[index][3]
        start = previous_index+1 if index and instances[index-1][3] == side else 0
        for option_index in range(start, len(options[side])):
            if nodes >= GROUP_NODE_LIMIT or work.total_nodes >= ROOM_NODE_LIMIT:
                return False
            nodes += 1
            work.total_nodes += 1
            mask, cells = options[side][option_index]
            if mask & occupied:
                continue
            placed.append(cells)
            if visit(index+1, occupied | mask, option_index):
                return True
            placed.pop()
        return False
    success = visit(0, 0, -1)
    work.max_group_nodes = max(work.max_group_nodes, nodes)
    return placed if success else None


def choose_encounter(room_cells, entry_cells, party_levels, difficulty, theme, seed, catalog):
    """Reserve one PC square each and every supplied in-room entrance/span cell.

    Return a fresh Encounter v1, or contextual E_CONTENT_FIT. Catalog must be
    the caller's already verified load; no source verification inside search.
    """
    budget = rules.encounter_budget(party_levels, difficulty, catalog=catalog)
    stream = Stream(seed, 'encounters')
    if theme not in catalog['packs']:
        raise FitIssue('E_CONTENT_FIT', '$.theme', f'unknown theme {theme!r}')
    work = _Work()
    floor, entries = _points(room_cells, '$.room_cells'), _points(entry_cells, '$.entry_cells')
    reserve = _entry_reserve(floor, entries, len(party_levels))
    free = floor - entries - set(reserve)
    monsters = []
    for identifier in sorted(catalog['packs'][theme]['monsters']):
        metadata = catalog['entries'][identifier]['metadata']
        size = 'medium' if 'medium' in metadata['sizes'] else metadata['sizes'][0]
        if size not in _SIDES or metadata['variant'] != 'base':
            raise FitIssue('E_CONTENT_FIT', f'$.catalog.entries.{identifier}', 'unsupported source size or nonbase variant')
        monsters.append((identifier, metadata['xp'], size, _SIDES[size]))
    groups = _candidate_groups(catalog['digest'], theme, tuple(party_levels), difficulty, budget, tuple(monsters))
    work.groups_enumerated = len(groups)
    options = _footprint_options(free, {m[3] for m in monsters})
    if free:
        xs, ys = zip(*free)
        box_width, box_height = max(xs)-min(xs)+1, max(ys)-min(ys)+1
    else:
        box_width = box_height = 0
    for _, tied in groupby(groups, key=lambda g:g[:3]):
        tied = list(tied)
        for i in range(len(tied)-1, 0, -1):
            j = stream.randbelow(i+1)
            tied[i], tied[j] = tied[j], tied[i]
        for xp, _, _, selected in tied:
            if work.groups_considered >= GROUP_LIMIT or work.total_nodes >= ROOM_NODE_LIMIT:
                break
            work.groups_considered += 1
            sizes = Counter()
            for monster, count in selected:
                sizes[monster[3]] += count
            if sum(side*side*count for side,count in sizes.items()) > len(free):
                continue
            if any(count > min(len(options[side]), (box_width//side)*(box_height//side), len(free)//(side*side))
                   for side,count in sizes.items()):
                continue
            instances = [monster for monster,count in selected for _ in range(count)]
            packed = _pack(instances, options, work)
            if packed is None:
                continue
            placements = [{'instance_id': f'M{i:03d}', 'catalog_id': monster[0],
                           'selected_size': monster[2], 'cells': [list(p) for p in cells]}
                          for i,(monster,cells) in enumerate(zip(instances,packed),1)]
            cautions = sorted({text for monster,_ in selected
                               for text in catalog['entries'][monster[0]]['metadata']['cautions']})
            if len(set(party_levels)) > 1:
                contributions = [catalog['tables']['xp_budgets'][str(level)][difficulty] for level in party_levels]
                cautions.append(f'Mixed-level budget summation is a generator extension; per-PC XP contributions: {contributions}.')
            return {'target_difficulty': difficulty, 'budget': budget, 'xp': xp, 'monsters': placements,
                    'party_entry_cells': [list(p) for p in reserve], 'cautions': cautions}
        if work.groups_considered >= GROUP_LIMIT or work.total_nodes >= ROOM_NODE_LIMIT:
            break
    raise FitIssue('E_CONTENT_FIT', '$.encounter',
                   f'theme={theme}, roster={party_levels}, difficulty={difficulty}, budget={budget}, '
                   f'free_floor={len(free)}: no legal >=60% base-XP placement; '
                   f'enumerated={work.groups_enumerated}, groups={work.groups_considered}, '
                    f'nodes={work.total_nodes}, max_group_nodes={work.max_group_nodes}')


PIT_ESCAPE = 'Cracks in the pit walls provide handholds and footholds for mundane climbing.'


def trap_mechanics(entry, tier):
    """Selected-tier values plus complete unmodified operative source wording."""
    metadata = entry['metadata']
    variant = next(v for v in metadata['variants'] if v['tier']==tier)
    dc = 'None' if variant['save_dc'] is None else str(variant['save_dc'])
    return (f'Selected-tier values (apply the source higher-level scaling):\n'
            f'Trigger: {metadata["trigger_text"]}\nDuration/reset: {metadata["reset_text"]}\n'
            f'Tier {tier} (levels {variant["minimum_level"]}–{variant["maximum_level"]}). '
            f'Damage: {variant["damage_text"]}\nSave DC: {dc}\n'
            f'Discovery: {variant["discovery_text"]}\nDisarm: {variant["disarm_text"]}\n'
            f'Escape: {variant["escape_text"]}\n\nComplete source wording:\n{entry["rules_text"]}')


def climax_encounter(geometry, room, brief, seed, catalog):
    return choose_encounter(room['cells'],progression.room_entries(geometry,room),brief['party_levels'],
                            'high',brief['theme'],Stream(seed,f'encounters/climax/{room["id"]}').draw(),catalog)


def _bypass(floor, entries):
    """Simple ordered path containing every mouth and the room safe anchor.

    A bounded DFS is needed because the frozen Point-array schema forbids
    duplicate path cells. An impossible simple path is a fit rejection.
    """
    targets = set(map(tuple,entries))
    if not targets:
        return None
    start = min(targets,key=lambda p:(p[1],p[0]))
    path, used, nodes = [start], {start}, 0
    def visit(point, remaining):
        nonlocal nodes
        if not remaining:
            return True
        if nodes >= 10000:
            return False
        x,y = point
        options = [(x,y-1),(x-1,y),(x+1,y),(x,y+1)]
        options = [p for p in options if p in floor and p not in used]
        options.sort(key=lambda p:(min(abs(p[0]-t[0])+abs(p[1]-t[1]) for t in remaining),p[1],p[0]))
        for p in options:
            if nodes >= 10000:
                return False
            nodes += 1
            used.add(p)
            path.append(p)
            left = remaining-{p}
            if not left or left <= set(cell_distances((floor-used) | {p},p)):
                if visit(p,left):
                    return True
            path.pop()
            used.remove(p)
        return False
    return [list(p) for p in path] if visit(start,targets-{start}) else None


def _place_trap(geometry, room, entry, tier, stream, index, objective_point):
    floor = set(map(tuple,room['cells']))
    entries = set(map(tuple,progression.room_entries(geometry,room))) | {tuple(room['anchor'])}
    if tuple(objective_point) in floor:
        entries.add(tuple(objective_point))
    candidates = sorted(floor-entries,key=lambda p:(p[1],p[0]))
    for i in range(len(candidates)-1,0,-1):
        j = stream.randbelow(i+1)
        candidates[i],candidates[j] = candidates[j],candidates[i]
    for x,y in candidates[:100]:
        trigger = {(x,y)}
        if entry['id']=='trap:falling-net':
            options = [{(u+dx,v+dy) for dx in range(2) for dy in range(2)}
                       for u,v in ((x,y),(x-1,y),(x,y-1),(x-1,y-1))]
            effect = next((p for p in options if p <= floor),None)
            if effect is None:
                continue
        elif entry['id']=='trap:poisoned-darts':
            # Wall-to-wall dart lane on the pressure plate's row.
            left, right = x,x
            while (left-1,y) in floor:
                left -= 1
            while (right+1,y) in floor:
                right += 1
            actual_floor = progression.floor_cells(geometry)
            if (left-1,y) in actual_floor or (right+1,y) in actual_floor:
                continue
            effect = {(u,y) for u in range(left,right+1)}
        elif entry['id']=='trap:collapsing-roof':
            effect = {(u,v) for u,v in ((x,y),(x+1,y),(x,y+1),(x+1,y+1)) if (u,v) in floor}
        else:
            effect = trigger.copy()
        bypass = _bypass(floor-trigger,entries)
        if bypass is None:
            continue
        return {'instance_id':f'H{index:03d}','catalog_id':entry['id'],'tier':tier,
                'trigger_cells':[list(p) for p in sorted(trigger)],
                'effect_cells':[list(p) for p in sorted(effect,key=lambda p:(p[1],p[0]))],
                'bypass_path':bypass,'escape_aid':PIT_ESCAPE if entry['id']=='trap:hidden-pit' else None}
    raise FitIssue('E_TRAP_FIT',f'$.contents.rooms.{room["id"]}.trap',
                   f'{entry["id"]}: no safe trigger/bypass within 100 candidates')


def reward_placements(geometry, rooms, brief, seed, catalog):
    """Actual rarity pools, unique-per-pool draws, exact integer allocations."""
    stream = Stream(seed,'treasure')
    request = brief['treasure']
    ids = [r['id'] for r in geometry['rooms']]
    eligible = sorted(r for r in ids if r != 'R001')
    mode = request['placement']
    if mode=='guarded':
        eligible = sorted(r['room_id'] for r in rooms if r['role']=='combat')
    elif mode=='final_room':
        eligible = [geometry['objective_room']]
    elif mode=='sparse_hidden':
        eligible = [eligible[stream.randbelow(len(eligible))]]
    if not eligible and (request['coin_gp'] or request['magic_items']):
        raise model.ValidationIssue('E_REWARDS','$.brief.treasure.placement','nonzero rewards have no eligible room')
    treasure, used = [], set()
    for index in range(1,request['magic_items']+1):
        rarity = request['rarity_override'] or rules.draw_rarity(brief['party_levels'],stream,catalog=catalog)
        pool = sorted(identifier for identifier in catalog['packs'][brief['theme']]['items']
                      if catalog['entries'][identifier]['metadata']['rarity']==rarity)
        fresh = [identifier for identifier in pool if identifier not in used]
        if not fresh:
            used -= set(pool)
            fresh = pool
        if not fresh:
            raise FitIssue('E_REWARD_FIT','$.contents.treasure',f'empty actual rarity pool {rarity}')
        identifier = fresh[stream.randbelow(len(fresh))]
        used.add(identifier)
        room = eligible[stream.randbelow(len(eligible))] if mode=='random' else eligible[(index-1)%len(eligible)]
        treasure.append({'instance_id':f'T{index:03d}','catalog_id':identifier,'room_id':room,'hidden':mode=='sparse_hidden'})
    coins = {}
    if request['coin_gp'] and eligible:
        coin_rooms = [eligible[stream.randbelow(len(eligible))]] if mode=='random' else sorted(eligible)
        share, remainder = divmod(request['coin_gp'],len(coin_rooms))
        coins = {r:share+(i<remainder) for i,r in enumerate(coin_rooms)}
    if mode=='custom':
        coins, allocations = {}, {}
        actual_items = {t['instance_id']:t for t in treasure}
        for i, allocation in enumerate(request['custom']):
            path = f'$.brief.treasure.custom[{i}]'
            room = allocation['room_id']
            if room not in ids or room in coins:
                raise model.ValidationIssue('E_CUSTOM',path+'.room_id','unknown or duplicate allocation room')
            coins[room] = allocation['coin_gp']
            for identifier in allocation['magic_item_ids']:
                if identifier not in actual_items or identifier in allocations:
                    raise model.ValidationIssue('E_CUSTOM',path+'.magic_item_ids','unknown or duplicate item instance')
                allocations[identifier] = room
        if set(allocations) != set(actual_items) or sum(coins.values()) != request['coin_gp']:
            raise model.ValidationIssue('E_CUSTOM','$.brief.treasure.custom','unallocated items or incorrect total coins')
        for identifier,room in allocations.items():
            actual_items[identifier]['room_id'] = room
    return treasure,coins


def populate_rooms(geometry, brief, seed, catalog):
    """Fresh Content v1; objective_point identifies selected climax room.

    Caller finalizes geometry.objective_room from that point before gates and
    canonical validation. No geometry/brief/catalog input is mutated.
    """
    brief = model.normalize_brief(brief)
    counts = model.role_counts(len(geometry['rooms']),brief)
    if brief['treasure']['placement']=='guarded' and not counts['combat'] and (brief['treasure']['magic_items'] or brief['treasure']['coin_gp']):
        raise FitIssue('E_CONTENT_FIT','$.contents.treasure','auto target has no rounded combat room for guarded rewards')
    stream = Stream(seed,'encounters')
    ordered = sorted(geometry['rooms'],key=lambda r:(-len(r['cells']),r['id']))
    encounters = {}
    for room in ordered:
        if len(encounters)==counts['combat']:
            break
        difficulty = 'low' if stream.randbelow(100)<60 else 'moderate'
        try:
            encounter = choose_encounter(room['cells'],progression.room_entries(geometry,room),
                brief['party_levels'],difficulty,brief['theme'],stream.draw(),catalog)
        except FitIssue:
            continue
        encounters[room['id']] = encounter
    if len(encounters) != counts['combat']:
        raise FitIssue('E_CONTENT_FIT','$.contents.rooms',f'only {len(encounters)} largest fitting combat rooms; need {counts["combat"]}')
    objective = geometry['objective_room']
    if brief['climax']:
        distances = cell_distances(progression.floor_cells(geometry),geometry['entrance'])
        candidates = sorted((r for r in geometry['rooms'] if r['id'] in encounters and r['required'] and not r['secret']),
                            key=lambda r:(distances[tuple(r['anchor'])],r['id']),reverse=True)
        for room in candidates:
            try:
                high = climax_encounter(geometry,room,brief,seed,catalog)
            except FitIssue:
                continue
            objective = room['id']
            encounters[objective] = high
            break
        else:
            raise FitIssue('E_CONTENT_FIT','$.contents.objective_point','no High-fitting combat room for climax')
    objective_room = next(r for r in geometry['rooms'] if r['id']==objective)
    occupied = {tuple(p) for m in encounters.get(objective,{}).get('monsters',[]) for p in m['cells']}
    safe = sorted(set(map(tuple,objective_room['cells']))-occupied,key=lambda p:(p!=tuple(objective_room['anchor']),p[1],p[0]))
    objective_point = list(safe[0])
    # Rewards use the finalized objective without changing caller geometry.
    effective_geometry = dict(geometry,objective_room=objective)
    trap_stream, prose_stream = Stream(seed,'traps'),Stream(seed,'prose')
    available = [r for r in geometry['rooms'] if r['id'] not in encounters]
    # Prefer large rooms for a 10-foot-square net and a safe simple bypass.
    trap_rooms = {r['id'] for r in sorted(available,key=lambda r:(-len(r['cells']),r['id']))[:counts['trap']]}
    rooms, actor_index, trap_index = [],0,0
    for room in geometry['rooms']:
        identifier = room['id']
        encounter, trap = encounters.get(identifier),None
        role = 'combat' if encounter else 'trap' if identifier in trap_rooms else 'exploration'
        features = [{'text':f'Purpose: {brief["purpose"]}\nTone: {brief["tone"]}. Room roles and terrain placement are product choices.',
                     'source_ids':[],'product_choice':True}]
        if encounter:
            for actor in encounter['monsters']:
                actor_index += 1
                actor['instance_id'] = f'M{actor_index:03d}'
            occupants = ', '.join(f'{n} × {catalog["entries"][sid]["name"]}'
                for sid,n in sorted(Counter(m['catalog_id'] for m in encounter['monsters']).items()))
            features.append({'text':'Use base source stat blocks and normal actions; terrain grants no lair benefits.',
                             'source_ids':sorted({m['catalog_id'] for m in encounter['monsters']}),'product_choice':True})
        if role=='trap':
            severity = brief['trap_severity']
            if severity=='mixed':
                severity = 'nuisance' if trap_stream.randbelow(2)==0 else 'deadly'
            pool = sorted(sid for sid in catalog['packs'][brief['theme']]['traps']
                          if catalog['entries'][sid]['metadata']['severity']==severity)
            entry = catalog['entries'][pool[trap_stream.randbelow(len(pool))]]
            trap_index += 1
            trap = _place_trap(effective_geometry,room,entry,rules.party_tier(brief['party_levels']),
                               trap_stream,trap_index,objective_point)
            features.append({'text':trap_mechanics(entry,trap['tier']),'source_ids':[entry['id']],'product_choice':False})
            features.append({'text':('Lowest-member tier and trigger/effect placement are product choices. '
                'Follow the marked floor bypass without stepping on the trigger. '+(trap['escape_aid'] or
                'Use the source disarm/escape options; no specific class or spell is required.')),
                'source_ids':[entry['id']],'product_choice':True})
        templates = catalog['packs'][brief['theme']]['templates'][role]
        template = templates[prose_stream.randbelow(len(templates))]
        description = template['description'].format(occupants=occupants if encounter else '',
            trap_name=catalog['entries'][trap['catalog_id']]['name'] if trap else '',
            bypass='a clear marked floor route around the trigger')
        rooms.append({'room_id':identifier,'role':role,'title':template['title'],'description':description,
                      'encounter':encounter,'trap':trap,'features':features})
    treasure,coins = reward_placements(effective_geometry,rooms,brief,seed,catalog)
    requested = dict(model.role_counts(model.room_target(brief),brief),item=brief['treasure']['magic_items'],coin=brief['treasure']['coin_gp'])
    actual = dict(counts,item=len(treasure),coin=sum(coins.values()))
    return {'rooms':rooms,'treasure':treasure,'coins':coins,'objective_point':objective_point,
            'objective_description':brief['purpose'],'requested_counts':requested,'actual_counts':actual}
