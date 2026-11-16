"""コマンドラインインターフェース。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .render import PAGE_SIZES, HiddenMethod, InjectPosition, Options, VisibleMode, generate_pdf


def _read_text(path: str, encoding: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding=encoding)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pdf-generator",
        description="テキストファイルから、コピー防止・隠しテキスト入りの PDF を生成します。",
    )
    p.add_argument("input", help="入力テキストファイル（- で標準入力）")
    p.add_argument("-o", "--output", help="出力 PDF のパス（既定: 入力名.pdf）")
    p.add_argument("--encoding", default="utf-8", help="入力ファイルの文字コード（既定: utf-8）")

    g = p.add_argument_group("レイアウト")
    g.add_argument("--page-size", choices=list(PAGE_SIZES), default="A4")
    g.add_argument("--margin", type=float, default=20.0, help="余白 [mm]（既定: 20）")
    g.add_argument("--font-size", type=float, default=10.5, help="本文の文字サイズ [pt]")
    g.add_argument("--line-height", type=float, default=1.7, help="行送り倍率")
    g.add_argument("--font", help="本文フォント (.ttf/.otf/.ttc)。未指定ならシステムの日本語フォント")
    g.add_argument("--font-index", type=int, default=0, help=".ttc 内のフォント番号")
    g.add_argument("--title", help="PDF のタイトル（メタデータ）")

    g = p.add_argument_group("コピー防止")
    g.add_argument(
        "--visible-mode",
        choices=[m.value for m in VisibleMode],
        default=VisibleMode.OUTLINE.value,
        help="outline: 本文を図形化しテキスト抽出不可にする（既定） / text: 通常の文字として埋め込む",
    )
    g.add_argument("--allow-copy", action="store_true", help="PDF の権限設定によるコピー禁止を行わない")
    g.add_argument("--owner-password", help="権限変更用パスワード（未指定ならランダム生成）")

    g = p.add_argument_group("隠しテキスト（AI 向け）")
    g.add_argument("--inject", action="append", default=[], metavar="TEXT", help="埋め込む隠しテキスト（複数指定可）")
    g.add_argument("--inject-file", action="append", default=[], metavar="FILE", help="隠しテキストをファイルから読み込む")
    g.add_argument(
        "--inject-method",
        choices=[m.value for m in HiddenMethod],
        default=HiddenMethod.INVISIBLE.value,
        help="invisible: 不可視描画（既定） / white: 白文字 / tiny: 極小白文字 / offpage: 用紙外",
    )
    g.add_argument(
        "--inject-position",
        choices=[m.value for m in InjectPosition],
        default=InjectPosition.TOP.value,
        help="ページ内で隠しテキストを置く位置（top なら抽出結果の先頭に来やすい）",
    )
    g.add_argument("--inject-first-page-only", action="store_true", help="1 ページ目にだけ埋め込む")
    g.add_argument("--inject-font-size", type=float, default=4.0, help="隠しテキストの文字サイズ [pt]")
    g.add_argument("--inject-metadata", action="store_true", help="PDF メタデータ（件名・キーワード）にも埋め込む")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    text = _read_text(args.input, args.encoding)
    hidden = list(args.inject)
    for f in args.inject_file:
        hidden.append(Path(f).read_text(encoding=args.encoding).strip())

    if args.output:
        output = args.output
    elif args.input == "-":
        output = "output.pdf"
    else:
        output = str(Path(args.input).with_suffix(".pdf"))

    opts = Options(
        page_size=args.page_size,
        margin_mm=args.margin,
        font_size=args.font_size,
        line_height=args.line_height,
        font_path=args.font,
        font_index=args.font_index,
        title=args.title,
        visible_mode=VisibleMode(args.visible_mode),
        restrict_permissions=not args.allow_copy,
        owner_password=args.owner_password,
        hidden_texts=hidden,
        hidden_method=HiddenMethod(args.inject_method),
        hidden_position=InjectPosition(args.inject_position),
        hidden_all_pages=not args.inject_first_page_only,
        hidden_font_size=args.inject_font_size,
        hidden_in_metadata=args.inject_metadata,
    )

    try:
        info = generate_pdf(text, output, opts)
    except FileNotFoundError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1

    print(f"生成しました: {output}（{info['pages']} ページ）")
    if info["owner_password"] and not args.owner_password:
        print(f"権限パスワード: {info['owner_password']}")
    return 0
