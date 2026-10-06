"""Pinned ReportLab Vera files and actual cmap checks shared by both renderers."""
import hashlib
from functools import lru_cache
from pathlib import Path

import reportlab
from PIL import ImageFont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from .model import ValidationIssue

FONT_DIGESTS = {
    'regular': 'c4c45690b345435b2cba52ecabe275f05e49b389b39fe68ad03afbb551288d3d',
    'bold': 'cc037385e4d55bfde89b13e03091ee93bf40c0c52ddd391ff031ab276f13b8e9',
}
FONT_NAMES = {'regular': 'FCW-Vera', 'bold': 'FCW-Vera-Bold'}


def font_paths():
    root = Path(reportlab.__file__).parent / 'fonts'
    paths = {'regular': root/'Vera.ttf', 'bold': root/'VeraBd.ttf'}
    for key, path in paths.items():
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != FONT_DIGESTS[key]:
            raise ValidationIssue('E_FONT', '$.fonts.'+key, f'fixed font missing or digest mismatch: {path}')
    return paths


@lru_cache(maxsize=2)
def _font(bold):
    key = 'bold' if bold else 'regular'
    font = TTFont(FONT_NAMES[key], str(font_paths()[key]))
    pdfmetrics.registerFont(font)
    return font


def validate_text(text, *, bold=False, path='$.text'):
    cmap = _font(bold).face.charToGlyph
    for character in text:
        if character not in '\n\r\t' and (ord(character) not in cmap or cmap[ord(character)] == 0):
            raise ValidationIssue('E_GLYPH', path, f'unsupported glyph U+{ord(character):04X} in fixed Vera font')


def pdf_font(*, bold=False):
    return _font(bold).fontName


@lru_cache(maxsize=64)
def pillow_font(size, *, bold=False):
    return ImageFont.truetype(str(font_paths()['bold' if bold else 'regular']), size)
