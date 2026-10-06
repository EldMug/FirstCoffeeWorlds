"""Pure canonical generation and stage-only exports; publication is separate."""
import secrets

from . import content, layout, model, progression, rules


def generate_dungeon(brief, seed, catalog):
    """Resolve seed once; at most 50 COMPLETE attempts across all auto targets."""
    brief = model.normalize_brief(brief)
    try:
        model.validate_schema(catalog,'catalog')
    except model.ValidationIssue as error:
        raise rules.CatalogIssue(error.code,'$.catalog'+error.path[1:],str(error)) from None
    if catalog != model._trusted_catalog():
        raise rules.CatalogIssue('E_CATALOG_DIGEST','$.catalog','supplied catalog differs from verified packaged v1 resources')
    effective_seed = seed if seed is not None else brief['seed']
    if effective_seed is None:
        effective_seed = secrets.randbits(64)
    brief['seed'] = effective_seed
    brief = model.normalize_brief(brief)
    original = model.room_target(brief)
    last = None
    for attempt in range(50):
        target = original if brief['room_count'] is not None or attempt<10 else max(4,original-max(0,(attempt-8)//2))
        try:
            geometry = layout.generate_layout(brief,effective_seed,attempt,target)
            contents = content.populate_rooms(geometry,brief,effective_seed,catalog)
            geometry['objective_room'] = next(r['id'] for r in geometry['rooms'] if contents['objective_point'] in r['cells'])
            hazards = [p for r in contents['rooms'] if r['trap'] for p in r['trap']['trigger_cells']]
            geometry = progression.place_gates(geometry,brief,effective_seed,hazards)
            # Prerequisites selected after traps must join their room's explicit
            # safe path, not merely happen to avoid one trigger square.
            for room in contents['rooms']:
                controls = [g['prerequisite_point'] for g in geometry['gates'] if g['prerequisite_room']==room['room_id']]
                for gate in geometry['gates']:
                    if gate['prerequisite_room']==room['room_id']:
                        room['features'].append({'text':gate['clue']+' '+gate['solution'],'source_ids':[],'product_choice':True})
                if room['trap']:
                    chamber = next(r for r in geometry['rooms'] if r['id']==room['room_id'])
                    safe = set(map(tuple,chamber['cells']))-set(map(tuple,room['trap']['trigger_cells']))
                    required = progression.room_entries(geometry,chamber)+[chamber['anchor']]+controls
                    if contents['objective_point'] in chamber['cells']:
                        required.append(contents['objective_point'])
                    path = content._bypass(safe,required)
                    if path is None:
                        raise layout.FitIssue('E_TRAP_FIT','$.contents.rooms','gate control cannot join simple safe bypass')
                    room['trap']['bypass_path'] = path
            roots = {t['catalog_id'] for t in contents['treasure']}
            for room in contents['rooms']:
                if room['encounter']:
                    roots.update(m['catalog_id'] for m in room['encounter']['monsters'])
                if room['trap']:
                    roots.add(room['trap']['catalog_id'])
                roots.update(sid for f in room['features'] for sid in f['source_ids'])
            dungeon = {'schema_version':1,'generator_version':1,'catalog_version':catalog['catalog_version'],
                       'catalog_digest':catalog['digest'],'seed':effective_seed,'brief':brief,'geometry':geometry,
                       'contents':contents,'sources':rules.snapshot_sources(roots,catalog=catalog),'lore_proposals':[]}
            model.validate_dungeon(dungeon)
            return dungeon
        except layout.FitIssue as error:
            last = error
    raise layout.FitIssue('E_LAYOUT_FIT','$.geometry',
        f'{brief["grid"]["width"]}x{brief["grid"]["height"]}, requested count={original}; '
        f'50 complete attempts exhausted; last constraint: {last}')


def write_staged_bundle(dungeon, destination, context):
    """Write eight data/export files only; return digests and closed context.

    Destination must be absent with an existing parent, or an empty directory.
    Revision/parent/manifest assignment belongs to the later locked publisher.
    """
    import hashlib
    import json
    import re
    from pathlib import Path
    from . import documents, maps

    if (type(context) is not dict or set(context)!={'operation','input_digest'} or
            context['operation'] not in ('generate','render','regenerate') or
            type(context['input_digest']) is not str or re.fullmatch('[0-9a-f]{64}',context['input_digest']) is None):
        raise model.ValidationIssue('E_CONTEXT','$.context','expected exactly operation (generate/render/regenerate) and lowercase SHA256 input_digest')
    model.validate_dungeon(dungeon)
    # Preflight all rendered strings and allocation bounds before writing files.
    maps.raster_size(dungeon)
    markdown = documents.room_key_markdown(dungeon)
    for audience in ('gm','player'):
        maps.validate_scene_text(dungeon,audience,maps.build_scene(dungeon,audience))
    destination = Path(destination)
    if destination.is_symlink() or (destination.exists() and (not destination.is_dir() or any(destination.iterdir()))):
        raise FileExistsError(f'stage destination must be empty: {destination}')
    created = not destination.exists()
    if created:
        destination.mkdir()
    names = ('brief.json','dungeon.json','map-gm.png','map-player.png','map-gm.pdf','map-player.pdf','room-key.md','room-key.pdf')
    try:
        for name,value in (('brief.json',dungeon['brief']),('dungeon.json',dungeon)):
            (destination/name).write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        for audience in ('gm','player'):
            maps.write_png(dungeon,destination/f'map-{audience}.png',audience)
            documents.write_map_pdf(dungeon,destination/f'map-{audience}.pdf',audience)
        (destination/'room-key.md').write_text(markdown,encoding='utf-8')
        documents.write_room_key_pdf(dungeon,destination/'room-key.pdf')
        artifacts = {name:hashlib.sha256((destination/name).read_bytes()).hexdigest() for name in names}
        return dict(context,artifacts=artifacts)
    except Exception:
        # Only fixed files in this initially empty, owned stage are ours.
        for name in names:
            (destination/name).unlink(missing_ok=True)
        if created:
            destination.rmdir()
        raise
