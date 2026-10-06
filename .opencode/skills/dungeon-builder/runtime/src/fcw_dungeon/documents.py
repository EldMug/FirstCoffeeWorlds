"""Vector Letter maps and complete, splittable offline GM reference documents."""
import json
import re
from html import escape

from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase.pdfmetrics import stringWidth, getAscentDescent
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle

from . import fonts, maps, model, rules


def map_layout(width, height):
    candidates = []
    for page in ((612,792),(792,612)):
        available_width,available_height = page[0]-72,page[1]-72-24-18
        # Outer grid stroke is max(0.15pt, 0.01*scale). Fit its ink, not merely
        # its centerline, inside the margins without clipping the frame.
        scale = min((available_width-0.15)/width, available_width/(width+0.01),
                    (available_height-0.15)/height, available_height/(height+0.01))
        candidates.append((scale,page,36+(available_width-width*scale)/2,
                           36+18+(available_height-height*scale)/2))
    # Strict comparison preserves portrait on equal fits.
    return candidates[1] if candidates[1][0]>candidates[0][0] else candidates[0]


def _map_title(text, width):
    """Fit complete title inside the fixed 24pt header, never below 6pt."""
    font = fonts.pdf_font(bold=True)
    for size in range(14,5,-1):
        remaining,lines = text,[]
        while remaining:
            end = 0
            for index in range(1,len(remaining)+1):
                if stringWidth(remaining[:index],font,size)>width:
                    break
                end = index
            if not end:
                break
            if end<len(remaining):
                boundary = max((i+1 for i,ch in enumerate(remaining[:end]) if ch.isspace()),default=0)
                # Avoid an almost-empty line followed by an oversized word.
                if boundary>=end/2:
                    end = boundary
            lines.append(remaining[:end])
            remaining = remaining[end:]
        _,descent = getAscentDescent(font,size)
        if not remaining and size+(len(lines)-1)*(size+1)-descent<=24:
            return size,lines
    raise model.ValidationIssue('E_RENDER_FIT','$.brief.name','title cannot fit 24pt header at >=6pt')


def write_map_pdf(dungeon, destination, audience):
    maps._audience(audience)
    model.validate_dungeon(dungeon)
    scene = maps.build_scene(dungeon,audience)
    maps.validate_scene_text(dungeon,audience,scene)
    geometry = dungeon['geometry']
    scale,page,left,bottom = map_layout(geometry['width'],geometry['height'])
    title_size,title_lines = _map_title(dungeon['brief']['name'],page[0]-72)
    canvas = Canvas(str(destination),pagesize=page,invariant=1,pageCompression=1)
    canvas.setTitle(dungeon['brief']['name'])
    canvas.setAuthor('')
    canvas.setSubject('')
    canvas.setKeywords('')
    def point(p):
        return left+p[0]*scale,bottom+(geometry['height']-p[1])*scale
    for primitive in scene:
        kind = primitive['kind']
        canvas.setFillColor(HexColor(primitive['color']))
        canvas.setStrokeColor(HexColor(primitive['color']))
        if kind=='rect':
            x,y,w,h = primitive['box']
            x,y = point([x,y+h])
            canvas.rect(x,y,w*scale,h*scale,stroke=0,fill=1)
        elif kind=='line':
            canvas.setLineWidth(max(0.15,primitive['width']*scale))
            for a,b in zip(primitive['points'],primitive['points'][1:]):
                canvas.line(*point(a),*point(b))
        else:
            size = max(6,min(11,scale*0.38))
            font = fonts.pdf_font(bold=primitive['bold'])
            canvas.setFont(font,size)
            x,y = point(primitive['point'])
            # White backing keeps numeric labels readable over grid/content.
            text_width = stringWidth(primitive['text'],font,size)
            canvas.setFillColor(HexColor(maps.FLOOR))
            canvas.rect(x-text_width/2-0.3,y-size*0.34-0.3,text_width+0.6,size+0.6,stroke=0,fill=1)
            canvas.setFillColor(HexColor(primitive['color']))
            canvas.drawString(x-text_width/2,y-size*0.34,primitive['text'])
    font = fonts.pdf_font(bold=True)
    canvas.setFillColor(HexColor(maps.INK))
    canvas.setFont(font,title_size)
    for index,text in enumerate(title_lines):
        canvas.drawString(36,page[1]-36-title_size-index*(title_size+1),text)
    lines = maps.legend(audience)
    font = fonts.pdf_font()
    canvas.setFont(font,6)
    for index,text in enumerate(lines):
        canvas.drawString(36,36+6*(len(lines)-1-index),text)
    canvas.showPage()
    canvas.save()


def _points(points):
    return ' '.join(f'({x},{y})' for x,y in points)


def key_blocks(dungeon):
    """Plain-text logical blocks shared by Markdown and PDF; no user XML."""
    model.validate_dungeon(dungeon)
    blocks = []
    def heading(level,text):
        blocks.append({'kind':'heading','level':level,'text':text})
    def text(value):
        blocks.append({'kind':'text','text':str(value)})
    def table(rows):
        blocks.append({'kind':'table','rows':[[str(cell) for cell in row] for row in rows]})
    brief,geometry,contents = dungeon['brief'],dungeon['geometry'],dungeon['contents']
    catalog = model._trusted_catalog()
    heading(1,brief['name'])
    text('GM room key. Numeric map label 1 = R001, 2 = R002, and so on. Coordinates are zero-based (x,y); each cell is 5 feet.')
    text(f"Grid {geometry['width']} × {geometry['height']}; {brief['layout']} / {brief['theme']}; seed {dungeon['seed']}; catalog {dungeon['catalog_digest']}.")
    text('Party roster in supplied order: '+', '.join(f'PC {i}: level {level}' for i,level in enumerate(brief['party_levels'],1)))
    text('Purpose: '+brief['purpose']+'\nTone: '+brief['tone'])
    text('Requested counts: '+json.dumps(contents['requested_counts'],sort_keys=True)+'\nActual counts: '+json.dumps(contents['actual_counts'],sort_keys=True))
    text(f"Actual coins: {sum(contents['coins'].values())} GP; actual magic items: {len(contents['treasure'])}. Hidden allocations are GM-only.")
    for fact in brief['world_context']['facts']:
        text('Supplied world context: '+fact)
    if brief['world_context']['references']:
        text('World references: '+', '.join(brief['world_context']['references']))
    heading(2,'Objective')
    text(f"{geometry['objective_room']} at {_points([contents['objective_point']])}; climax: {brief['climax']}.")
    text(contents['objective_description'])
    chamber_map = {r['id']:r for r in geometry['rooms']}
    for room in contents['rooms']:
        rid = room['room_id']
        chamber = chamber_map[rid]
        heading(2,rid+' — '+room['title'])
        text(f"Role: {room['role']}; required: {chamber['required']}; secret: {chamber['secret']}; map number {int(rid[1:])}.")
        cells = chamber['cells']
        w,h = max(p[0] for p in cells)-min(p[0] for p in cells)+1,max(p[1] for p in cells)-min(p[1] for p in cells)+1
        text(f'Envelope {w} × {h} squares ({w*5} × {h*5} feet); {len(cells)} floor cells; anchor {_points([chamber["anchor"]])}.\nCells: '+_points(cells))
        text(room['description'])
        for feature in room['features']:
            text(('Product choice: ' if feature['product_choice'] else 'Source feature: ')+feature['text'])
            if feature['source_ids']:
                text('Full source references in appendix: '+', '.join(feature['source_ids']))
        encounter = room['encounter']
        if encounter:
            heading(3,'Encounter')
            text(f"Difficulty {encounter['target_difficulty']}; exact roster budget {encounter['budget']} XP; actual base XP {encounter['xp']}; no lair variant or adjusted-XP multiplier.")
            table([['PC','Level','Budget XP']]+[[i,level,rules.encounter_budget([level,level],encounter['target_difficulty'])//2] for i,level in enumerate(brief['party_levels'],1)])
            text('Party entry reserve: '+_points(encounter['party_entry_cells']))
            for monster in encounter['monsters']:
                source = dungeon['sources'][monster['catalog_id']]
                text(f"{monster['instance_id']}: {source['name']}; {monster['catalog_id']}; selected size {monster['selected_size']}; base XP {source['metadata']['xp']}; cells {_points(monster['cells'])}.")
            for caution in encounter['cautions']:
                text('Caution: '+caution)
        trap = room['trap']
        if trap:
            heading(3,'Trap')
            source = dungeon['sources'][trap['catalog_id']]
            text(f"{trap['instance_id']}: {source['name']}; source {trap['catalog_id']}; selected source tier {trap['tier']} (lowest party member; mixed-level scaling is product choice).")
            variant = next(v for v in source['metadata']['variants'] if v['tier']==trap['tier'])
            text('Selected tier values: '+json.dumps(variant,ensure_ascii=False,sort_keys=True))
            text('Trigger: '+source['metadata']['trigger_text']+'\nTrigger cells: '+_points(trap['trigger_cells']))
            text('Effect cells: '+_points(trap['effect_cells'])+'\nSafe bypass in order: '+_points(trap['bypass_path']))
            text('Escape aid: '+(trap['escape_aid'] or 'None; use the source escape wording below.')+'\nReset: '+source['metadata']['reset_text'])
            text('Complete operating wording (base wording and higher-level instructions unchanged; apply selected-tier values above):\n'+source['rules_text'])
        heading(3,'Rewards and gates')
        text(f"Coins: {contents['coins'].get(rid,0)} GP.")
        for item in contents['treasure']:
            if item['room_id']==rid:
                entry = dungeon['sources'][item['catalog_id']]
                text(f"{item['instance_id']}: {entry['name']}; {item['catalog_id']}; actual rarity {entry['metadata']['rarity']}; hidden: {item['hidden']}. Full source effects: appendix {entry['id']}.")
        local = [g['id'] for g in geometry['gates'] if g['prerequisite_room']==rid]
        text('Gate prerequisites here: '+(', '.join(local) if local else 'none'))
    heading(2,'Passages, doors and gate solutions')
    for passage in geometry['passages']:
        text(f"{passage['id']}: {passage['from_room']} to {passage['to_room']}; width {passage['width']}; secret: {passage['secret']}; cells {_points(passage['cells'])}.")
    for door in geometry['doors']:
        text(f"{door['id']}: span {_points(door['cells'])}; gate {door['gate_id'] or 'none (ordinary door)'}.")
    for gate in geometry['gates']:
        heading(3,gate['id']+' — '+gate['mechanism'])
        text(f"Door {gate['door_id']}; prerequisite {gate['prerequisite_id']} in {gate['prerequisite_room']} at {_points([gate['prerequisite_point']])}.")
        text('Clue: '+gate['clue']+'\nSolution: '+gate['solution'])
    heading(2,'Proposed-only lore')
    text('These proposals are not approved world definitions.')
    if not dungeon['lore_proposals']:
        text('No lore proposals.')
    for proposal in dungeon['lore_proposals']:
        text(proposal['id']+' — status: proposed\n'+proposal['text']+'\nRelated mentions: '+', '.join(proposal['related_links']))
    heading(2,'Complete source dependency appendices')
    text('Every selected entry and operative dependency follows once by ID. Creature dependency cycles are deduplicated. Source bodies are complete, not URL-only summaries. Placement and chosen tier overlays above do not alter source wording.')
    for sid,entry in sorted(dungeon['sources'].items()):
        heading(3,sid+' — '+entry['name'])
        text('Kind: '+entry['kind']+'\nDependencies: '+(', '.join(entry['dependencies']) or 'none'))
        metadata = entry['metadata']
        if entry['kind']=='monster':
            table([['Ability','Score','Modifier','Save']]+[[ability.upper(),metadata['abilities'][ability]['score'],f"{metadata['abilities'][ability]['modifier']:+d}",f"{metadata['abilities'][ability]['save']:+d}"] for ability in ('str','dex','con','int','wis','cha')])
        if metadata:
            text('Source metadata: '+json.dumps(metadata,ensure_ascii=False,sort_keys=True))
        text(entry['rules_text'])
        source = entry['source']
        text(f"Source: {source['edition']}; SRD pages {', '.join(map(str,source['pages']))}.\nPDF: {source['pdf_url']}\nPDF SHA256: {source['pdf_sha256']}\nBasic Rules citation: {source['basic_rules_url']}\nText SHA256: {source['text_sha256']}")
    heading(2,'Attribution')
    text(catalog['attribution'].strip())
    validate_blocks(blocks)
    return blocks


def validate_blocks(blocks):
    fonts.font_paths()
    for index,block in enumerate(blocks):
        values = [block['text']] if block['kind']!='table' else [cell for row in block['rows'] for cell in row]
        for text in values:
            fonts.validate_text(text,bold=block['kind']=='heading',path=f'$.document.blocks[{index}]')


def _markdown(text):
    # Match CommonMark line boundaries before Python's LF-only multiline ^.
    # This string copy changes presentation, never saved canonical field values.
    text = text.replace('\r\n','\n').replace('\r','\n')
    # Inline escapes alone do not neutralize block indentation. Encode leading
    # whitespace after escaping syntax so CommonMark sees prose, then decodes the
    # original spaces/tabs and literal markup in that prose context.
    text = escape(text,quote=False)
    text = re.sub(r'([\\`*_{}\[\]()#+.!|>~=\-])',r'\\\1',text)
    return re.sub(r'^[ \t]+',lambda m: ''.join('&#32;' if ch==' ' else '&#9;' for ch in m[0]),text,flags=re.MULTILINE)


def room_key_markdown(dungeon):
    output = []
    for block in key_blocks(dungeon):
        if block['kind']=='heading':
            # Display names may legally contain link mentions. Keep that text
            # as prose rather than exporting an unapproved Markdown definition.
            prefix = '' if '@' in block['text'] else '#'*block['level']+' '
            output.append(prefix+_markdown(block['text']))
        elif block['kind']=='table':
            rows = block['rows']
            output.append('\n'.join(['| '+' | '.join(_markdown(c) for c in rows[0])+' |',
                                      '| '+' | '.join('---' for _ in rows[0])+' |']+
                                     ['| '+' | '.join(_markdown(c) for c in row)+' |' for row in rows[1:]]))
        else:
            output.append(_markdown(block['text']))
    return '\n\n'.join(output)+'\n'


def _paragraphs(text):
    # Keep all source tokens, while limiting paragraph layout complexity. Split
    # at whitespace only; never truncate a long stat block or keep it together.
    for paragraph in text.split('\n'):
        while len(paragraph)>1800:
            end = paragraph.rfind(' ',0,1800)
            if end<=0:
                break
            yield paragraph[:end]
            paragraph = paragraph[end+1:]
        yield paragraph


def write_room_key_pdf(dungeon, destination):
    blocks = key_blocks(dungeon)
    body = ParagraphStyle('FCWBody',fontName=fonts.pdf_font(),fontSize=10,leading=13,spaceAfter=6,
                          splitLongWords=1,allowWidows=1,allowOrphans=1)
    heading = ParagraphStyle('FCWHeading',fontName=fonts.pdf_font(bold=True),fontSize=14,leading=18,
                             spaceBefore=10,spaceAfter=7,keepWithNext=True)
    story = []
    for block in blocks:
        if block['kind']=='heading':
            story.append(Paragraph(escape(block['text']),heading))
        elif block['kind']=='table':
            rows = [[Paragraph(escape(cell),body) for cell in row] for row in block['rows']]
            table = Table(rows,colWidths=[540/len(rows[0])]*len(rows[0]),repeatRows=1,hAlign='LEFT',splitByRow=1,splitInRow=1)
            table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),HexColor('#e4e8ec')),
                                       ('GRID',(0,0),(-1,-1),0.3,HexColor('#8f979b')),
                                       ('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),5),
                                       ('RIGHTPADDING',(0,0),(-1,-1),5)]))
            story.extend([table,Spacer(1,6)])
        else:
            story.extend(Paragraph(escape(text),body) for text in _paragraphs(block['text']) if text)
    def page_footer(canvas,document):
        canvas.saveState()
        canvas.setFont(fonts.pdf_font(),10)
        canvas.drawString(36,36,f'Page {document.page}')
        canvas.restoreState()
    document = BaseDocTemplate(str(destination),pagesize=(612,792),leftMargin=36,rightMargin=36,
                               topMargin=36,bottomMargin=36,title=dungeon['brief']['name'],author='',
                               invariant=1,pageCompression=1)
    # Footer occupies 18 points inside the mandatory bottom margin envelope.
    frame = Frame(36,54,540,702,leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)
    document.addPageTemplates(PageTemplate(id='portrait',frames=[frame],onPage=page_footer))
    document.build(story)
