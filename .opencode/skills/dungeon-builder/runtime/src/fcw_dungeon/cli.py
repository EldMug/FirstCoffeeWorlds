"""Strict terminal interface; generation commands are explicit artifact requests."""
import argparse
import json
from pathlib import Path
import re
import secrets
import sys
import tempfile


def _seed(text):
    if re.fullmatch('[0-9]+', text) is None or len(text) > 20 or int(text) > 2**64-1:
        raise argparse.ArgumentTypeError('seed must be uint64 (0–18446744073709551615)')
    return int(text)


def _parser():
    parser = argparse.ArgumentParser(prog='fcw-dungeon')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('brief-template', help='print JSON example')
    generate = commands.add_parser('generate', help='generate and publish')
    generate.add_argument('--brief', required=True)
    validate = commands.add_parser('validate', help='validate saved data read-only')
    validate.add_argument('--dungeon', required=True)
    render = commands.add_parser('render', help='publish supplied canonical data without rerolling')
    render.add_argument('--dungeon', required=True)
    regenerate = commands.add_parser('regenerate', help='generate a fresh base from saved/revised brief')
    regenerate.add_argument('--from', dest='source', required=True)
    regenerate.add_argument('--brief')
    for command in (generate, regenerate):
        command.add_argument('--seed', type=_seed)
    for command in (generate, render, regenerate):
        command.add_argument('--root')
    return parser


def _read(source, *, brief=False):
    from . import model
    limit = model.BRIEF_MAX_BYTES if brief else model.DOCUMENT_MAX_BYTES
    try:
        if source == '-':
            raw = sys.stdin.buffer.read(limit+1)
        else:
            with Path(source).open('rb') as stream:
                raw = stream.read(limit+1)
    except OSError:
        raise model.ValidationIssue('E_INPUT', '$.input', 'cannot read input file') from None
    parsed = model.parse_brief(raw) if brief else model.parse_document(raw)
    return parsed if brief else json.loads(raw)


def _checkout(explicit):
    from . import model
    if explicit is not None:
        root = Path(explicit).absolute()
        if root.is_symlink() or not root.is_dir():
            raise model.ValidationIssue('E_ROOT', '$.root', 'root must be an existing nonsymlink directory')
        return root.resolve()
    current = Path.cwd().resolve()
    for root in (current, *current.parents):
        if (root/'.git').exists():
            return root
    raise model.ValidationIssue('E_ROOT', '$.root', 'checkout not found; supply --root')


def _template():
    from . import model
    return model.normalize_brief({'name': 'Example Crypt', 'party_levels': [5, 5, 5, 5]})


def _error(error):
    from . import model
    # Schema messages may include a complete rejected value. Keep field/code,
    # use stable summaries instead of leaking potentially private input prose.
    if isinstance(error, model.ValidationIssue):
        messages = {'E_SCHEMA': 'invalid field value or unsupported schema/version',
                    'E_JSON_DUPLICATE': 'duplicate JSON key', 'E_JSON_NUMBER': 'invalid JSON number',
                    'E_SEMANTIC': 'saved data violates canonical constraints', 'E_LORE': 'definition heading is not allowed'}
        message = messages.get(error.code)
        text = f'{error.code} {error.path}: {message}' if message else str(error)
    else:
        text = str(error)
    return text.replace('\r', ' ').replace('\n', ' ')[:600]


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        from . import model
    except ImportError:
        print('E_DEPENDENCY: required input-validation dependency unavailable', file=sys.stderr)
        return 1
    if args.command == 'brief-template':
        print(json.dumps(_template(), ensure_ascii=False, indent=2))
        return 0
    try:
        if args.command == 'validate':
            model.validate_dungeon(_read(args.dungeon))
            print('Valid dungeon')
            return 0
        root = _checkout(args.root)
        from . import bundle, rules, storage
        if args.command == 'render':
            dungeon = _read(args.dungeon)
            model.validate_dungeon(dungeon)
            input_digest = storage.canonical_digest(dungeon)
        else:
            if args.command == 'generate':
                brief = model.normalize_brief(_read(args.brief, brief=True))
                seed = args.seed
            else:
                previous = _read(args.source)
                model.validate_dungeon(previous)
                brief = model.normalize_brief(_read(args.brief, brief=True) if args.brief else previous['brief'])
                seed = args.seed if args.seed is not None else secrets.randbits(64)
            dungeon = bundle.generate_dungeon(brief, seed, rules.load_catalog())
            input_digest = storage.canonical_digest(dungeon['brief'])
        with tempfile.TemporaryDirectory(prefix='.fcw-dungeon-stage-', dir=root) as temporary:
            stage = Path(temporary)
            info = bundle.write_staged_bundle(dungeon, stage,
                                             {'operation': args.command, 'input_digest': input_digest})
            revision = storage.publish_bundle(root, dungeon['brief']['slug'], stage, info)
        print(f'{root/"dungeons"/dungeon["brief"]["slug"]} revision={revision} GM seed={dungeon["seed"]}')
        return 0
    except model.ValidationIssue as error:
        print(_error(error), file=sys.stderr)
        return 1 if error.code in ('E_FONT', 'E_RENDER_FIT', 'E_IMAGE_SIZE') else 2
    except (OSError, ImportError) as error:
        print(_error(error), file=sys.stderr)
        return 1
    except ValueError as error:
        # CatalogIssue/FitIssue are ValueError; input errors were handled above.
        print(_error(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
