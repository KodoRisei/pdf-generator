"""フォント読み込みとグリフのアウトライン（ベクターパス）化。

可視テキストを「文字」ではなく「図形」として PDF に描画することで、
コピー＆ペーストやテキスト抽出で本文が取り出せないようにする。
"""

from __future__ import annotations

import hashlib
import io
import os
from dataclasses import dataclass, field
from pathlib import Path

from fontTools import subset
from fontTools.pens.basePen import BasePen
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, newTable

# 日本語を含むフォントの既定候補（上から順に存在するものを使う）
DEFAULT_FONT_CANDIDATES: list[tuple[str, int]] = [
    ("/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc", 0),  # macOS
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
    ("/Library/Fonts/Arial Unicode.ttf", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),  # Debian/Ubuntu
    ("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc", 0),  # Fedora/Arch
    ("C:/Windows/Fonts/meiryo.ttc", 0),  # Windows
    ("C:/Windows/Fonts/msgothic.ttc", 0),
]


def find_default_font() -> tuple[str, int]:
    env = os.environ.get("PDFGEN_FONT")
    if env:
        return env, 0
    for path, index in DEFAULT_FONT_CANDIDATES:
        if Path(path).exists():
            return path, index
    raise FileNotFoundError(
        "日本語フォントが見つかりません。--font でフォントファイル (.ttf/.otf/.ttc) を指定してください。"
    )


class _ReportLabPathPen(BasePen):
    """fontTools のグリフ描画命令を reportlab のパスへ変換するペン。"""

    def __init__(self, glyph_set, path, x: float, y: float, scale: float):
        super().__init__(glyph_set)
        self.path = path
        self.x = x
        self.y = y
        self.scale = scale

    def _t(self, pt):
        return self.x + pt[0] * self.scale, self.y + pt[1] * self.scale

    def _moveTo(self, pt):
        self.path.moveTo(*self._t(pt))

    def _lineTo(self, pt):
        self.path.lineTo(*self._t(pt))

    def _curveToOne(self, p1, p2, p3):
        self.path.curveTo(*self._t(p1), *self._t(p2), *self._t(p3))

    def _closePath(self):
        self.path.close()

    def _endPath(self):
        pass


@dataclass
class OutlineFont:
    """グリフ幅の取得とアウトライン描画を行うフォント。"""

    path: str
    index: int = 0
    _font: TTFont = field(init=False, repr=False)

    def __post_init__(self):
        self._font = TTFont(self.path, fontNumber=self.index, lazy=True)
        self._cmap = self._font.getBestCmap() or {}
        self._glyph_set = self._font.getGlyphSet()
        self._hmtx = self._font["hmtx"]
        self.units_per_em = self._font["head"].unitsPerEm

    def has_char(self, ch: str) -> bool:
        return ord(ch) in self._cmap

    def _glyph_name(self, ch: str) -> str:
        return self._cmap.get(ord(ch), ".notdef")

    def char_width(self, ch: str, size: float) -> float:
        advance = self._hmtx[self._glyph_name(ch)][0]
        return advance * size / self.units_per_em

    def text_width(self, text: str, size: float) -> float:
        return sum(self.char_width(ch, size) for ch in text)

    def append_text_path(self, path, text: str, x: float, y: float, size: float) -> float:
        """text を (x, y) を基準線の始点として path に追加し、描画後の x を返す。"""
        scale = size / self.units_per_em
        for ch in text:
            name = self._glyph_name(ch)
            if not ch.isspace():
                pen = _ReportLabPathPen(self._glyph_set, path, x, y, scale)
                self._glyph_set[name].draw(pen)
            x += self._hmtx[name][0] * scale
        return x


def build_embeddable_ttf(path: str, index: int, chars: str) -> io.BytesIO:
    """PDF に埋め込める TrueType (glyf) フォントを作る。

    必要な文字だけにサブセット化し、CFF（PostScript）アウトラインのフォントは
    TrueType アウトラインへ変換する。reportlab は glyf 形式しか埋め込めないため。
    埋め込んだフォントには ToUnicode が付くので、どの抽出ツールでも文字が読める。
    """
    font = TTFont(path, fontNumber=index)

    options = subset.Options()
    options.name_IDs = ["*"]
    options.notdef_outline = True
    options.layout_features = []
    options.drop_tables += ["VORG", "BASE", "meta", "vhea", "vmtx", "DSIG"]
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes={ord(c) for c in set(chars) | {" "}})
    subsetter.subset(font)

    if "CFF " in font:
        _cff_to_glyf(font)

    # reportlab はフォント内部の PostScript 名でフェイスを共有してしまうため、
    # 文字集合の異なるサブセット同士が衝突しないよう一意な名前を付ける
    digest = hashlib.sha1(f"{path}:{index}:{''.join(sorted(set(chars)))}".encode()).hexdigest()[:12]
    unique = f"PdfGen-{digest}"
    name_table = font["name"]
    for record in list(name_table.names):
        if record.nameID in (1, 3, 4, 6, 16):
            name_table.setName(unique, record.nameID, record.platformID, record.platEncID, record.langID)

    buf = io.BytesIO()
    font.save(buf)
    buf.seek(0)
    buf.name = f"{unique}.ttf"
    return buf


def _cff_to_glyf(font: TTFont) -> None:
    glyph_order = font.getGlyphOrder()
    glyph_set = font.getGlyphSet()

    glyf = newTable("glyf")
    glyf.glyphOrder = glyph_order
    glyf.glyphs = {}
    for name in glyph_order:
        pen = TTGlyphPen(glyph_set)
        glyph_set[name].draw(Cu2QuPen(pen, max_err=1.0, reverse_direction=True))
        glyf.glyphs[name] = pen.glyph()

    font["glyf"] = glyf
    font["loca"] = newTable("loca")
    del font["CFF "]
    font["head"].glyphDataFormat = 0

    maxp = font["maxp"]
    maxp.tableVersion = 0x00010000
    for attr in (
        "maxZones", "maxTwilightPoints", "maxStorage", "maxFunctionDefs",
        "maxInstructionDefs", "maxStackElements", "maxSizeOfInstructions", "maxComponentElements",
    ):
        setattr(maxp, attr, 0)
    maxp.maxZones = 1

    post = font["post"]
    post.formatType = 3.0
    font.sfntVersion = "\x00\x01\x00\x00"
