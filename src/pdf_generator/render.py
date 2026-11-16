"""PDF の生成本体。

- 可視テキスト: 「outline」モードではグリフを図形として描画（テキスト抽出・コピー不可）
- 隠しテキスト: 人間には見えないが、テキスト抽出（＝AI への入力）には現れる文字列を埋め込む
- 権限設定: PDF 暗号化の権限フラグでコピー・編集を禁止
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from enum import Enum

from reportlab.lib.pagesizes import A4, A5, B5, letter
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont as RLTTFont
from reportlab.pdfgen.canvas import Canvas

from .fonts import OutlineFont, build_embeddable_ttf, find_default_font
from .layout import paginate, wrap_line

PAGE_SIZES = {"A4": A4, "A5": A5, "B5": B5, "letter": letter}

# 抽出可能な「本物の文字」を書くための埋め込みフォント名の接頭辞。
# reportlab はフォントを名前でグローバルにキャッシュするため、生成ごとに一意な名前を付ける。
TEXT_FONT_PREFIX = "PdfGenText"


class VisibleMode(str, Enum):
    OUTLINE = "outline"  # 図形化（コピー不可）
    TEXT = "text"  # 通常のテキスト


class HiddenMethod(str, Enum):
    INVISIBLE = "invisible"  # テキスト描画モード 3（不可視）
    WHITE = "white"  # 白文字
    TINY = "tiny"  # 極小文字
    OFFPAGE = "offpage"  # ページ外に配置


class InjectPosition(str, Enum):
    TOP = "top"
    BOTTOM = "bottom"
    BOTH = "both"


@dataclass
class Options:
    page_size: str = "A4"
    margin_mm: float = 20.0
    font_size: float = 10.5
    line_height: float = 1.7
    font_path: str | None = None
    font_index: int = 0
    title: str | None = None
    visible_mode: VisibleMode = VisibleMode.OUTLINE
    # コピー禁止の権限設定（暗号化）
    restrict_permissions: bool = True
    owner_password: str | None = None
    # 隠しテキスト
    hidden_texts: list[str] = field(default_factory=list)
    hidden_method: HiddenMethod = HiddenMethod.INVISIBLE
    hidden_position: InjectPosition = InjectPosition.TOP
    hidden_all_pages: bool = True
    hidden_font_size: float = 4.0
    hidden_in_metadata: bool = False


def generate_pdf(text: str, output_path: str, opts: Options) -> dict:
    """text から PDF を生成し output_path に保存する。生成情報を dict で返す。"""
    page_w, page_h = PAGE_SIZES[opts.page_size]
    margin = opts.margin_mm * mm
    content_w = page_w - margin * 2
    leading = opts.font_size * opts.line_height
    lines_per_page = max(1, int((page_h - margin * 2) // leading))

    font_path, font_index = (opts.font_path, opts.font_index) if opts.font_path else find_default_font()
    outline_font = OutlineFont(font_path, font_index)

    def width_of(ch: str) -> float:
        return outline_font.char_width(ch, opts.font_size)

    # 抽出可能な文字として書き込む文字（隠しテキスト＋text モードの本文）だけを含むフォントを埋め込む
    text_chars = "".join(opts.hidden_texts)
    if opts.visible_mode is VisibleMode.TEXT:
        text_chars += text
    text_font = f"{TEXT_FONT_PREFIX}-{secrets.token_hex(6)}"
    pdfmetrics.registerFont(RLTTFont(text_font, build_embeddable_ttf(font_path, font_index, text_chars)))

    pages = paginate(text, content_w, lines_per_page, width_of)

    owner_password = None
    encrypt = None
    if opts.restrict_permissions:
        owner_password = opts.owner_password or secrets.token_urlsafe(24)
        encrypt = StandardEncryption(
            userPassword="",  # 開くときのパスワードは不要
            ownerPassword=owner_password,
            canPrint=1,
            canModify=0,
            canCopy=0,
            canAnnotate=0,
            strength=128,
        )

    c = Canvas(output_path, pagesize=(page_w, page_h), encrypt=encrypt, invariant=False)
    if opts.title:
        c.setTitle(opts.title)
    if opts.hidden_in_metadata and opts.hidden_texts:
        joined = "\n".join(opts.hidden_texts)
        c.setSubject(joined)
        c.setKeywords(joined)

    for page_no, lines in enumerate(pages):
        inject_here = opts.hidden_texts and (opts.hidden_all_pages or page_no == 0)
        if inject_here and opts.hidden_position in (InjectPosition.TOP, InjectPosition.BOTH):
            _draw_hidden(c, opts, text_font, page_w, page_h, margin, top=True)

        y = page_h - margin - opts.font_size
        if opts.visible_mode is VisibleMode.OUTLINE:
            _draw_outline_lines(c, outline_font, lines, margin, y, leading, opts.font_size)
        else:
            _draw_text_lines(c, text_font, lines, margin, y, leading, opts.font_size)

        if inject_here and opts.hidden_position in (InjectPosition.BOTTOM, InjectPosition.BOTH):
            _draw_hidden(c, opts, text_font, page_w, page_h, margin, top=False)
        c.showPage()

    c.save()
    return {"pages": len(pages), "owner_password": owner_password}


def _draw_outline_lines(c: Canvas, font: OutlineFont, lines, x, y, leading, size) -> None:
    c.setFillColorRGB(0, 0, 0)
    for line in lines:
        if line.strip():
            p = c.beginPath()
            font.append_text_path(p, line, x, y, size)
            c.drawPath(p, stroke=0, fill=1, fillMode=0)  # 0 = non-zero winding
        y -= leading


def _draw_text_lines(c: Canvas, text_font: str, lines, x, y, leading, size) -> None:
    t = c.beginText(x, y)
    t.setFont(text_font, size, leading)
    for line in lines:
        t.textLine(line)
    c.drawText(t)


def _draw_hidden(c: Canvas, opts: Options, text_font: str, page_w, page_h, margin, top: bool) -> None:
    method = opts.hidden_method
    size = opts.hidden_font_size
    if method is HiddenMethod.TINY:
        size = min(size, 0.5)
    leading = size * 1.2

    def width_of(ch: str) -> float:
        return pdfmetrics.stringWidth(ch, text_font, size)

    lines: list[str] = []
    for hidden in opts.hidden_texts:
        for para in hidden.splitlines() or [""]:
            lines.extend(wrap_line(para, page_w - margin * 2, width_of))

    x = margin
    if method is HiddenMethod.OFFPAGE:
        x = -page_w  # 用紙の左外側
        y = page_h - margin if top else margin + leading * len(lines)
    elif top:
        y = page_h - size * 1.5
    else:
        y = margin * 0.5 + leading * len(lines)

    c.saveState()
    t = c.beginText(x, y)
    t.setFont(text_font, size, leading)
    if method is HiddenMethod.INVISIBLE:
        t.setTextRenderMode(3)
    elif method is HiddenMethod.WHITE:
        t.setFillColorRGB(1, 1, 1)
    elif method is HiddenMethod.TINY:
        t.setFillColorRGB(1, 1, 1)
    for line in lines:
        t.textLine(line)
    c.drawText(t)
    c.restoreState()
