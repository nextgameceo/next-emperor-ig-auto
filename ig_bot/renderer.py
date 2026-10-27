"""カルーセル画像（1080x1350 / 4:5 JPEG）の描画。

デザイン: 黒地＋ゴールド＋シルバー＋シアンの光（NEXT EMPERORロゴの配色）。
- 表紙: 対象ラベル・タイトル・サブタイトル・「スワイプ」誘導
- 本文: 番号・見出し・本文（1枚1メッセージ）
- 最終: 固定の案内文（AIに書かせない＝事実の誤りが入らない）
assets/logo.png があれば表紙と最終スライドに配置する。
"""
from __future__ import annotations

import math
import pathlib
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1350
MARGIN = 96
BG = (7, 8, 12)
GOLD = (217, 180, 90)
GOLD_DIM = (140, 112, 52)
SILVER = (205, 210, 218)
WHITE = (246, 244, 238)
CYAN = (95, 212, 240)
MUTED = (128, 134, 146)

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOGO = ROOT / "assets" / "logo.png"

# 行頭に置かない文字（禁則）
NO_START = set("、。，．,.!！?？」』）)］]｝}〉》ー〜…・ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ％%")
NO_END = set("「『（(［[｛{〈《")

_FONT_CANDIDATES = {
    "serif_bold": ["/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
                   "/usr/share/fonts/noto-cjk/NotoSerifCJK-Bold.ttc"],
    "sans_bold": ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
                  "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc"],
    "sans": ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
             "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"],
}
_font_cache: dict = {}


class FontMissing(RuntimeError):
    pass


_path_cache: dict = {}


def _font_paths(kind: str) -> list[str]:
    """既知のパス＋/usr/share/fonts 以下の同名ファイル（ディストリによる配置の違いに対応）。"""
    if kind not in _path_cache:
        names = {pathlib.Path(p).name for p in _FONT_CANDIDATES[kind]}
        root = pathlib.Path("/usr/share/fonts")
        found = [str(p) for p in root.rglob("*.tt[cf]") if p.name in names] if root.exists() else []
        _path_cache[kind] = list(dict.fromkeys(list(_FONT_CANDIDATES[kind]) + found))
    return _path_cache[kind]


def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    key = (kind, size)
    if key in _font_cache:
        return _font_cache[key]
    for path in _font_paths(kind):
        if not pathlib.Path(path).exists():
            continue
        # TTC内の日本語(JP)フェイスを名前で探す（並び順に依存しない）
        for idx in range(10):
            try:
                f = ImageFont.truetype(path, size, index=idx)
            except OSError:
                break
            if " JP" in f.getname()[0]:
                _font_cache[key] = f
                return f
    raise FontMissing(f"日本語フォント（Noto CJK {kind}）が見つかりません。fonts-noto-cjk をインストールしてください")


# ------------------------------------------------------------------ テキスト折り返し
def _tokens(text: str) -> list[str]:
    """英数字の連続は1語として扱い、それ以外は1文字ずつ。"""
    out, buf = [], ""
    for ch in text:
        if ch.isascii() and (ch.isalnum() or ch in "-_'&+"):
            buf += ch
        else:
            if buf:
                out.append(buf)
                buf = ""
            out.append(ch)
    if buf:
        out.append(buf)
    return out


def wrap(text: str, f: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    lines: list[str] = []
    for para in str(text).split("\n"):
        line = ""
        for tok in _tokens(para):
            if tok == " " and not line:
                continue
            trial = line + tok
            if f.getlength(trial) <= max_w or not line:
                line = trial
                continue
            if tok[0] in NO_START:          # 句読点などは前の行にぶら下げる
                line = trial
                continue
            if line and line[-1] in NO_END:  # 開き括弧は次の行へ送る
                lines.append(line[:-1])
                line = line[-1] + tok
                continue
            lines.append(line.rstrip())
            line = tok.lstrip()
        lines.append(line.rstrip())
    return lines


def balanced_wrap(text: str, f: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    """行数はそのままで、各行の長さがなるべく揃う幅を探して折り返す（最終行だけ短い、を防ぐ）。"""
    lines = wrap(text, f, max_w)
    if len(lines) <= 1:
        return lines
    lo, hi = max_w // 3, max_w
    while hi - lo > 4:
        mid = (lo + hi) // 2
        if len(wrap(text, f, mid)) <= len(lines):
            hi = mid
        else:
            lo = mid
    return wrap(text, f, hi)


def fit_text(text: str, kind: str, max_size: int, min_size: int, max_w: int, max_lines: int,
             balanced: bool = False):
    """収まる最大のフォントサイズで折り返す。"""
    wrapper = balanced_wrap if balanced else wrap
    size = max_size
    while size >= min_size:
        f = font(kind, size)
        lines = wrapper(text, f, max_w)
        if len(lines) <= max_lines:
            return f, lines
        size -= 4
    f = font(kind, min_size)
    return f, wrapper(text, f, max_w)


# ------------------------------------------------------------------ 装飾
def _background(seed: str, glow_xy: tuple[int, int], glow_r: int, strength: int = 70) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    glow = Image.new("RGB", (W, H), BG)
    gd = ImageDraw.Draw(glow)
    x, y = glow_xy
    for i, (r, a) in enumerate(((glow_r, strength), (int(glow_r * 0.55), int(strength * 0.6)))):
        col = tuple(int(BG[k] + (CYAN[k] - BG[k]) * a / 255) for k in range(3))
        gd.ellipse([x - r, y - r, x + r, y + r], fill=col)
    glow = glow.filter(ImageFilter.GaussianBlur(160))
    img = Image.blend(img, glow, 1.0)
    d = ImageDraw.Draw(img)
    rng = random.Random(seed)
    for _ in range(46):  # 星くず
        sx, sy = rng.randrange(W), rng.randrange(H)
        v = rng.randrange(60, 150)
        d.point((sx, sy), fill=(v, v, v + 10))
    return img


def sparkle(d: ImageDraw.ImageDraw, cx: int, cy: int, r: int, color=SILVER) -> None:
    """4方向に伸びる光のマーク（ロゴの星を簡略化）。"""
    t = max(2, r // 6)
    d.polygon([(cx, cy - r), (cx + t, cy - t), (cx + r, cy), (cx + t, cy + t),
               (cx, cy + r), (cx - t, cy + t), (cx - r, cy), (cx - t, cy - t)], fill=color)


def _spaced(d: ImageDraw.ImageDraw, xy, text: str, f, fill, spacing: int, anchor_center=False) -> int:
    """字間を空けて描画。描画幅を返す。"""
    widths = [f.getlength(c) for c in text]
    total = sum(widths) + spacing * (len(text) - 1)
    x, y = xy
    if anchor_center:
        x -= total / 2
    for c, w in zip(text, widths):
        d.text((x, y), c, font=f, fill=fill)
        x += w + spacing
    return int(total)


def _header(d: ImageDraw.ImageDraw, page: int, total: int) -> None:
    _spaced(d, (MARGIN, 78), "NEXT EMPEROR", font("serif_bold", 28), GOLD, 7)
    f = font("sans", 26)
    label = f"{page:02d} / {total:02d}"
    d.text((W - MARGIN - f.getlength(label), 80), label, font=f, fill=MUTED)


def _footer_rule(d: ImageDraw.ImageDraw) -> None:
    y = H - 118
    d.line([(MARGIN, y), (W - MARGIN, y)], fill=(40, 42, 50), width=2)


def _paste_logo(img: Image.Image, center_x: int, top: int, max_w: int) -> int:
    """ロゴがあれば貼り付けて高さを返す。なければ0。"""
    if not LOGO.exists():
        return 0
    logo = Image.open(LOGO).convert("RGBA")
    scale = min(max_w / logo.width, 1.0)
    logo = logo.resize((int(logo.width * scale), int(logo.height * scale)), Image.LANCZOS)
    img.paste(logo, (int(center_x - logo.width / 2), top), logo)
    return logo.height


# ------------------------------------------------------------------ スライド
def render_cover(post: dict, aud_cfg: dict, total: int, seed: str) -> Image.Image:
    img = _background(seed + "c", (W - 180, 260), 520, 90)
    d = ImageDraw.Draw(img)
    _header(d, 1, total)

    # 対象バッジ
    bf = font("sans_bold", 26)
    badge = aud_cfg.get("badge", "")
    bw = _spaced(ImageDraw.Draw(Image.new("RGB", (1, 1))), (0, 0), badge, bf, CYAN, 5)
    by = 330
    d.rounded_rectangle([MARGIN, by, MARGIN + bw + 52, by + 58], radius=29, outline=CYAN, width=2)
    _spaced(d, (MARGIN + 26, by + 12), badge, bf, CYAN, 5)

    # タイトル
    tf, lines = fit_text(post["title"], "serif_bold", 96, 64, W - MARGIN * 2, 3, balanced=True)
    lh = int(tf.size * 1.38)
    y = by + 120
    for ln in lines:
        d.text((MARGIN, y), ln, font=tf, fill=WHITE)
        y += lh
    # ゴールドの区切り線
    y += 24
    d.line([(MARGIN, y), (MARGIN + 140, y)], fill=GOLD, width=4)
    sparkle(d, MARGIN + 172, y + 1, 14, GOLD)
    y += 56
    sf, slines = fit_text(post.get("subtitle", ""), "sans", 40, 32, W - MARGIN * 2, 3, balanced=True)
    for ln in slines:
        d.text((MARGIN, y), ln, font=sf, fill=SILVER)
        y += int(sf.size * 1.6)

    _footer_rule(d)
    ff = font("sans", 28)
    d.text((MARGIN, H - 88), aud_cfg.get("label", ""), font=ff, fill=MUTED)
    sw = font("sans_bold", 30)
    msg = "スワイプして読む  →"
    d.text((W - MARGIN - sw.getlength(msg), H - 90), msg, font=sw, fill=GOLD)
    sparkle(d, W - 170, 210, 22)
    sparkle(d, W - 260, 300, 10)
    return img


def render_content(slide: dict, index: int, page: int, total: int, seed: str) -> Image.Image:
    img = _background(f"{seed}{page}", (W + 40, H - 160) if page % 2 else (-40, H - 200), 420, 55)
    d = ImageDraw.Draw(img)
    _header(d, page, total)

    nf = font("serif_bold", 150)
    hf, hlines = fit_text(slide["heading"], "serif_bold", 68, 52, W - MARGIN * 2, 2, balanced=True)
    bf, blines = fit_text(slide["body"], "sans", 46, 34, W - MARGIN * 2, 9)
    h_lh, b_lh = int(hf.size * 1.35), int(bf.size * 1.75)
    block = 190 + h_lh * len(hlines) + 26 + 60 + b_lh * len(blines)
    top_area, bottom_area = 170, H - 150
    y0 = max(top_area, top_area + (bottom_area - top_area - block) // 2 - 30)
    d.text((MARGIN - 6, y0 - 20), f"{index:02d}", font=nf, fill=GOLD_DIM)
    y = y0 + 190
    for ln in hlines:
        d.text((MARGIN, y), ln, font=hf, fill=WHITE)
        y += h_lh
    y += 26
    d.line([(MARGIN, y), (MARGIN + 96, y)], fill=GOLD, width=4)
    y += 60
    for ln in blines:
        d.text((MARGIN, y), ln, font=bf, fill=SILVER)
        y += b_lh

    _footer_rule(d)
    ff = font("sans", 26)
    d.text((MARGIN, H - 88), "保存して、あとで見返そう", font=ff, fill=MUTED)
    if page < total:
        msg = "→"
        af = font("sans_bold", 34)
        d.text((W - MARGIN - af.getlength(msg), H - 92), msg, font=af, fill=GOLD)
    return img


def render_cta(post: dict, aud_cfg: dict, total: int, seed: str, brand: dict) -> Image.Image:
    img = _background(seed + "z", (W // 2, H // 2 - 80), 560, 95)
    d = ImageDraw.Draw(img)
    _header(d, total, total)

    y = 300
    lh = _paste_logo(img, W // 2, y, 760)
    if lh:
        y += lh + 70
    else:
        sparkle(d, W // 2, y + 40, 40)
        y += 130
        _spaced(d, (W // 2, y), "NEXT EMPEROR", font("serif_bold", 64), GOLD, 10, anchor_center=True)
        y += 110
        tg = font("sans", 34)
        tag = brand.get("tagline", "")
        d.text((W / 2 - tg.getlength(tag) / 2, y), tag, font=tg, fill=SILVER)
        y += 130

    d.line([(W // 2 - 60, y), (W // 2 + 60, y)], fill=GOLD, width=3)
    y += 70
    tf = font("serif_bold", 60)
    t = aud_cfg["cta_title"]
    d.text((W / 2 - tf.getlength(t) / 2, y), t, font=tf, fill=WHITE)
    y += 120
    lf = font("sans", 40)
    for ln in aud_cfg["cta_lines"]:
        d.text((W / 2 - lf.getlength(ln) / 2, y), ln, font=lf, fill=SILVER)
        y += 70

    _footer_rule(d)
    ff = font("sans", 26)
    msg = "フォローで次回の投稿もチェック"
    d.text((W / 2 - ff.getlength(msg) / 2, H - 88), msg, font=ff, fill=MUTED)
    return img


def render_post(post: dict, config: dict, out_dir: str | pathlib.Path, seed: str) -> list[pathlib.Path]:
    """カルーセル全スライドをJPEGで保存し、パスの一覧を返す。"""
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    aud_cfg = config["audiences"][post["audience"]]
    slides = post["slides"]
    total = len(slides) + 2
    images = [render_cover(post, aud_cfg, total, seed)]
    for i, s in enumerate(slides, start=1):
        images.append(render_content(s, i, i + 1, total, seed))
    images.append(render_cta(post, aud_cfg, total, seed, config["brand"]))
    paths = []
    for i, im in enumerate(images, start=1):
        p = out / f"slide{i:02d}.jpg"
        im.convert("RGB").save(p, "JPEG", quality=92, optimize=True, progressive=False)
        paths.append(p)
    return paths


def text_fits(post: dict) -> list[str]:
    """描画時にはみ出すかを事前チェック（生成結果の検証で使う）。"""
    issues = []
    _, tl = fit_text(post.get("title", ""), "serif_bold", 96, 64, W - MARGIN * 2, 3, balanced=True)
    if len(tl) > 3:
        issues.append("表紙タイトルが長すぎて3行に収まりません")
    for i, s in enumerate(post.get("slides", []), start=1):
        _, hl = fit_text(s.get("heading", ""), "serif_bold", 68, 52, W - MARGIN * 2, 2, balanced=True)
        if len(hl) > 2:
            issues.append(f"スライド{i}の見出しが2行に収まりません")
        _, bl = fit_text(s.get("body", ""), "sans", 46, 34, W - MARGIN * 2, 9)
        if len(bl) > 9:
            issues.append(f"スライド{i}の本文が長すぎます")
    return issues
