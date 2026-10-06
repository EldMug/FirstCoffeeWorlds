"""Actual-floor gate acquisition, with persistent prerequisites and safe paths."""
from collections import deque
from copy import deepcopy

from .layout import FitIssue
from .model import ValidationIssue, _no_definitions
from .rng import Stream


def floor_cells(geometry):
    return {(x, y) for y, row in enumerate(geometry['rows']) for x, value in enumerate(row) if value == '.'}


def reachable_cells(floor, start):
    """Legal 5-foot moves; diagonals cannot cross either solid wall corner."""
    floor, start = set(map(tuple, floor)), tuple(start)
    reached = {start} if start in floor else set()
    pending = deque(reached)
    while pending:
        x, y = pending.popleft()
        for dx, dy in ((0,-1),(-1,0),(1,0),(0,1),(-1,-1),(1,-1),(-1,1),(1,1)):
            point = x+dx, y+dy
            if point not in floor or point in reached:
                continue
            if dx and dy and ((x+dx,y) not in floor or (x,y+dy) not in floor):
                continue
            reached.add(point)
            pending.append(point)
    return reached


def room_entries(geometry, room):
    """Chamber-side mouths, including every width cell, never outside door cells."""
    cells = set(map(tuple, room['cells']))
    entries = set()
    for passage in geometry['passages']:
        if room['id'] not in (passage['from_room'], passage['to_room']):
            continue
        outside = set(map(tuple, passage['cells'])) - cells
        for x, y in cells:
            if any(p in outside for p in ((x,y-1),(x-1,y),(x+1,y),(x,y+1))):
                entries.add((x,y))
    if room['id'] == 'R001':
        entries.add(tuple(geometry['entrance']))
    return [list(p) for p in sorted(entries, key=lambda p:(p[1],p[0]))]


def door_passage(geometry, door, path):
    """A complete perpendicular span must physically cut its own passage."""
    rooms = {r['id']: set(map(tuple,r['cells'])) for r in geometry['rooms']}
    span = set(map(tuple, door['cells']))
    candidates = []
    for passage in geometry['passages']:
        endpoints = rooms.get(passage['from_room'],set()) | rooms.get(passage['to_room'],set())
        cells = set(map(tuple,passage['cells']))
        if span and span <= cells - endpoints:
            candidates.append((passage, cells | endpoints))
    if len(candidates) != 1:
        raise ValidationIssue('E_GATE_SPAN', path, 'door must belong to exactly one passage interior')
    passage, cells = candidates[0]
    ordered = sorted(span)
    straight = len(span) == 1 or (len(span) == 2 and
        abs(ordered[0][0]-ordered[1][0])+abs(ordered[0][1]-ordered[1][1]) == 1)
    if len(span) != passage['width'] or not straight:
        raise ValidationIssue('E_GATE_SPAN', path, 'door does not span complete passage width')
    start, end = passage['centerline'][0], tuple(passage['centerline'][-1])
    if end in reachable_cells(cells-span, start):
        raise ValidationIssue('E_GATE_SPAN', path, 'closed span has an actual cardinal/diagonal bypass')
    return passage


def validate_progression(geometry, avoid_cells=None, required_points=None):
    """Validate at most four persistent acquisition masks; tree fixtures permitted."""
    gates = geometry['gates']
    if len(gates) > 2 or len({g['mechanism'] for g in gates}) != len(gates):
        raise ValidationIssue('E_GATES', '$.geometry.gates', 'at most two DISTINCT mechanisms permitted')
    rooms = {r['id']: r for r in geometry['rooms']}
    doors = {d['id']: d for d in geometry['doors']}
    if len(rooms) != len(geometry['rooms']) or len(doors) != len(geometry['doors']):
        raise ValidationIssue('E_REFERENCE', '$.geometry', 'duplicate room/door identifiers')
    ids = {g['id'] for g in gates}
    if len(ids) != len(gates) or len({g['door_id'] for g in gates}) != len(gates) or len({g['prerequisite_id'] for g in gates}) != len(gates):
        raise ValidationIssue('E_REFERENCE', '$.geometry.gates', 'duplicate gate, door or prerequisite identifier')
    floor, hazards = floor_cells(geometry), set(map(tuple, avoid_cells or []))
    if not hazards <= floor:
        raise ValidationIssue('E_HAZARD', '$.geometry', 'hazard mask contains nonfloor points')
    for i, door in enumerate(geometry['doors']):
        if door['gate_id'] is not None and not any(g['id']==door['gate_id'] and g['door_id']==door['id'] for g in gates):
            raise ValidationIssue('E_REFERENCE', f'$.geometry.doors[{i}].gate_id', 'unknown or nonreciprocal gate')
    for i, gate in enumerate(gates):
        path = f'$.geometry.gates[{i}]'
        door = doors.get(gate['door_id'])
        room = rooms.get(gate['prerequisite_room'])
        if door is None or door['gate_id'] != gate['id'] or room is None:
            raise ValidationIssue('E_REFERENCE', path, 'gate/door/prerequisite references do not resolve reciprocally')
        if gate['mechanism'] not in ('key_lock','lever','secret_door') or not gate['prerequisite_id'].strip() or not gate['clue'].strip() or not gate['solution'].strip():
            raise ValidationIssue('E_GATES', path, 'gate needs known mechanism, explicit clue and solution')
        for field in ('clue','solution'):
            _no_definitions(gate[field],path+'.'+field)
        point = tuple(gate['prerequisite_point'])
        if point not in set(map(tuple,room['cells'])) or point in hazards:
            raise ValidationIssue('E_PREREQUISITE', path+'.prerequisite_point', 'prerequisite is outside room or unsafe')
        passage = door_passage(geometry, door, path+'.door_id')
        if (gate['mechanism'] == 'secret_door') != passage['secret']:
            raise ValidationIssue('E_SECRET', path+'.mechanism', 'secret passage requires its reveal gate')
        if 'Blocks required progression.' in gate['clue']:
            blocked = reachable_cells(floor-hazards-set(map(tuple,door['cells'])),geometry['entrance'])
            targets = {tuple(r['anchor']) for r in rooms.values() if r['required']}
            targets |= set(map(tuple,required_points or [])) - set(map(tuple,geometry['secret_cells']))
            if targets <= blocked:
                raise ValidationIssue('E_GATES',path+'.clue','claimed required gate is physically bypassable')
    for i, passage in enumerate(geometry['passages']):
        if passage['secret'] and not any(g['mechanism']=='secret_door' and
                set(map(tuple,doors[g['door_id']]['cells'])) <= set(map(tuple,passage['cells'])) for g in gates):
            raise ValidationIssue('E_SECRET', f'$.geometry.passages[{i}]', 'secret passage lacks counted reveal gate')
    mask = 0
    for _ in range(1 << len(gates)):
        closed = set().union(*(set(map(tuple,doors[g['door_id']]['cells']))
                             for i,g in enumerate(gates) if not mask & (1 << i)))
        reached = reachable_cells(floor - hazards - closed, geometry['entrance'])
        next_mask = mask
        for i, gate in enumerate(gates):
            if tuple(gate['prerequisite_point']) in reached:
                next_mask |= 1 << i
        if next_mask == mask:
            break
        mask = next_mask
    if mask != (1 << len(gates))-1:
        locked = [g['id'] for i,g in enumerate(gates) if not mask & (1 << i)]
        raise ValidationIssue('E_PROGRESS', '$.geometry.gates', f'unreachable prerequisite/self-lock/cycle: {locked}')
    required = {tuple(p) for p in required_points or []}
    required |= {tuple(r['anchor']) for r in rooms.values() if r['required']}
    objective = rooms.get(geometry['objective_room'])
    if objective is None:
        raise ValidationIssue('E_REFERENCE', '$.geometry.objective_room', 'unknown objective room')
    required.add(tuple(objective['anchor']))
    reached = reachable_cells(floor-hazards, geometry['entrance'])
    if not required <= reached:
        raise ValidationIssue('E_PROGRESS', '$.geometry', 'required room, objective or safe point unreachable')


def place_gates(geometry, brief, seed, hazard_cells):
    """Fresh geometry; safe reachable reusable keys/controls/reveal clues."""
    result = deepcopy(geometry)
    result['gates'] = []
    for door in result['doors']:
        door['gate_id'] = None
    stream = Stream(seed,'gates')
    hazards = set(map(tuple,hazard_cells))
    floor = floor_cells(result)
    candidates = []
    for i, door in enumerate(result['doors']):
        passage = door_passage(result,door,f'$.geometry.doors[{i}]')
        candidates.append((door, passage))
    secret = [c for c in candidates if c[1]['secret']]
    ordinary = [c for c in candidates if not c[1]['secret']]
    selected = [(d,'secret_door') for d,_ in secret]
    mechanisms = ['key_lock','lever']
    while len(selected) < brief['gate_count'] and ordinary and mechanisms:
        door, _ = ordinary.pop(stream.randbelow(len(ordinary)))
        selected.append((door,mechanisms.pop(stream.randbelow(len(mechanisms)))))
    if len(selected) != brief['gate_count']:
        raise FitIssue('E_GATE_FIT','$.geometry.gates','not enough distinct complete door spans')
    closed = set().union(*(set(map(tuple,d['cells'])) for d,_ in selected))
    reached = reachable_cells(floor-hazards-closed,result['entrance'])
    for i, (door, mechanism) in enumerate(selected,1):
        # Chamber-side mouths are already reserved from actor placement and
        # included in every trap bypass. The geometry-only API can therefore
        # choose controls safe from both actors and triggers without content
        # mutation or pretending monster footprints are permanent walls.
        safe = [(r,p) for r in result['rooms'] if not r['secret']
                for p in sorted(set(map(tuple,room_entries(result,r))) & reached,key=lambda p:(p[1],p[0]))]
        if not safe:
            raise FitIssue('E_GATE_FIT','$.geometry.gates','no reachable safe prerequisite location')
        room, point = safe[stream.randbelow(len(safe))]
        identifier = f'G{i:03d}'
        other_closed = set(map(tuple,door['cells']))
        blocked = reachable_cells(floor-hazards-other_closed,result['entrance'])
        required = {tuple(r['anchor']) for r in result['rooms'] if r['required']}
        label = 'Blocks required progression.' if not required <= blocked else 'Optional barrier; alternate routes may bypass it.'
        action = {
            'key_lock': (f'Reusable key {identifier} rests at {room["id"]} {list(point)}.',
                         f'Use reusable key {identifier} in door {door["id"]}; key is not consumed.'),
            'lever': (f'Control {identifier} at {room["id"]} {list(point)} is marked OPEN.',
                      f'Pull the lever marked OPEN; door {door["id"]} stays open permanently.'),
            'secret_door': (f'Clue at {room["id"]} {list(point)}: a hand-shaped seam marks door {door["id"]}.',
                            f'Follow the clue and press the hand-shaped seam to reveal and open {door["id"]}; no spell required.'),
        }[mechanism]
        result['gates'].append({'id':identifier,'mechanism':mechanism,'door_id':door['id'],
            'prerequisite_id':f'{mechanism}-{identifier}','prerequisite_room':room['id'],
            'prerequisite_point':list(point),'clue':f'{label} {action[0]}','solution':action[1]})
        door['gate_id'] = identifier
        closed -= set(map(tuple,door['cells']))
        reached = reachable_cells(floor-hazards-closed,result['entrance'])
    try:
        validate_progression(result,avoid_cells=hazard_cells)
    except ValidationIssue as error:
        raise FitIssue('E_GATE_FIT',error.path,str(error)) from None
    return result
