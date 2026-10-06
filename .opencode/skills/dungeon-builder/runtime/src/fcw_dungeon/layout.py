"""One bounded deterministic chamber/route attempt; orchestration owns retries."""
from collections import deque
from dataclasses import dataclass, field
import heapq

from . import model
from .rng import Stream


class FitIssue(ValueError):
    def __init__(self, code, path, context):
        self.code, self.path, self.context = code, path, context
        super().__init__(f'{code} {path}: {context}')


@dataclass
class _Work:
    placement_trials: dict = field(default_factory=dict)
    route_expansions: list = field(default_factory=list)


def _ordered(cells):
    return [list(p) for p in sorted(cells, key=lambda p: (p[1], p[0]))]


def _neighbors(point):
    x, y = point
    return ((x, y-1), (x-1, y), (x+1, y), (x, y+1))


def cell_distances(cells, start):
    """Cardinal floor distances from start; unreachable points are absent."""
    cells = set(map(tuple, cells))
    start = tuple(start)
    result = {start: 0} if start in cells else {}
    pending = deque(result)
    while pending:
        point = pending.popleft()
        for neighbor in _neighbors(point):
            if neighbor in cells and neighbor not in result:
                result[neighbor] = result[point] + 1
                pending.append(neighbor)
    return result


def expand_centerline(centerline, width):
    """Width-2 ribbon is the union of positive-x/y 2x2 stamps at each point."""
    if width not in (1, 2):
        raise ValueError('passage width must be 1 or 2')
    return _ordered({(x+dx, y+dy) for x, y in centerline
                     for dx in range(width) for dy in range(width)})


def has_route_loop(rooms, passages):
    """Check the simple nonsecret endpoint graph, never floor-tile cycles."""
    parent = {r['id']: r['id'] for r in rooms if not r['secret']}
    def root(identifier):
        while parent[identifier] != identifier:
            identifier = parent[identifier]
        return identifier
    pairs = set()
    for passage in passages:
        a, b = passage['from_room'], passage['to_room']
        pair = tuple(sorted((a, b)))
        if passage['secret'] or a not in parent or b not in parent or a == b or pair in pairs:
            continue
        pairs.add(pair)
        left, right = root(a), root(b)
        if left == right:
            return True
        parent[left] = right
    return False


def _moat(cells):
    return cells | {neighbor for point in cells for neighbor in _neighbors(point)}


def _shuffle(values, stream):
    for i in range(len(values)-1, 0, -1):
        j = stream.randbelow(i+1)
        values[i], values[j] = values[j], values[i]


def _chambers(width, height, count, style, stream, work):
    # Stratification leaves rock lanes while sizes and positions vary within
    # slots. It is a placement strategy, not a fixed floor template.
    minimum = 5 if style == 'cave' else 3
    choices = []
    for columns in range(1, count+1):
        rows = (count+columns-1)//columns
        if (width-2)//columns >= minimum+1 and (height-2)//rows >= minimum+1:
            score = (columns*rows-count)*4 + abs(columns*height-rows*width)
            choices.append((score, columns, rows))
    if not choices:
        return None
    choices.sort()
    best = [p for p in choices if p[0] == choices[0][0]]
    _, columns, rows = best[stream.randbelow(len(best))]
    slots = [(c, r) for r in range(rows) for c in range(columns)]
    _shuffle(slots, stream)
    chambers, envelopes = [], []
    for index, (column, row) in enumerate(slots[:count]):
        left = 1 + column*(width-2)//columns
        right = 1 + (column+1)*(width-2)//columns
        top = 1 + row*(height-2)//rows
        bottom = 1 + (row+1)*(height-2)//rows
        for trial in range(1, 201):
            work.placement_trials[index] = trial
            w = minimum + stream.randbelow(min(16, right-left-1)-minimum+1)
            h = minimum + stream.randbelow(min(16, bottom-top-1)-minimum+1)
            x = left + stream.randbelow(right-left-w)
            y = top + stream.randbelow(bottom-top-h)
            envelope = {(u, v) for v in range(y, y+h) for u in range(x, x+w)}
            if any(envelope & _moat(previous) for previous in envelopes):
                continue
            if style == 'constructed':
                cells = envelope
            else:
                core = {(u, v) for v in range(y+1, y+h-1) for u in range(x+1, x+w-1)}
                # Every fringe point touches the core. Corners stay rock, so
                # even a fully sampled fringe is not a rectangular chamber.
                fringe = sorted(_moat(core) & envelope - core, key=lambda p:(p[1],p[0]))
                cells = core | {p for p in fringe if stream.randbelow(3) != 0}
                # Preserve the envelope extent and a usable entry on each side.
                cells |= {(x, y+h//2), (x+w-1, y+h//2), (x+w//2, y), (x+w//2, y+h-1)}
            chambers.append({'cells': _ordered(cells), 'anchor': [x+w//2, y+h//2],
                             'required': True, 'secret': False})
            envelopes.append(envelope)
            break
        else:
            return None
    combined = sorted(zip(chambers, envelopes), key=lambda pair:(pair[0]['anchor'][1],pair[0]['anchor'][0]))
    for i, (chamber, _) in enumerate(combined, 1):
        chamber['id'] = f'R{i:03d}'
    return [p[0] for p in combined], [p[1] for p in combined]


def _edges(rooms):
    edges = []
    for a in range(len(rooms)):
        x, y = rooms[a]['anchor']
        for b in range(a+1, len(rooms)):
            u, v = rooms[b]['anchor']
            edges.append(((x-u)**2+(y-v)**2, a, b))
    edges.sort()
    parent = list(range(len(rooms)))
    def root(i):
        while parent[i] != i:
            i = parent[i]
        return i
    tree = []
    for edge in edges:
        _, a, b = edge
        left, right = root(a), root(b)
        if left != right:
            tree.append(edge)
            parent[left] = right
    return tree, edges


def _astar(start, goal, allowed, expansion_limit, work):
    """A point is expanded at most once: <=grid area for each route."""
    pending = [(abs(start[0]-goal[0])+abs(start[1]-goal[1]), 0, start[1], start[0])]
    cost, previous, closed = {start: 0}, {}, set()
    while pending and len(closed) < expansion_limit:
        _, distance, y, x = heapq.heappop(pending)
        point = (x, y)
        if point in closed:
            continue
        closed.add(point)
        if point == goal:
            line = [point]
            while point != start:
                point = previous[point]
                line.append(point)
            work.route_expansions.append(len(closed))
            return list(reversed(line))
        for neighbor in _neighbors(point):
            if neighbor in allowed and neighbor not in closed and distance+1 < cost.get(neighbor, expansion_limit+1):
                cost[neighbor] = distance+1
                previous[neighbor] = point
                u, v = neighbor
                heuristic = abs(u-goal[0])+abs(v-goal[1])
                heapq.heappush(pending, (distance+1+heuristic, distance+1, v, u))
    work.route_expansions.append(len(closed))
    return None


def _door_span(line, cells, endpoints, width):
    interior = cells - endpoints
    for i in range(1, len(line)-1):
        x, y = line[i]
        before, after = line[i-1], line[i+1]
        if before[1] == y == after[1]:
            span = {(x, y+dy) for dy in range(width)}
        elif before[0] == x == after[0]:
            span = {(x+dx, y) for dx in range(width)}
        elif width == 1:
            span = {(x, y)}
        else:
            continue
        if span <= interior and line[-1] not in cell_distances((cells | endpoints) - span, line[0]):
            return span
    return None


def _route(a, b, preferred, rooms, envelopes, passages, width, height, work):
    room_cells = [set(map(tuple, r['cells'])) for r in rooms]
    endpoints = room_cells[a] | room_cells[b]
    blocked = set()
    for i, envelope in enumerate(envelopes):
        if i not in (a, b):
            blocked |= _moat(envelope)
    for passage in passages:
        interior = set(map(tuple, passage['cells'])) - set().union(*room_cells)
        blocked |= _moat(interior) - endpoints
    start, goal = tuple(rooms[a]['anchor']), tuple(rooms[b]['anchor'])
    first_search = len(work.route_expansions)
    for size in ([2, 1] if preferred == 2 else [1]):
        remaining = width*height - sum(work.route_expansions[first_search:])
        if remaining <= 0:
            break
        allowed = {(x, y) for y in range(1, height-size) for x in range(1, width-size)
                   if all((x+dx, y+dy) not in blocked for dx in range(size) for dy in range(size))}
        if start not in allowed or goal not in allowed:
            continue
        line = _astar(start, goal, allowed, remaining, work)
        if line is None:
            continue
        cells = set(map(tuple, expand_centerline(line, size)))
        span = _door_span(line, cells, endpoints, size)
        if span is None:
            continue
        passage = {'from_room': rooms[a]['id'], 'to_room': rooms[b]['id'],
                   'cells': _ordered(cells), 'centerline': [list(p) for p in line],
                   'width': size, 'secret': rooms[a]['secret'] or rooms[b]['secret']}
        return passage, span
    return None


def generate_layout(brief, seed, attempt_index, room_target):
    """Return exactly room_target rooms, or E_LAYOUT_FIT; never retry targets."""
    brief = model.normalize_brief(brief)
    if type(attempt_index) is not int or attempt_index < 0:
        raise ValueError('attempt_index must be a nonnegative integer')
    if type(room_target) is not int or not 4 <= room_target <= 48:
        raise ValueError('room_target must be integer 4–48')
    if brief['room_count'] is not None and room_target != brief['room_count']:
        raise FitIssue('E_LAYOUT_FIT', '$.room_count', 'explicit count cannot be reduced')
    width, height = brief['grid']['width'], brief['grid']['height']
    stream = Stream(seed, f'layout/target-{room_target}/attempt-{attempt_index}')
    work = _Work()
    def fail(constraint):
        raise FitIssue('E_LAYOUT_FIT', '$.geometry',
                       f'{width}x{height}, count={room_target}, attempt={attempt_index}: {constraint}; '
                       f'placement_trials={sum(work.placement_trials.values())}, '
                       f'route_expansions={work.route_expansions}')
    placed = _chambers(width, height, room_target, brief['layout'], stream, work)
    if placed is None:
        fail('separated chamber envelopes do not fit')
    rooms, envelopes = placed
    tree, edges = _edges(rooms)
    if brief['secret_rooms']:
        degrees = [0]*room_target
        for _, a, b in tree:
            degrees[a] += 1
            degrees[b] += 1
        leaves = [i for i in range(1, room_target) if degrees[i] == 1]
        secret = leaves[stream.randbelow(len(leaves))]
        rooms[secret]['secret'], rooms[secret]['required'] = True, False
    passages, doors = [], []
    def add_route(edge):
        _, a, b = edge
        routed = _route(a, b, 1+stream.randbelow(2), rooms, envelopes, passages, width, height, work)
        if routed is None:
            return False
        passage, span = routed
        passage['id'] = f'P{len(passages)+1:03d}'
        passages.append(passage)
        doors.append({'id': f'D{len(doors)+1:03d}', 'cells': _ordered(span), 'gate_id': None})
        return True
    for edge in tree:
        if not add_route(edge):
            fail(f'MST route {rooms[edge[1]]["id"]}–{rooms[edge[2]]["id"]} cannot be carved without crossing')
    tree_pairs = {(a, b) for _, a, b in tree}
    for edge in edges:
        _, a, b = edge
        if (a, b) not in tree_pairs and not rooms[a]['secret'] and not rooms[b]['secret'] and add_route(edge):
            break
    else:
        fail('no nonduplicate nonsecret chord fits')
    if not has_route_loop(rooms, passages):
        fail('nonsecret route graph has no cycle')
    floor = set().union(*(set(map(tuple, region['cells'])) for region in rooms+passages))
    entrance = rooms[0]['anchor'][:]
    distances = cell_distances(floor, entrance)
    if set(distances) != floor:
        fail('physical floor disconnected')
    required = [r for r in rooms if r['required'] and not r['secret']]
    objective = max(required, key=lambda r:(distances[tuple(r['anchor'])], r['id']))
    visible = set().union(*(set(map(tuple, r['cells'])) for r in rooms if not r['secret']))
    secret_cells = set().union(*(set(map(tuple, r['cells'])) for r in rooms+passages if r['secret'])) - visible
    return {'width': width, 'height': height,
            'rows': [''.join('.' if (x, y) in floor else '#' for x in range(width)) for y in range(height)],
            'rooms': rooms, 'passages': passages, 'doors': doors, 'gates': [],
            'entrance': entrance, 'objective_room': objective['id'],
            'secret_cells': _ordered(secret_cells), 'actual_room_count': len(rooms)}
