"""Linux durable, single-writer publication with conservative restart recovery.

Individual renames are atomic; the bundle is not. The journal defines whether
restart restores the old state or retains a verified committed publication.
All live paths are derived from a validated slug, never manifest filenames.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import html
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import re
import stat
import tempfile
import uuid

from . import bundle, fonts, model

ARTIFACTS = ('brief.json', 'dungeon.json', 'map-gm.png', 'map-player.png',
             'map-gm.pdf', 'map-player.pdf', 'room-key.md', 'room-key.pdf')
FILES = ARTIFACTS + ('manifest.json',)
START = b'<!-- fcw-dungeon:catalog:start -->'
END = b'<!-- fcw-dungeon:catalog:end -->'
PHASES = ('PREPARED', 'ARCHIVED', 'PUBLISHING', 'CATALOG_WRITTEN', 'COMMITTED')


class PublishIssue(OSError):
    def __init__(self, code, path, context):
        self.code, self.path, self.context = code, str(path), context
        super().__init__(f'{code} {self.path}: {context}')


def canonical_digest(value):
    return _hash(json.dumps(value, ensure_ascii=False, sort_keys=True,
                            separators=(',', ':'), allow_nan=False).encode('utf-8'))


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _slug(value):
    if type(value) is not str or len(value) > 64 or re.fullmatch('[a-z0-9]+(-[a-z0-9]+)*', value) is None:
        raise PublishIssue('E_PATH', '$.slug', 'expected safe nonempty folder slug')
    return value


def _root(value):
    path = Path(os.path.abspath(value))
    if path.is_symlink() or not path.is_dir():
        raise PublishIssue('E_ROOT', path, 'root must be an existing nonsymlink directory')
    return path.resolve()


def _safe(root, path):
    path = Path(os.path.abspath(path))
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        raise PublishIssue('E_PATH', path, 'path must be contained in root') from None
    current = root
    for part in parts:
        current /= part
        if current.is_symlink():
            raise PublishIssue('E_PATH', current, 'symlinks are not publication paths')
        if current.exists() and current != path and not current.is_dir():
            raise PublishIssue('E_PATH', current, 'parent must be a directory')
    return path


def _bytes(root, path):
    path = _safe(root, path)
    if not path.exists():
        return None
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise PublishIssue('E_PATH', path, 'expected regular file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            return stream.read()
    finally:
        os.close(fd)


def _state(root, path):
    data = _bytes(root, path)
    return None if data is None else _hash(data)


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise PublishIssue('E_PATH', path, 'scratch must be an unlinked-to-elsewhere regular file')
        os.ftruncate(stream.fileno(), 0)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()


def _checkpoint(label):
    """Private instrumentation seam; production has no crash flag/environment switch."""


@contextmanager
def _locked(root):
    path = _safe(root, root/'.fcw-dungeon.lock')
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise PublishIssue('E_PATH', path, 'lock must be a regular file')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PublishIssue('E_LOCKED', path, 'another publisher holds root lock') from None
        os.fsync(fd)
        _sync_dir(root)
        yield
    finally:
        os.close(fd)  # The permanent lock inode is deliberately never unlinked.


def _features(dungeon):
    brief = dungeon['brief']
    levels = brief['party_levels']
    return {'grid': brief['grid'], 'level_min': min(levels), 'level_max': max(levels),
            'party_levels': levels, 'layout': brief['layout'], 'theme': brief['theme'],
            'purpose': brief['purpose'], 'seed': dungeon['seed']}


def _manifest(dungeon, info, revision, parent):
    paths = fonts.font_paths()
    result = {'schema_version': 1, 'slug': dungeon['brief']['slug'], 'name': dungeon['brief']['name'],
              'revision': revision, 'operation': info['operation'],
              'created_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
              'input_digest': info['input_digest'], 'parent_digest': parent,
              'catalog_digest': dungeon['catalog_digest'], 'generator_version': dungeon['generator_version'],
              'runtime': {'python': platform.python_version(), 'pillow': version('Pillow'),
                          'reportlab': version('reportlab'), 'jsonschema': version('jsonschema'),
                          'font_digests': {p.name: _hash(p.read_bytes()) for p in paths.values()}},
              'artifacts': dict(info['artifacts']), 'features': _features(dungeon)}
    model.validate_schema(result, 'manifest')
    return result


def _read_json(data):
    # Reuse strict parsers for bounded JSON/type validation, but retain authored
    # Unicode/line endings in canonical data rather than normalizing a copy.
    model.parse_document(data)
    return json.loads(data)


def _verify_bundle(root, directory, slug, *, exact=False):
    data = _bytes(root, directory/'manifest.json')
    if data is None:
        raise PublishIssue('E_MANIFEST', directory, 'missing managed manifest')
    try:
        manifest = _read_json(data)
        model.validate_schema(manifest, 'manifest')
        if manifest['slug'] != slug:
            raise PublishIssue('E_IDENTITY', directory, 'manifest slug differs from folder')
        for name in ARTIFACTS:
            if _state(root, directory/name) != manifest['artifacts'][name]:
                raise PublishIssue('E_CURRENT_CHANGED', directory/name, 'artifact differs from manifest; preserve edits and render a copy')
        dungeon = _read_json(_bytes(root, directory/'dungeon.json'))
        model.validate_dungeon(dungeon)
        brief = _read_json(_bytes(root, directory/'brief.json'))
        if (canonical_digest(brief) != canonical_digest(dungeon['brief']) or
                manifest['name'] != brief['name'] or manifest['slug'] != brief['slug'] or
                canonical_digest(manifest['features']) != canonical_digest(_features(dungeon)) or
                manifest['catalog_digest'] != dungeon['catalog_digest'] or
                manifest['generator_version'] != dungeon['generator_version'] or
                manifest['input_digest'] != canonical_digest(dungeon if manifest['operation'] == 'render' else brief)):
            raise PublishIssue('E_MANIFEST', directory, 'manifest/brief/canonical identity or features disagree')
        if exact and set(p.name for p in directory.iterdir()) != set(FILES):
            raise PublishIssue('E_MANIFEST', directory, 'archive must contain exactly nine files')
        return manifest, dungeon
    except model.ValidationIssue as error:
        raise PublishIssue('E_MANIFEST', directory, str(error)) from None


def _verify_history(root, current, manifest):
    history = _safe(root, current/'revisions')
    found = set()
    parents = {manifest['revision']: manifest['parent_digest']}
    digests = {}
    if history.exists():
        if not history.is_dir():
            raise PublishIssue('E_HISTORY', history, 'history must be a directory')
        for child in history.iterdir():
            _safe(root, child)
            if re.fullmatch('r[0-9]{4,}', child.name):
                if not child.is_dir():
                    raise PublishIssue('E_HISTORY', child, 'archive must be a directory')
                old, dungeon = _verify_bundle(root, child, manifest['slug'], exact=True)
                if old['revision'] != int(child.name[1:]) or old['name'] != manifest['name']:
                    raise PublishIssue('E_HISTORY', child, 'archive identity/revision disagrees')
                found.add(child.name)
                parents[old['revision']] = old['parent_digest']
                digests[old['revision']] = canonical_digest(dungeon)
    numbers = sorted(int(name[1:]) for name in found)
    if (len(numbers) != manifest['revision']-1 or
            any(number != index for index, number in enumerate(numbers, 1))):
        raise PublishIssue('E_HISTORY', history, 'archive sequence differs from current revision')
    for revision, parent in parents.items():
        if parent != digests.get(revision-1):
            raise PublishIssue('E_HISTORY', history, 'canonical parent digest chain disagrees')


def _catalog_bounds(data, *, allow_absent=False):
    starts, ends = data.count(START), data.count(END)
    if allow_absent and starts == ends == 0:
        return None
    if starts != 1 or ends != 1 or data.index(END) < data.index(START):
        raise PublishIssue('E_CATALOG', 'dungeons/Dungeons.md', 'expected exactly one ordered managed marker pair')
    return data.index(START), data.index(END)+len(END)


def _catalog(root, old_bytes, candidate):
    try:
        if old_bytes is not None:
            old_bytes.decode('utf-8')
    except UnicodeError:
        raise PublishIssue('E_CATALOG', 'dungeons/Dungeons.md', 'catalog must be UTF-8') from None
    manifests = {}
    folder = _safe(root, root/'dungeons')
    if folder.exists():
        if not folder.is_dir():
            raise PublishIssue('E_PATH', folder, 'dungeons must be a directory')
        for child in folder.iterdir():
            if child.is_symlink():
                raise PublishIssue('E_PATH', child, 'symlink in publication directory')
            if child.is_dir() and (child/'manifest.json').exists():
                _slug(child.name)
                manifest, _ = _verify_bundle(root, child, child.name)
                _verify_history(root, child, manifest)
                manifests[child.name] = manifest
    manifests[candidate['slug']] = candidate
    names = [m['name'] for m in manifests.values()]
    if len(names) != len(set(names)):
        raise PublishIssue('E_IDENTITY', folder, 'same display name has multiple managed slugs')
    def cell(value):
        return html.escape(str(value), quote=False).replace('\\', '\\\\').replace('|', '\\|').replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' ')
    lines = ['| Name | Size | Levels | Party | Style/Theme | Purpose | Seed | Revision | Current | History |',
             '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for slug, manifest in sorted(manifests.items()):
        f = manifest['features']
        levels = str(f['level_min']) if f['level_min'] == f['level_max'] else f'{f["level_min"]}–{f["level_max"]}'
        values = [manifest['name'], f'{f["grid"]["width"]}×{f["grid"]["height"]}', levels,
                  f'{len(f["party_levels"])}: '+', '.join(map(str, f['party_levels'])),
                  f'{f["layout"]}/{f["theme"]}', f['purpose'], f['seed'], manifest['revision'],
                  f'[Current]({slug}/dungeon.json)', f'[History]({slug}/revisions/)' if manifest['revision'] > 1 else '—']
        lines.append('| '+' | '.join(map(cell, values))+' |')
    region = START+b'\n'+'\n'.join(lines).encode()+b'\n'+END
    old_bytes = b'# Dungeons\n\n' if old_bytes is None else old_bytes
    bounds = _catalog_bounds(old_bytes, allow_absent=True)
    if bounds is None:
        return old_bytes+(b'' if old_bytes.endswith(b'\n') else b'\n')+region+b'\n'
    begin, end = bounds
    return old_bytes[:begin]+region+old_bytes[end:]


def _validate_stage(root, slug, stage, info):
    stage = _safe(root, stage)
    if (stage == root or root/'dungeons' in (stage, *stage.parents) or
            root/'.fcw-dungeon-txn' in (stage, *stage.parents) or not stage.is_dir()):
        raise PublishIssue('E_STAGE', stage, 'stage must be a separate contained directory')
    if set(p.name for p in stage.iterdir()) != set(ARTIFACTS):
        raise PublishIssue('E_STAGE', stage, 'stage must contain exactly eight artifacts')
    if (type(info) is not dict or set(info) != {'operation', 'input_digest', 'artifacts'} or
            info['operation'] not in ('generate', 'render', 'regenerate') or
            type(info['input_digest']) is not str or re.fullmatch('[0-9a-f]{64}', info['input_digest']) is None or
            type(info['artifacts']) is not dict or set(info['artifacts']) != set(ARTIFACTS)):
        raise PublishIssue('E_STAGE', stage, 'invalid closed stage metadata')
    for name in ARTIFACTS:
        if _state(root, stage/name) != info['artifacts'][name]:
            raise PublishIssue('E_STAGE', stage/name, 'stage hash mismatch')
    try:
        dungeon = _read_json(_bytes(root, stage/'dungeon.json'))
        model.validate_dungeon(dungeon)
        brief = _read_json(_bytes(root, stage/'brief.json'))
        if brief['slug'] != slug or canonical_digest(brief) != canonical_digest(dungeon['brief']):
            raise PublishIssue('E_STAGE', stage, 'brief/slug/canonical data disagree')
        expected = canonical_digest(dungeon if info['operation'] == 'render' else brief)
        if info['input_digest'] != expected:
            raise PublishIssue('E_STAGE', stage, 'input digest differs from effective canonical input')
        # Rehashed unrelated exports are not sufficient proof of consistency.
        # Render from supplied data, never replay its seed (authored edits survive).
        with tempfile.TemporaryDirectory(prefix='.fcw-dungeon-stage-check-', dir=root) as directory:
            checked = bundle.write_staged_bundle(dungeon, Path(directory),
                                                {k: info[k] for k in ('operation', 'input_digest')})
            if checked['artifacts'] != info['artifacts']:
                raise PublishIssue('E_STAGE', stage, 'exports differ from canonical rendering')
        return stage, dungeon
    except model.ValidationIssue as error:
        raise PublishIssue('E_STAGE', stage, str(error)) from None


def _phase(txn, journal, phase):
    _write(txn/'journal-next', _json_bytes(dict(journal, phase=phase)))
    os.replace(txn/'journal-next', txn/'journal.json')
    _sync_dir(txn)
    journal['phase'] = phase
    _checkpoint(phase)


def _commit(txn, journal):
    try:
        _phase(txn, journal, 'COMMITTED')
    except BaseException:
        if journal['phase'] != 'COMMITTED':
            # A failed rename/fsync may already expose COMMITTED to restart.
            # Live rollback is forbidden until a noncommitted record is again
            # durable. If that cannot be proven, keep both complete states and
            # every recovery source; restart will reconcile the visible record.
            try:
                _phase(txn, journal, 'CATALOG_WRITTEN')
            except BaseException:
                raise PublishIssue('E_COMMIT_UNCERTAIN', txn,
                                   'commit/abort journal durability unresolved; live state and backups retained') from None
        raise


def _copy_fixed(root, source, destination, names):
    destination.mkdir()
    for name in names:
        _write(destination/name, _bytes(root, source/name))
    _sync_dir(destination)
    _sync_dir(destination.parent)


def _journal(root, txn):
    """Validate recovery capabilities before allowing any live-path mutation."""
    path = _safe(root, txn/'journal.json')
    try:
        journal = _read_json(_bytes(root, path))
        keys = {'version', 'id', 'slug', 'phase', 'old_revision', 'new_revision',
                'old', 'new', 'catalog_old', 'catalog_new', 'owned_dirs'}
        if (set(journal) != keys or journal['version'] != 1 or type(journal['version']) is not int or
                journal['id'] != txn.name or re.fullmatch('[0-9a-f]{32}', txn.name) is None or
                journal['phase'] not in PHASES or
                type(journal['old_revision']) is not int or journal['old_revision'] < 0 or
                type(journal['new_revision']) is not int or journal['new_revision'] != journal['old_revision']+1):
            raise ValueError('invalid journal fields')
        slug = _slug(journal['slug'])
        for key in ('old', 'new'):
            values = journal[key]
            expected = set(FILES) if key == 'new' or journal['old_revision'] else set()
            if type(values) is not dict or set(values) != expected or any(
                    type(v) is not str or re.fullmatch('[0-9a-f]{64}', v) is None for v in values.values()):
                raise ValueError('invalid fixed digest set')
        allowed_dirs = ['dungeons', f'dungeons/{slug}', f'dungeons/{slug}/revisions']
        owned = journal['owned_dirs']
        if type(owned) is not list or len(set(owned)) != len(owned) or any(p not in allowed_dirs for p in owned):
            raise ValueError('invalid owned directories')
        for key in ('catalog_old', 'catalog_new'):
            expected = journal[key]
            if (key == 'catalog_new' or expected is not None) and (
                    type(expected) is not str or re.fullmatch('[0-9a-f]{64}', expected) is None):
                raise ValueError('invalid catalog digest')
            if _state(root, txn/key.replace('_', '-')) != expected:
                raise ValueError('catalog backup digest mismatch')
        for name, expected in journal['old'].items():
            if _state(root, txn/'old'/name) != expected:
                raise ValueError('old backup digest mismatch')
        # New files stay in the transaction even after promotion, for restart
        # verification and conservative ownership checks during cleanup.
        for name, expected in journal['new'].items():
            if _state(root, txn/'new'/name) != expected:
                raise ValueError('new backup digest mismatch')
        new, _ = _verify_bundle(root, txn/'new', slug, exact=True)
        if new['revision'] != journal['new_revision']:
            raise ValueError('new revision mismatch')
        if journal['old_revision']:
            old, dungeon = _verify_bundle(root, txn/'old', slug, exact=True)
            if (old['revision'] != journal['old_revision'] or old['name'] != new['name'] or
                    new['parent_digest'] != canonical_digest(dungeon)):
                raise ValueError('old revision/parent mismatch')
        elif new['parent_digest'] is not None:
            raise ValueError('first revision has a parent')
        return journal
    except (ValueError, TypeError, KeyError) as error:
        raise PublishIssue('E_JOURNAL', path, f'invalid journal; retained for inspection: {error}') from None


def _check_live(root, path, allowed):
    try:
        actual = _state(root, path)
    except PublishIssue:
        raise PublishIssue('E_RECOVERY_USER_CHANGED', path, 'unsafe or changed recovery path; journal retained') from None
    if actual not in allowed:
        raise PublishIssue('E_RECOVERY_USER_CHANGED', path, 'matches neither recorded old nor new state; journal retained')
    return actual


def _archive_check(root, archive, old):
    if archive.exists():
        if not archive.is_dir() or set(p.name for p in archive.iterdir()) != set(FILES):
            raise PublishIssue('E_RECOVERY_USER_CHANGED', archive, 'archive contains unexpected content; journal retained')
        for name in FILES:
            _check_live(root, archive/name, {old[name]})


def _restore(root, txn, journal):
    if journal['phase'] == 'COMMITTED':
        raise PublishIssue('E_COMMIT_STATE', txn, 'committed journal cannot authorize rollback')
    journal = _journal(root, txn)
    if journal['phase'] == 'COMMITTED':
        raise PublishIssue('E_COMMIT_STATE', txn, 'visible committed journal cannot authorize rollback')
    # Restart may observe an abort replacement whose preceding directory fsync
    # failed. Confirm its durability before making any old-state file durable.
    _sync_dir(txn)
    slug = journal['slug']
    current = _safe(root, root/'dungeons'/slug)
    catalog = _safe(root, root/'dungeons/Dungeons.md')
    archive = _safe(root, current/'revisions'/f'r{journal["old_revision"]:04d}')
    # Preflight every destination before restoring any: conflicts preserve all.
    for name in FILES:
        _check_live(root, current/name, {journal['old'].get(name), journal['new'][name]})
    _check_live(root, catalog, {journal['catalog_old'], journal['catalog_new']})
    if journal['old_revision']:
        _archive_check(root, archive, journal['old'])
    for name in FILES:
        path = current/name
        expected = journal['old'].get(name)
        if _state(root, path) != expected:
            if expected is None:
                path.unlink()
            else:
                _write(txn/'restore', _bytes(root, txn/'old'/name))
                os.replace(txn/'restore', path)
                _sync_dir(txn)
            _sync_dir(current)
        _checkpoint('rollback:'+name)
    if _state(root, catalog) != journal['catalog_old']:
        if journal['catalog_old'] is None:
            catalog.unlink()
        else:
            _write(txn/'restore', _bytes(root, txn/'catalog-old'))
            os.replace(txn/'restore', catalog)
            _sync_dir(txn)
        _sync_dir(catalog.parent)
    _checkpoint('rollback:catalog')
    if journal['old_revision'] and archive.exists():
        # Move complete archive back into owned transaction storage before
        # cleanup; a crash never leaves a half-deleted public archive.
        os.rename(archive, txn/'archive-rollback')
        _sync_dir(archive.parent)
        _sync_dir(txn)
        _checkpoint('rollback:archive')
    for name in FILES:
        _check_live(root, current/name, {journal['old'].get(name)})
    _check_live(root, catalog, {journal['catalog_old']})
    for relative in sorted(journal['owned_dirs'], key=lambda p: p.count('/'), reverse=True):
        path = _safe(root, root/relative)
        if path.exists() and path.is_dir() and not any(path.iterdir()):
            path.rmdir()
            _sync_dir(path.parent)


def _cleanup_inventory(root, directory):
    allowed = {'old', 'new', 'archive', 'archive-rollback', 'journal.json',
               'journal-next', 'catalog-old', 'catalog-new', 'restore', 'promote', 'owner.json'}
    inventory = {}
    for child in directory.iterdir():
        _safe(root, child)
        if child.name == 'cleanup.json':
            continue
        if child.name not in allowed:
            raise PublishIssue('E_JOURNAL', child, 'unexpected transaction content; retained')
        if child.name in ('old', 'new', 'archive', 'archive-rollback'):
            if not child.is_dir() or any(p.name not in FILES or not p.is_file() or p.is_symlink() for p in child.iterdir()):
                raise PublishIssue('E_JOURNAL', child, 'unexpected backup content; retained')
            for artifact in child.iterdir():
                inventory[f'{child.name}/{artifact.name}'] = _state(root, artifact)
        else:
            inventory[child.name] = _state(root, child)
    return inventory


def _cleanup(root, txn):
    # Terminal cleanup is first atomically renamed out of the pending namespace.
    # Restart can finish partial cleanup without requiring consumed backups.
    identifier = txn.name[6:] if txn.name.startswith('build-') else txn.name
    garbage = txn if txn.name.startswith('done-') else txn.with_name('done-'+identifier)
    if garbage != txn:
        inventory = _cleanup_inventory(root, txn)
        _write(txn/'cleanup.json', _json_bytes({'version': 1, 'id': identifier, 'files': inventory}))
        _sync_dir(txn)
        os.rename(txn, garbage)
        _sync_dir(garbage.parent)
        _checkpoint('cleanup-renamed')
    if not any(garbage.iterdir()):
        garbage.rmdir()
        _sync_dir(garbage.parent)
        return
    try:
        receipt = _read_json(_bytes(root, garbage/'cleanup.json'))
        if (set(receipt) != {'version', 'id', 'files'} or type(receipt['version']) is not int or
                receipt['version'] != 1 or receipt['id'] != garbage.name[5:] or type(receipt['files']) is not dict):
            raise ValueError('invalid cleanup receipt')
        actual = _cleanup_inventory(root, garbage)
        for name, expected in receipt['files'].items():
            parts = name.split('/')
            if ((len(parts) == 1 and name not in {'journal.json', 'journal-next', 'catalog-old', 'catalog-new', 'restore', 'promote', 'owner.json'}) or
                    (len(parts) == 2 and (parts[0] not in {'old', 'new', 'archive', 'archive-rollback'} or parts[1] not in FILES)) or
                    len(parts) not in (1, 2) or type(expected) is not str or re.fullmatch('[0-9a-f]{64}', expected) is None):
                raise ValueError('invalid cleanup capability')
        if any(receipt['files'].get(name) != digest for name, digest in actual.items()):
            raise ValueError('cleanup file differs from terminal inventory')
    except (ValueError, TypeError, KeyError) as error:
        raise PublishIssue('E_JOURNAL', garbage, f'cleanup ownership invalid; retained: {error}') from None
    for child in list(garbage.iterdir()):
        if child.name == 'cleanup.json':
            continue
        if child.is_dir():
            for artifact in child.iterdir():
                artifact.unlink()
                _sync_dir(child)
                _checkpoint('cleanup:'+str(artifact.relative_to(garbage)))
            child.rmdir()
        else:
            child.unlink()
        _sync_dir(garbage)
    (garbage/'cleanup.json').unlink()
    _sync_dir(garbage)
    garbage.rmdir()
    _sync_dir(garbage.parent)


def _abort_build(root, txn):
    # Build namespace never has authority to touch current/catalog/history.
    # Ownership receipt is durable before writing any staged/backup artifacts.
    if not any(txn.iterdir()):
        txn.rmdir()
        _sync_dir(txn.parent)
        return
    try:
        owner = _read_json(_bytes(root, txn/'owner.json'))
        if owner != {'version': 1, 'id': txn.name[6:]} or type(owner['version']) is not int:
            raise ValueError('invalid construction ownership')
    except (ValueError, TypeError, KeyError) as error:
        raise PublishIssue('E_JOURNAL', txn, f'construction ownership invalid; retained: {error}') from None
    _cleanup(root, txn)


def _prepare(root, stage, current, manifest, old, old_catalog, new_catalog, dirs):
    base = _safe(root, root/'.fcw-dungeon-txn')
    base.mkdir(exist_ok=True)
    _sync_dir(root)
    identifier = uuid.uuid4().hex
    txn = base/('build-'+identifier)
    # Bootstrap is an inert staging subarea, outside pending build discovery.
    # Incomplete receipts and unknown files there confer no recovery/deletion
    # authority. Only a complete, synced receipt is promoted into build-*.
    bootstrap = _safe(root, base/'bootstrap')
    bootstrap.mkdir(exist_ok=True)
    _sync_dir(base)
    initial = bootstrap/identifier
    initial.mkdir()
    _sync_dir(bootstrap)
    try:
        owner = _json_bytes({'version': 1, 'id': identifier})
        _write(initial/'owner.json', owner)
        _sync_dir(initial)
        if set(p.name for p in initial.iterdir()) != {'owner.json'} or _bytes(root, initial/'owner.json') != owner:
            raise PublishIssue('E_JOURNAL', initial, 'bootstrap content changed; retained before promotion')
        os.rename(initial, txn)
        _sync_dir(bootstrap)
        _sync_dir(base)
        if not any(bootstrap.iterdir()):
            bootstrap.rmdir()
            _sync_dir(base)
        _copy_fixed(root, stage, txn/'new', ARTIFACTS)
        _write(txn/'new/manifest.json', _json_bytes(manifest))
        _sync_dir(txn/'new')
        if old:
            _copy_fixed(root, current, txn/'old', FILES)
        if old_catalog is not None:
            _write(txn/'catalog-old', old_catalog)
        _write(txn/'catalog-new', new_catalog)
        journal = {'version': 1, 'id': identifier, 'slug': manifest['slug'], 'phase': 'PREPARED',
                   'old_revision': manifest['revision']-1, 'new_revision': manifest['revision'],
                   'old': old, 'new': {name: _state(root, txn/'new'/name) for name in FILES},
                   'catalog_old': None if old_catalog is None else _hash(old_catalog),
                   'catalog_new': _hash(new_catalog),
                   'owned_dirs': [str(p.relative_to(root)) for p in dirs if not p.exists()]}
        _write(txn/'journal.json', _json_bytes(journal))
        _sync_dir(txn)
        prepared = base/identifier
        os.rename(txn, prepared)
        _sync_dir(base)
        return prepared, journal
    except BaseException:
        if txn.exists():
            _abort_build(root, txn)
        raise


def _verify_committed(root, txn, journal):
    current = _safe(root, root/'dungeons'/journal['slug'])
    for name, expected in journal['new'].items():
        _check_live(root, current/name, {expected})
    _check_live(root, root/'dungeons/Dungeons.md', {journal['catalog_new']})
    try:
        manifest, _ = _verify_bundle(root, current, journal['slug'])
        _verify_history(root, current, manifest)
        if journal['old_revision']:
            _archive_check(root, current/'revisions'/f'r{journal["old_revision"]:04d}', journal['old'])
    except PublishIssue as error:
        raise PublishIssue('E_RECOVERY_USER_CHANGED', error.path,
                           'committed current/history/catalog disagree; journal and backups retained') from None
    # Reconcile a visible COMMITTED record whose prior directory fsync outcome
    # was unknown. No recovery sources are discarded if this fsync fails.
    _sync_dir(txn)


def _recover_locked(root):
    base = _safe(root, root/'.fcw-dungeon-txn')
    if not base.exists():
        return
    if not base.is_dir():
        raise PublishIssue('E_JOURNAL', base, 'transaction area must be a directory')
    for txn in sorted(base.iterdir()):
        _safe(root, txn)
        if not txn.is_dir():
            raise PublishIssue('E_JOURNAL', txn, 'unexpected transaction entry')
        if txn.name == 'bootstrap':
            # Never interpret incomplete staging receipts as active journals or
            # delete unknown bootstrap bytes. They cannot block known recovery.
            continue
        if re.fullmatch('done-[0-9a-f]{32}', txn.name):
            _cleanup(root, txn)
            continue
        if re.fullmatch('build-[0-9a-f]{32}', txn.name):
            _abort_build(root, txn)
            continue
        journal = _journal(root, txn)
        if journal['phase'] != 'COMMITTED':
            _restore(root, txn, journal)
        else:
            _verify_committed(root, txn, journal)
        _cleanup(root, txn)


def recover_pending(root):
    """Acquire the permanent root lock and recover/clean pending transactions."""
    root = _root(root)
    try:
        with _locked(root):
            _recover_locked(root)
    except OSError as error:
        if isinstance(error, PublishIssue):
            raise
        raise PublishIssue('E_RECOVERY', root, str(error)) from None


def publish_bundle(root, slug, stage, stage_info):
    """Publish a verified staged bundle, return revision assigned under flock."""
    root, slug = _root(root), _slug(slug)
    txn = journal = None
    try:
        stage, dungeon = _validate_stage(root, slug, stage, stage_info)
        with _locked(root):
            _recover_locked(root)
            current = _safe(root, root/'dungeons'/slug)
            catalog = _safe(root, root/'dungeons/Dungeons.md')
            old_catalog = _bytes(root, catalog)
            old_revision, parent, old = 0, None, {}
            if (current/'manifest.json').exists():
                previous, old_dungeon = _verify_bundle(root, current, slug)
                if previous['name'] != dungeon['brief']['name']:
                    raise PublishIssue('E_IDENTITY', current, 'different display name already owns this slug')
                _verify_history(root, current, previous)
                old_revision, parent = previous['revision'], canonical_digest(old_dungeon)
                old = {name: _state(root, current/name) for name in FILES}
            else:
                for name in FILES:
                    if _bytes(root, current/name) is not None:
                        raise PublishIssue('E_CURRENT_CHANGED', current/name, 'unmanaged fixed file already exists')
                history = _safe(root, current/'revisions')
                if history.exists() and any(re.fullmatch('r[0-9]{4,}', p.name) for p in history.iterdir()):
                    raise PublishIssue('E_HISTORY', history, 'archives exist without a current manifest')
            manifest = _manifest(dungeon, stage_info, old_revision+1, parent)
            new_catalog = _catalog(root, old_catalog, manifest)
            _catalog_bounds(new_catalog)
            # Keep caller stage untouched. The ninth file belongs to durable
            # transaction staging, assigned only now under the root lock.
            dirs = [root/'dungeons', current] + ([current/'revisions'] if old else [])
            txn, journal = _prepare(root, stage, current, manifest, old, old_catalog, new_catalog, dirs)
            try:
                _checkpoint('PREPARED')
                # Validate durable backups and all original live states again.
                _journal(root, txn)
                for name in FILES:
                    _check_live(root, current/name, {old.get(name)})
                _check_live(root, catalog, {journal['catalog_old']})
                for directory in dirs:
                    _safe(root, directory).mkdir(exist_ok=True)
                    _sync_dir(directory.parent)
                archive = current/'revisions'/f'r{old_revision:04d}'
                if old:
                    if archive.exists():
                        raise PublishIssue('E_HISTORY', archive, 'archive already exists')
                    _copy_fixed(root, txn/'old', txn/'archive', FILES)
                    os.rename(txn/'archive', archive)
                    _sync_dir(txn)
                    _sync_dir(archive.parent)
                    _checkpoint('archive-renamed')
                _phase(txn, journal, 'ARCHIVED')
                _phase(txn, journal, 'PUBLISHING')
                for name in FILES:
                    _check_live(root, current/name, {old.get(name)})
                    _write(txn/'promote', _bytes(root, txn/'new'/name))
                    os.replace(txn/'promote', current/name)
                    _sync_dir(txn)
                    _sync_dir(current)
                    _checkpoint('current:'+name)
                _check_live(root, catalog, {journal['catalog_old']})
                _write(txn/'promote', new_catalog)
                os.replace(txn/'promote', catalog)
                _sync_dir(txn)
                _sync_dir(catalog.parent)
                _checkpoint('catalog-renamed')
                _phase(txn, journal, 'CATALOG_WRITTEN')
                published, _ = _verify_bundle(root, current, slug)
                if published != manifest:
                    raise PublishIssue('E_VERIFY', current, 'published manifest differs')
                _verify_history(root, current, published)
                if _bytes(root, catalog) != new_catalog:
                    raise PublishIssue('E_VERIFY', catalog, 'published catalog differs')
                _commit(txn, journal)
            except BaseException as error:
                if isinstance(error, PublishIssue) and error.code == 'E_COMMIT_UNCERTAIN':
                    raise
                if journal['phase'] != 'COMMITTED':
                    _restore(root, txn, journal)
                else:
                    _verify_committed(root, txn, journal)
                _cleanup(root, txn)
                raise
            _verify_committed(root, txn, journal)
            _cleanup(root, txn)
            return old_revision+1
    except (OSError, model.ValidationIssue) as error:
        if isinstance(error, PublishIssue):
            raise
        raise PublishIssue('E_PUBLICATION', root, str(error)) from None
