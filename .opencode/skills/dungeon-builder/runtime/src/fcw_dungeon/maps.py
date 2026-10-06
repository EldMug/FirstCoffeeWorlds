"""Audience-safe cell-coordinate scenes, rendered directly as raster or vector."""
from copy import deepcopy

from PIL import Image, ImageDraw
from reportlab.pdfbase.pdfmetrics import stringWidth

from . import fonts, model

ROCK = '#37414b'
FLOOR = '#f5f2e8'
WALL = '#20252b'
GRID = '#8f979b'
INK = '#18232b'
MAX_PIXELS = 32_000_000


def player_projection(dungeon):
    """Closed, detached DTO. Input geometry must have its validated secret mask.

    Export entry points perform full canonical validation before using this pure
    projection. No private field or private door identity crosses the boundary.
    """
    geometry = dungeon['geometry']
    private = set(map(tuple, geometry['secret_cells']))
    rows = [''.join('#' if (x,y) in private else ch for x,ch in enumerate(row))
            for y,row in enumerate(geometry['rows'])]
    doors = [{'cells': deepcopy(door['cells'])} for door in geometry['doors']
             if not private.intersection(map(tuple, door['cells']))]
    return {'name': dungeon['brief']['name'], 'width': geometry['width'], 'height': geometry['height'],
            'feet_per_square': dungeon['brief']['grid']['feet_per_square'], 'rows': rows,
            'doors': doors, 'entrance': list(geometry['entrance'])}


def _audience(audience):
    if audience not in ('gm','player'):
        raise model.ValidationIssue('E_AUDIENCE', '$.audience', 'audience must be gm or player')


def build_scene(dungeon, audience):
    """List of rect/line/text primitives in top-left-origin grid units.

    Fields: kind, role, color; rect: box; line: points,width;
    text: point,text,bold. GM keyed primitives additionally carry id.
    Scenes contain only projected geometry for player audiences.
    """
    _audience(audience)
    geometry = player_projection(dungeon) if audience=='player' else dungeon['geometry']
    width, height = geometry['width'], geometry['height']
    scene = [{'kind':'rect','role':'rock','box':[0,0,width,height],'color':ROCK}]
    floor = {(x,y) for y,row in enumerate(geometry['rows']) for x,ch in enumerate(row) if ch=='.'}
    def rect(role, box, color, **extra):
        scene.append(dict(kind='rect',role=role,box=box,color=color,**extra))
    def line(role, points, color, thickness=0.025, **extra):
        scene.append(dict(kind='line',role=role,points=points,color=color,width=thickness,**extra))
    def label(role, point, text, color=INK, **extra):
        scene.append(dict(kind='text',role=role,point=point,text=text,color=color,bold=True,**extra))
    for x,y in sorted(floor,key=lambda p:(p[1],p[0])):
        rect('floor',[x,y,1,1],FLOOR)
    # Grid across the whole visible extent, including unremarkable rock.
    for x in range(width+1):
        line('grid',[[x,0],[x,height]],GRID,0.01)
    for y in range(height+1):
        line('grid',[[0,y],[width,y]],GRID,0.01)
    # Derive walls ONLY after private floor removal. Never reuse GM outlines.
    for x,y in sorted(floor,key=lambda p:(p[1],p[0])):
        for neighbor,edge in (((x,y-1),[[x,y],[x+1,y]]),((x+1,y),[[x+1,y],[x+1,y+1]]),
                              ((x,y+1),[[x,y+1],[x+1,y+1]]),((x-1,y),[[x,y],[x,y+1]])):
            if neighbor not in floor:
                line('wall',edge,WALL,0.06)
    for door in geometry['doors']:
        cells = door['cells']
        x0,y0 = min(p[0] for p in cells),min(p[1] for p in cells)
        x1,y1 = max(p[0] for p in cells)+1,max(p[1] for p in cells)+1
        # Orientation comes from adjacent rock, not gate/reveal metadata.
        vertical = x1-x0 < y1-y0 or (x1-x0==y1-y0 and (x0,y0-1) not in floor and (x0,y1) not in floor)
        box = [(x0+x1)/2-0.12,y0+0.08,0.24,y1-y0-0.16] if vertical else [x0+0.08,(y0+y1)/2-0.12,x1-x0-0.16,0.24]
        rect('door',box,'#a17640', **({'id':door['id']} if audience=='gm' else {}))
    x,y = geometry['entrance']
    label('entrance',[x+0.5,y+0.5],'E','#145d42')
    if audience=='player':
        return scene
    for room in dungeon['contents']['rooms']:
        encounter,trap = room['encounter'],room['trap']
        if encounter:
            for monster in encounter['monsters']:
                for x,y in monster['cells']:
                    rect('monster',[x+0.18,y+0.18,0.64,0.64],'#ba4b46',id=monster['instance_id'])
        if trap:
            for x,y in trap['effect_cells']:
                rect('effect',[x+0.1,y+0.1,0.8,0.8],'#e5c789',id=trap['instance_id'])
            for x,y in trap['trigger_cells']:
                label('trigger',[x+0.5,y+0.5],'!', '#994000',id=trap['instance_id'])
            points = [[x+0.5,y+0.5] for x,y in trap['bypass_path']]
            if len(points)>1:
                line('bypass',points,'#278065',0.05,id=trap['instance_id'])
    chambers = {r['id']:r for r in geometry['rooms']}
    rewards = {t['room_id'] for t in dungeon['contents']['treasure']}
    rewards.update(rid for rid,amount in dungeon['contents']['coins'].items() if amount)
    for rid in sorted(rewards):
        room = chambers[rid]
        x,y = max(room['cells'],key=lambda p:(p[1],p[0]))
        label('treasure',[x+0.5,y+0.5],'T','#875000',id=rid)
    doors = {d['id']:d for d in geometry['doors']}
    for gate in geometry['gates']:
        x,y = doors[gate['door_id']]['cells'][0]
        label('gate',[x+0.5,y+0.5],'G', '#473995',id=gate['id'])
        x,y = gate['prerequisite_point']
        label('control',[x+0.5,y+0.5],'K' if gate['mechanism']=='key_lock' else 'C','#473995',id=gate['prerequisite_id'])
    x,y = dungeon['contents']['objective_point']
    label('objective',[x+0.5,y+0.5],'X','#145d42')
    if dungeon['brief']['climax']:
        room = chambers[geometry['objective_room']]
        x,y = room['anchor']
        label('climax',[x+0.5,y+1.25],'HIGH','#a51f2b',id=room['id'])
    for room in geometry['rooms']:
        x,y = room['anchor']
        label('room',[x+0.5,y+0.5],str(int(room['id'][1:])),id=room['id'])
    return _place_labels(scene,geometry)


def _place_labels(scene, geometry):
    """Keep annotations inside floor and legible without erasing other labels.

    Actual mechanical locations never move. A displaced annotation has location
    plus a vector leader to that location; room numbers choose free chamber space.
    All fills precede labels. Bounds cover minimum printable sizes and 16px PNGs.
    """
    width,height = geometry['width'],geometry['height']
    scale = max(min(540/width,678/height),min(720/width,498/height))
    relative_size = max(0.5,max(6,min(11,scale*0.38))/scale)
    occupied,leaders,labels = [],[],[]
    floor = {(x,y) for y,row in enumerate(geometry['rows']) for x,ch in enumerate(row) if ch=='.'}
    rooms = {r['id']:set(map(tuple,r['cells'])) for r in geometry['rooms']}
    def bounds(label,point):
        half = stringWidth(label['text'],fonts.pdf_font(bold=label['bold']),relative_size)/2+0.05
        x,y = point
        return x-half,y-relative_size*0.66-0.05,x+half,y+relative_size*0.34+0.05
    for label in (p for p in scene if p['kind']=='text'):
        original = label['point']
        room = rooms.get(label.get('id')) if label['role']=='room' else next((cells for cells in rooms.values() if (int(original[0]),int(original[1])) in cells),None)
        allowed = room if room is not None else floor
        candidates = [original]+[[x+0.5,y+0.5] for x,y in sorted(allowed,key=lambda p:((p[0]+0.5-original[0])**2+(p[1]+0.5-original[1])**2,p[1],p[0]))]
        for point in candidates:
            box = bounds(label,point)
            if not all((int(x),int(y)) in allowed for x,y in ((box[0],box[1]),(box[2],box[1]),(box[0],box[3]),(box[2],box[3]))):
                continue
            if any(box[0]<b[2] and b[0]<box[2] and box[1]<b[3] and b[1]<box[3] for b in occupied):
                continue
            if point!=original and label['role']!='room':
                label['location'] = original
                leaders.append({'kind':'line','role':'leader','points':[original,point],'color':label['color'],'width':0.025})
            label['point'] = point
            occupied.append(box)
            labels.append(label)
            break
        else:
            raise model.ValidationIssue('E_RENDER_FIT','$.scene','cannot place printable annotation: '+label['role'])
    return [p for p in scene if p['kind']!='text']+leaders+labels


def legend(audience):
    _audience(audience)
    common = '5 feet per square • E entrance • brown door'
    if audience=='player':
        return [common]
    return [common+' • numbers = R001 IDs',
            'red monster • ! trigger • tan effect • green bypass • T treasure',
            'G gate • K key / C control or reveal • X objective • HIGH climax']


def raster_size(dungeon, pixels_per_square=None):
    px = dungeon['brief']['pixels_per_square'] if pixels_per_square is None else pixels_per_square
    if type(px) is not int or not 16<=px<=128:
        raise model.ValidationIssue('E_PIXELS','$.pixels_per_square','pixels per square must be an integer from 16 to 128')
    geometry = dungeon['geometry']
    size = ((geometry['width']+2)*px,(geometry['height']+3)*px)
    if size[0]*size[1]>MAX_PIXELS:
        raise model.ValidationIssue('E_IMAGE_SIZE','$.pixels_per_square',
            f'{size[0]}x{size[1]} exceeds 32,000,000 pixels; request a smaller pixels_per_square (for example 64)')
    return px,size


def validate_scene_text(dungeon, audience, scene):
    fonts.font_paths()
    fonts.validate_text(dungeon['brief']['name'],bold=True,path='$.brief.name')
    for primitive in scene:
        if primitive['kind']=='text':
            fonts.validate_text(primitive['text'],bold=primitive['bold'])
    for text in legend(audience):
        fonts.validate_text(text)


def write_png(dungeon, destination, audience, *, pixels_per_square=None):
    _audience(audience)
    model.validate_dungeon(dungeon)
    px,size = raster_size(dungeon,pixels_per_square)
    scene = build_scene(dungeon,audience)
    validate_scene_text(dungeon,audience,scene)
    image = Image.new('RGB',size,'#ffffff')
    draw = ImageDraw.Draw(image)
    def point(p):
        return ((p[0]+1)*px,(p[1]+2)*px)
    for primitive in scene:
        kind,color = primitive['kind'],primitive['color']
        if kind=='rect':
            x,y,w,h = primitive['box']
            left,top = point([x,y])
            right,bottom = point([x+w,y+h])
            draw.rectangle([left,top,right-1,bottom-1],fill=color)
        elif kind=='line':
            draw.line([point(p) for p in primitive['points']],fill=color,width=max(1,round(primitive['width']*px)))
        else:
            font = fonts.pillow_font(max(8,round(px*0.38)),bold=primitive['bold'])
            draw.text(point(primitive['point']),primitive['text'],font=font,fill=color,anchor='mm',stroke_width=1,stroke_fill=FLOOR)
    title = dungeon['brief']['name']
    size_title = max(9,round(px*0.6))
    while draw.textlength(title,font=fonts.pillow_font(size_title,bold=True))>image.width-2*px:
        size_title -= 1
    draw.text((px,px),title,font=fonts.pillow_font(size_title,bold=True),fill=INK,anchor='lm')
    lines = legend(audience)
    font_size = max(5,round(px*0.2))
    while max(draw.textlength(t,font=fonts.pillow_font(font_size)) for t in lines)>image.width-2*px:
        font_size -= 1
    for index,text in enumerate(lines):
        y = (dungeon['geometry']['height']+2)*px+(index+0.5)*px/len(lines)
        draw.text((px,y),text,font=fonts.pillow_font(font_size),fill=INK,anchor='lm')
    image.save(destination,format='PNG')
    image.close()
