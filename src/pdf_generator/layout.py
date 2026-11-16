"""テキストの行折り返しとページ分割（簡易禁則処理つき）。"""

from __future__ import annotations

from typing import Callable

# 行頭に来てはいけない文字
NO_LINE_START = set("、。，．・：；？！ー）」』】〕〉》｝］’”ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ々…‥),.:;?!]}")
# 行末に来てはいけない文字
NO_LINE_END = set("（「『【〔〈《｛［‘“([{")


def wrap_line(text: str, max_width: float, width_of: Callable[[str], float]) -> list[str]:
    """1 段落を max_width に収まるよう折り返す。"""
    if not text:
        return [""]

    lines: list[str] = []
    current = ""
    current_width = 0.0
    i = 0
    while i < len(text):
        ch = text[i]
        # 英数字の連続は単語単位で扱う
        if ch.isascii() and ch.isalnum():
            j = i
            while j < len(text) and text[j].isascii() and (text[j].isalnum() or text[j] in "-_'"):
                j += 1
            token = text[i:j]
        else:
            token = ch
            j = i + 1
        token_width = sum(width_of(c) for c in token)

        if current and current_width + token_width > max_width:
            if token[0] in NO_LINE_START:
                # ぶら下げ：行頭禁則文字は現在の行に含めてしまう
                current += token
                lines.append(current)
                current, current_width = "", 0.0
                i = j
                continue
            if current[-1] in NO_LINE_END:
                carry = current[-1]
                current = current[:-1]
                lines.append(current)
                current, current_width = carry, width_of(carry)
            else:
                lines.append(current)
                current, current_width = "", 0.0
            if token.isspace():
                i = j
                continue

        if token_width > max_width:
            # 1 単語が 1 行に収まらない場合は文字単位で分割
            for c in token:
                w = width_of(c)
                if current and current_width + w > max_width:
                    lines.append(current)
                    current, current_width = "", 0.0
                current += c
                current_width += w
        else:
            current += token
            current_width += token_width
        i = j

    lines.append(current)
    return lines


def paginate(
    text: str,
    max_width: float,
    lines_per_page: int,
    width_of: Callable[[str], float],
    tab_size: int = 4,
) -> list[list[str]]:
    """テキスト全体をページごとの行リストに分割する。\\f（改ページ文字）で強制改ページ。"""
    pages: list[list[str]] = []
    for chunk in text.replace("\r\n", "\n").replace("\r", "\n").split("\f"):
        page: list[str] = []
        for paragraph in chunk.split("\n"):
            for line in wrap_line(paragraph.expandtabs(tab_size), max_width, width_of):
                if len(page) >= lines_per_page:
                    pages.append(page)
                    page = []
                page.append(line)
        pages.append(page)
    # 末尾の空ページは除く（ただし最低 1 ページは残す）
    while len(pages) > 1 and not any(pages[-1]):
        pages.pop()
    return pages
