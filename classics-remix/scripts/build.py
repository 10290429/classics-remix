#!/usr/bin/env python3
"""名著改編誌 建置腳本（LLM Wiki 架構）

用法：
  python3 scripts/build.py          檢查 wiki 並產生網頁
  python3 scripts/build.py --lint   只做健檢、不產生網頁，並列出較嚴格的警告

流程：raw/（原始資料）→ wiki/（AI 維護的知識頁）→ 本腳本 → 網頁
1. 讀取 wiki/works/*.md 與 wiki/concepts/*.md（YAML frontmatter ＋ Markdown 本文）
2. 依 CLAUDE.md 的規則檢查資料、[[連結]]、索引與日誌
3. 全部通過後，產生 index.html、upcoming.html、works/*.html、concepts/*.html
"""
import datetime
import glob
import html
import os
import re
import sys

import markdown
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIKI = os.path.join(ROOT, "wiki")
RAW = os.path.join(ROOT, "raw")
TODAY = datetime.date.today()
LINT = "--lint" in sys.argv
STALE_DAYS = 60

SITE_NAME = "名著改編誌"
SITE_EN = "Classics Remixed"

TYPES = ["電影", "影集", "動畫", "舞台", "網路影音", "其他"]
STATUSES = ["已定檔", "製作中", "開發中"]
MOTIFS = ["arch", "bolt", "circles", "stripe", "grid"]
LOG_ACTIONS = ["ingest", "query", "lint", "update"]
MIN_ADAPTATIONS = 6

REQUIRED_WORK = {
    "slug": str, "title_zh": str, "title_original": str, "author_zh": str,
    "author_original": str, "year": int, "year_note": str, "country": str,
    "form": str, "cover": dict, "tagline": str, "themes": list,
    "adaptations": list, "upcoming": list, "updated": str,
}
REQUIRED_ADAPT = {
    "title": str, "title_zh": str, "year": int, "type": str, "is_parody": bool,
    "creator": str, "approach": str, "note": str, "source": str, "concepts": list,
}
REQUIRED_UPCOMING = {
    "title": str, "type": str, "expected": str, "status": str,
    "note": str, "source": str, "checked": str,
}
REQUIRED_CONCEPT = {"slug": str, "title": str, "title_en": str, "summary": str, "updated": str}
WORK_SECTIONS = ["故事簡介", "為什麼一再被改編", "相關改編手法"]

# 與 CLAUDE.md 第九節同步
BANNED_WORDS = [
    "路徑", "揭示", "重構", "收束", "堆疊", "節奏", "漂浮", "斷裂", "生成",
    "具體", "迷宮", "真實", "建構", "實際", "旨在", "錯位", "支點", "推進",
    "節點", "載體", "而是", "清晰", "關鍵", "系統性", "結構性", "至關重要",
    "展示", "深入探討", "值得一提的是", "總而言之", "往往", "安排", "逐步", "詩意",
]
BANNED_MARKS = ["——"]

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EXPECTED_RE = re.compile(r"^(\d{4})(-(\d{2}))?(-(\d{2}))?$")
LINK_RE = re.compile(r"\[\[([a-z0-9-]+)(\|([^\]]+))?\]\]")
LOG_RE = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\] (\w+) \| .+$")


# ────────────────────────────────────────────────────────────
# 讀取 wiki
# ────────────────────────────────────────────────────────────

def read_page(path, errors):
    """回傳 (frontmatter dict, markdown 本文)。"""
    text = open(path, encoding="utf-8").read()
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        errors.append(f"{rel(path)}：缺少 YAML frontmatter（檔案開頭需以 --- 包住）")
        return None, ""
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as ex:
        errors.append(f"{rel(path)}：frontmatter 格式錯誤 {ex}")
        return None, ""
    for k in ("updated", "checked"):
        if isinstance(meta.get(k), datetime.date):
            meta[k] = meta[k].isoformat()
    return meta, m.group(2).strip()


def rel(path):
    return os.path.relpath(path, ROOT)


def sections(body):
    """將本文依 ## 標題切成 {標題: 內容}。"""
    out, cur = {}, None
    for line in body.splitlines():
        if line.startswith("## "):
            cur = line[3:].strip()
            out[cur] = []
        elif cur:
            out[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


# ────────────────────────────────────────────────────────────
# 檢查
# ────────────────────────────────────────────────────────────

def check_fields(obj, spec, where, errors):
    ok = True
    for key, typ in spec.items():
        if key not in obj:
            errors.append(f"{where}：缺少欄位 {key}")
            ok = False
        elif not isinstance(obj[key], typ) or (typ is int and isinstance(obj[key], bool)):
            errors.append(f"{where}：欄位 {key} 應為 {typ.__name__}")
            ok = False
    return ok


def check_text(text, where, errors):
    for mark in BANNED_MARKS:
        if mark in text:
            errors.append(f"{where}：出現禁用符號「{mark}」")
    for word in BANNED_WORDS:
        if word in text:
            errors.append(f"{where}：出現禁用詞「{word}」")


def expected_to_date(expected):
    m = EXPECTED_RE.match(expected)
    if not m:
        return None
    return datetime.date(int(m.group(1)), int(m.group(3) or 12), int(m.group(5) or 28))


def validate_work(w, body, path, concept_slugs, errors, warnings):
    where = rel(path)
    slug_file = os.path.splitext(os.path.basename(path))[0]
    if not check_fields(w, REQUIRED_WORK, where, errors):
        return
    if w["slug"] != slug_file:
        errors.append(f"{where}：slug「{w['slug']}」與檔名不一致")
    if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", w["slug"]):
        errors.append(f"{where}：slug 只能用小寫英文、數字與連字號")
    if not glob.glob(os.path.join(RAW, f"*-{w['slug']}.md")):
        errors.append(f"{where}：raw/ 裡找不到這部作品的原始資料（raw/YYYY-MM-DD-{w['slug']}.md）")
    if not DATE_RE.match(w["updated"]):
        errors.append(f"{where}：updated 格式應為 YYYY-MM-DD")

    c = w["cover"]
    if c.get("motif") not in MOTIFS:
        errors.append(f"{where}：cover.motif 只能是 {', '.join(MOTIFS)}")
    for k in ("bg", "ink"):
        if not re.match(r"^#[0-9A-Fa-f]{6}$", str(c.get(k, ""))):
            errors.append(f"{where}：cover.{k} 應為 #RRGGBB 色碼")

    check_text(w["tagline"], f"{where} tagline", errors)
    if len(w["tagline"]) > 24:
        errors.append(f"{where}：tagline 超過 24 字")
    for t in w["themes"]:
        check_text(t, f"{where} themes", errors)

    secs = sections(body)
    for name in WORK_SECTIONS:
        if name not in secs or not secs[name]:
            errors.append(f"{where}：本文缺少「## {name}」段落")
    check_text(body, f"{where} 本文", errors)
    summary_len = len(secs.get("故事簡介", ""))
    if summary_len and not 120 <= summary_len <= 260:
        errors.append(f"{where}：故事簡介 {summary_len} 字，建議 150 到 220 字")

    ads = w["adaptations"]
    if len(ads) < MIN_ADAPTATIONS:
        errors.append(f"{where}：改編只有 {len(ads)} 筆，至少需要 {MIN_ADAPTATIONS} 筆")
    if not any(a.get("is_parody") for a in ads):
        errors.append(f"{where}：至少需要 1 筆惡搞版（is_parody: true）")
    seen = set()
    for i, a in enumerate(ads):
        aw = f"{where} adaptations[{i}] {a.get('title', '?')}"
        if not check_fields(a, REQUIRED_ADAPT, aw, errors):
            continue
        if a["type"] not in TYPES:
            errors.append(f"{aw}：type「{a['type']}」不在允許清單 {TYPES}")
        if not a["source"].startswith("https://"):
            errors.append(f"{aw}：source 必須是 https:// 開頭的網址")
        if not (w["year"] <= a["year"] <= TODAY.year):
            errors.append(f"{aw}：year {a['year']} 不合理（早於原著或晚於今年）")
        for cs in a["concepts"]:
            if cs not in concept_slugs:
                errors.append(f"{aw}：concepts 標籤「{cs}」沒有對應的 wiki/concepts/{cs}.md")
        if not a["concepts"]:
            warnings.append(f"{aw}：沒有任何改編手法標籤")
        key = (a["title"], a["year"])
        if key in seen:
            errors.append(f"{aw}：與其他筆資料重複")
        seen.add(key)
        for f in ("approach", "note", "creator"):
            check_text(a[f], f"{aw} {f}", errors)

    for i, u in enumerate(w["upcoming"]):
        uw = f"{where} upcoming[{i}] {u.get('title', '?')}"
        if not check_fields(u, REQUIRED_UPCOMING, uw, errors):
            continue
        if u["type"] not in TYPES:
            errors.append(f"{uw}：type「{u['type']}」不在允許清單")
        if u["status"] not in STATUSES:
            errors.append(f"{uw}：status 只能是 {STATUSES}")
        if not u["source"].startswith("https://"):
            errors.append(f"{uw}：source 必須是 https:// 開頭的網址")
        if not DATE_RE.match(u["checked"]):
            errors.append(f"{uw}：checked 格式應為 YYYY-MM-DD")
        elif (TODAY - datetime.date.fromisoformat(u["checked"])).days > STALE_DAYS:
            warnings.append(f"{uw}：查證日期 {u['checked']} 已超過 {STALE_DAYS} 天，請重新查證")
        if u["expected"] != "未定":
            d = expected_to_date(u["expected"])
            if d is None:
                errors.append(f"{uw}：expected 應為 YYYY-MM-DD、YYYY-MM、YYYY 或 未定")
            elif d < TODAY:
                errors.append(f"{uw}：預計日期 {u['expected']} 已過，請移到 adaptations 並補上年份")
        check_text(u["note"], f"{uw} note", errors)


def validate_concept(cmeta, body, path, errors):
    where = rel(path)
    if not check_fields(cmeta, REQUIRED_CONCEPT, where, errors):
        return
    if cmeta["slug"] != os.path.splitext(os.path.basename(path))[0]:
        errors.append(f"{where}：slug 與檔名不一致")
    for name in ("這是什麼", "跨作品觀察"):
        if f"## {name}" not in body:
            errors.append(f"{where}：本文缺少「## {name}」段落")
    check_text(cmeta["summary"] + body, where, errors)


def check_links(pages, all_slugs, errors):
    for where, body in pages:
        for m in LINK_RE.finditer(body):
            if m.group(1) not in all_slugs:
                errors.append(f"{where}：[[{m.group(1)}]] 找不到對應頁面")


def lint_wiki(works, concepts, examples, errors, warnings):
    # index.md 必須列出所有頁面
    idx_path = os.path.join(WIKI, "index.md")
    if not os.path.exists(idx_path):
        errors.append("wiki/index.md 不存在")
    else:
        idx = open(idx_path, encoding="utf-8").read()
        listed = {m.group(1) for m in LINK_RE.finditer(idx)}
        for s in list(works) + list(concepts):
            if s not in listed:
                errors.append(f"wiki/index.md：沒有列出 [[{s}]]，ingest 後請更新索引")
    # log.md 格式
    log_path = os.path.join(WIKI, "log.md")
    if not os.path.exists(log_path):
        errors.append("wiki/log.md 不存在")
    else:
        heads = [l for l in open(log_path, encoding="utf-8").read().splitlines() if l.startswith("## ")]
        if not heads:
            errors.append("wiki/log.md：沒有任何紀錄")
        dates = []
        for h in heads:
            m = LOG_RE.match(h)
            if not m:
                errors.append(f"wiki/log.md：標題格式錯誤「{h}」，應為 ## [YYYY-MM-DD] 動作 | 標題")
            elif m.group(2) not in LOG_ACTIONS:
                errors.append(f"wiki/log.md：動作「{m.group(2)}」只能是 {LOG_ACTIONS}")
            else:
                dates.append(m.group(1))
        if dates != sorted(dates):
            errors.append("wiki/log.md：紀錄日期沒有依時間排列，日誌只能在末端新增")
        newest = max((w["updated"] for w in works.values()), default="")
        if dates and newest > dates[-1]:
            warnings.append(f"wiki/log.md：最新一筆紀錄 {dates[-1]} 早於作品更新日 {newest}，可能漏記")
    # 概念頁健康度
    for s, c in concepts.items():
        ex = examples.get(s, [])
        if not ex:
            errors.append(f"wiki/concepts/{s}.md：孤兒頁面，沒有任何改編標上這個手法")
        elif len({w["slug"] for w, _ in ex}) < 2:
            warnings.append(f"wiki/concepts/{s}.md：只有一部作品的例子，跨作品觀察可能不足")
        body = c["_body"]
        linked = {m.group(1) for m in LINK_RE.finditer(body)}
        for ws in {w["slug"] for w, _ in ex}:
            if ws not in linked:
                warnings.append(f"wiki/concepts/{s}.md：有《{works[ws]['title_zh']}》的例子，本文卻沒有連到 [[{ws}]]")
    # 作品頁的相關改編手法段落應與標籤一致
    for s, w in works.items():
        tagged = {c for a in w["adaptations"] for c in a.get("concepts", [])}
        linked = {m.group(1) for m in LINK_RE.finditer(sections(w["_body"]).get("相關改編手法", ""))}
        for c in tagged - linked:
            warnings.append(f"wiki/works/{s}.md：改編標有 {c}，但「相關改編手法」段落沒有連到 [[{c}]]")
        for c in linked - tagged:
            warnings.append(f"wiki/works/{s}.md：「相關改編手法」連到 [[{c}]]，但沒有任何改編標上它")


# ────────────────────────────────────────────────────────────
# 輸出工具
# ────────────────────────────────────────────────────────────

e = html.escape


def fmt_expected(exp):
    if exp == "未定":
        return "尚未定檔"
    m = EXPECTED_RE.match(exp)
    y, mo, d = m.group(1), m.group(3), m.group(5)
    if d:
        return f"{y} 年 {int(mo)} 月 {int(d)} 日"
    if mo:
        return f"{y} 年 {int(mo)} 月"
    return f"{y} 年"


def source_host(url):
    return re.sub(r"^https?://(www\.)?", "", url).split("/")[0]


def md_html(text, works, concepts, prefix):
    def repl(m):
        slug, label = m.group(1), m.group(3)
        if slug in works:
            return f'<a class="wikilink" href="{prefix}works/{slug}.html">{e(label or "《" + works[slug]["title_zh"] + "》")}</a>'
        c = concepts[slug]
        return f'<a class="wikilink wikilink-concept" href="{prefix}concepts/{slug}.html">{e(label or c["title"])}</a>'
    return markdown.markdown(LINK_RE.sub(repl, text))


def cover_html(w, size="md"):
    c = w["cover"]
    return (
        f'<div class="cover cover-{size}" style="--cover-bg:{c["bg"]};--cover-ink:{c["ink"]}">'
        f'<div class="motif motif-{c["motif"]}" aria-hidden="true"><span></span><span></span></div>'
        f'<div class="cover-text"><p class="cover-author">{e(w["author_zh"])}</p>'
        f'<p class="cover-title">{e(w["title_zh"])}</p>'
        f'<p class="cover-year">{w["year"]}</p></div></div>'
    )


def page(title, body, depth=0, active=""):
    prefix = "../" * depth
    def nav(href, label, key):
        cls = ' class="is-active"' if key == active else ""
        return f'<a href="{prefix}{href}"{cls}>{label}</a>'
    return f"""<!DOCTYPE html>
<html lang="zh-Hant-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="介紹世界經典名著，並整理每部作品的電影、影集、動畫、舞台與惡搞改編版本。">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700&family=Noto+Serif+TC:wght@500;700;900&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{prefix}assets/style.css">
</head>
<body>
<header class="site-header">
  <a class="brand" href="{prefix}index.html"><span class="brand-zh">{SITE_NAME}</span><span class="brand-en">{SITE_EN}</span></a>
  <nav class="site-nav">{nav("index.html#shelf", "書架", "shelf")}{nav("index.html#concepts", "改編手法", "concepts")}{nav("upcoming.html", "即將推出", "upcoming")}{nav("index.html#about", "收錄原則", "about")}</nav>
</header>
<main>
{body}
</main>
<footer class="site-footer">
  <p>{SITE_NAME}｜以 LLM Wiki 方式維護，所有資料皆附來源連結，最後建置於 {TODAY.isoformat()}。</p>
  <p>本站不收錄海報與劇照，書封皆以 CSS 繪製。</p>
</footer>
<script src="{prefix}assets/filter.js"></script>
</body>
</html>
"""


def all_upcoming(works):
    items = [(w, u) for w in works.values() for u in w["upcoming"]]
    def key(pair):
        d = expected_to_date(pair[1]["expected"]) if pair[1]["expected"] != "未定" else None
        return (d is None, d or datetime.date.max, pair[0]["title_zh"])
    return sorted(items, key=key)


def upcoming_card(w, u, depth):
    prefix = "../" * depth
    date_attr = u["expected"] if DATE_RE.match(u["expected"]) else ""
    return f"""<article class="up-card" style="--accent:{w['cover']['bg']}">
  <p class="up-date" data-date="{date_attr}">{fmt_expected(u['expected'])}</p>
  <h3 class="up-title">{e(u['title'])}</h3>
  <p class="up-meta"><a href="{prefix}works/{w['slug']}.html">改編自《{e(w['title_zh'])}》</a><span class="badge">{e(u['type'])}</span><span class="badge badge-status">{e(u['status'])}</span></p>
  <p class="up-note">{e(u['note'])}</p>
  <p class="source">資料來源：<a href="{e(u['source'])}" target="_blank" rel="noopener">{e(source_host(u['source']))}</a>，查證於 {u['checked']}</p>
</article>"""


def concept_card(c, n, prefix=""):
    return f"""<a class="concept-card" href="{prefix}concepts/{c['slug']}.html">
  <p class="eyebrow">{e(c['title_en'])}</p>
  <h3>{e(c['title'])}</h3>
  <p>{e(c['summary'])}</p>
  <p class="book-count">{n} 筆改編</p>
</a>"""


# ────────────────────────────────────────────────────────────
# 頁面
# ────────────────────────────────────────────────────────────

def build_index(works, concepts, examples):
    total_adapt = sum(len(w["adaptations"]) for w in works.values())
    total_parody = sum(1 for w in works.values() for a in w["adaptations"] if a["is_parody"])
    ups = all_upcoming(works)
    shelf = []
    for w in works.values():
        n_up = len(w["upcoming"])
        shelf.append(f"""<a class="book" href="works/{w['slug']}.html">
  {cover_html(w)}
  <div class="book-info">
    <h3>{e(w['title_zh'])}</h3>
    <p class="book-sub">{e(w['author_zh'])}｜{w['year']}｜{e(w['form'])}</p>
    <p class="book-tagline">{e(w['tagline'])}</p>
    <p class="book-count">{len(w['adaptations'])} 筆改編{f'・{n_up} 項即將推出' if n_up else ''}</p>
  </div>
</a>""")
    ccards = "\n".join(concept_card(c, len(examples.get(s, []))) for s, c in concepts.items())
    next_up = "\n".join(upcoming_card(w, u, 0) for w, u in ups[:3])
    body = f"""
<section class="hero">
  <p class="eyebrow">世界名著 × 改編版本資料庫</p>
  <h1>同一個故事，<br>每個時代都重講一次。</h1>
  <p class="lede">這裡收集經典名著，以及它們被拍成電影、影集、動畫、搬上舞台，甚至被惡搞成殭屍片的各種版本。先讀懂原著，再看看後人怎麼重新詮釋，順便知道下一個版本何時推出。</p>
  <dl class="stats">
    <div><dt>名著</dt><dd>{len(works)}</dd></div>
    <div><dt>改編版本</dt><dd>{total_adapt}</dd></div>
    <div><dt>改編手法</dt><dd>{len(concepts)}</dd></div>
    <div><dt>惡搞版</dt><dd>{total_parody}</dd></div>
    <div><dt>即將推出</dt><dd>{len(ups)}</dd></div>
  </dl>
</section>

<section class="section" id="shelf">
  <div class="section-head"><h2>書架</h2><p>點開任一本書，查看原著介紹與完整改編年表。</p></div>
  <div class="shelf">
{chr(10).join(shelf)}
  </div>
</section>

<section class="section" id="concepts">
  <div class="section-head"><h2>改編手法</h2><p>跨作品整理的六種改編方式，每一種都連回各部名著的實例。</p></div>
  <div class="concept-grid">
{ccards}
  </div>
</section>

<section class="section">
  <div class="section-head"><h2>最近要推出的</h2><p><a href="upcoming.html">查看全部即將推出項目 →</a></p></div>
  <div class="up-grid">
{next_up}
  </div>
</section>

<section class="section about" id="about">
  <div class="section-head"><h2>收錄原則</h2></div>
  <div class="about-cols">
    <div><h3>原著先讀懂</h3><p>每部作品都有故事簡介、核心主題，以及它一再被改編的原因，沒讀過原著也能跟上。</p></div>
    <div><h3>改編看得全</h3><p>依年份排列電影、影集、動畫、舞台與網路影音，每筆都寫出改編手法，並連到跨作品的手法整理頁。</p></div>
    <div><h3>資訊可查證</h3><p>每一筆資料都附來源連結，即將推出的項目另標查證日期。查不到可靠來源的消息，本站寧可不收。</p></div>
  </div>
</section>
"""
    return page(f"{SITE_NAME}｜世界名著與改編版本", body, 0, "shelf")


def build_work(w, works, concepts):
    adapts = sorted(w["adaptations"], key=lambda a: (a["year"], a["title"]))
    counts = {t: sum(1 for a in adapts if a["type"] == t) for t in TYPES}
    n_parody = sum(1 for a in adapts if a["is_parody"])
    buttons = [f'<button class="chip is-active" data-filter="all">全部 <span>{len(adapts)}</span></button>']
    for t in TYPES:
        if counts[t]:
            buttons.append(f'<button class="chip" data-filter="{t}">{t} <span>{counts[t]}</span></button>')
    buttons.append(f'<button class="chip chip-parody" data-filter="parody">惡搞版 <span>{n_parody}</span></button>')

    rows = []
    for a in adapts:
        zh = a["title_zh"]
        title_line = f'<div><h3>{e(zh)}</h3><p class="orig">{e(a["title"])}</p></div>' if zh else f'<div><h3>{e(a["title"])}</h3></div>'
        parody = '<span class="badge badge-parody">惡搞版</span>' if a["is_parody"] else ""
        tags = "".join(f'<a class="tag" href="../concepts/{c}.html">{e(concepts[c]["title"])}</a>' for c in a["concepts"])
        rows.append(f"""<li class="adapt" data-type="{a['type']}" data-parody="{str(a['is_parody']).lower()}">
  <p class="adapt-year">{a['year']}</p>
  <div class="adapt-body">
    <div class="adapt-head">{title_line}<p class="badges"><span class="badge">{a['type']}</span>{parody}</p></div>
    <p class="creator">{e(a['creator'])}</p>
    <p><strong>改編手法</strong>{e(a['approach'])}</p>
    <p class="note">{e(a['note'])}</p>
    <p class="adapt-foot">{f'<span class="tags">{tags}</span>' if tags else ''}<span class="source">來源：<a href="{e(a['source'])}" target="_blank" rel="noopener">{e(source_host(a['source']))}</a></span></p>
  </div>
</li>""")

    if w["upcoming"]:
        ups = sorted(w["upcoming"], key=lambda u: (u["expected"] == "未定", u["expected"]))
        up_html = "\n".join(upcoming_card(w, u, 1) for u in ups)
    else:
        up_html = '<p class="empty">目前沒有查到即將推出的新版本。</p>'

    secs = sections(w["_body"])
    themes = "".join(f"<li>{e(t)}</li>" for t in w["themes"])
    year_note = f"（{e(w['year_note'])}）" if w["year_note"] else ""
    body = f"""
<article class="work" style="--accent:{w['cover']['bg']};--accent-ink:{w['cover']['ink']}">
  <p class="crumb"><a href="../index.html#shelf">書架</a> / {e(w['title_zh'])}</p>
  <header class="work-hero">
    {cover_html(w, "lg")}
    <div class="work-meta">
      <p class="eyebrow">{e(w['form'])}・{e(w['country'])}</p>
      <h1>{e(w['title_zh'])}</h1>
      <p class="orig-title">{e(w['title_original'])}</p>
      <p class="tagline">{e(w['tagline'])}</p>
      <dl class="facts">
        <div><dt>作者</dt><dd>{e(w['author_zh'])}<span>{e(w['author_original'])}</span></dd></div>
        <div><dt>出版</dt><dd>{w['year']} 年{year_note}</dd></div>
        <div><dt>已收錄改編</dt><dd>{len(adapts)} 筆，其中惡搞版 {n_parody} 筆</dd></div>
      </dl>
    </div>
  </header>

  <section class="section two-col">
    <div class="prose">
      <h2>故事簡介</h2>
      {md_html(secs['故事簡介'], works, concepts, '../')}
    </div>
    <div>
      <h2>核心主題</h2>
      <ul class="themes">{themes}</ul>
      <h2>為什麼一再被改編</h2>
      <div class="prose">{md_html(secs['為什麼一再被改編'], works, concepts, '../')}</div>
      <h2>相關改編手法</h2>
      <div class="prose related">{md_html(secs['相關改編手法'], works, concepts, '../')}</div>
    </div>
  </section>

  <section class="section" id="adaptations">
    <div class="section-head"><h2>改編年表</h2><p>依首映、首播或首演年份排列，可用下方按鈕篩選類型。</p></div>
    <div class="filters" role="toolbar" aria-label="依類型篩選">{''.join(buttons)}</div>
    <ol class="timeline">
{chr(10).join(rows)}
    </ol>
  </section>

  <section class="section" id="upcoming">
    <div class="section-head"><h2>即將推出</h2><p>上映日期以查證日當下的公開資訊為準。</p></div>
    <div class="up-grid">
{up_html}
    </div>
  </section>

  <p class="updated">本頁資料更新於 {w['updated']}</p>
</article>
"""
    return page(f"{w['title_zh']}的改編版本｜{SITE_NAME}", body, 1)


def build_concept(c, works, concepts, examples):
    ex = examples.get(c["slug"], [])
    groups = []
    for ws, w in works.items():
        items = [a for ww, a in ex if ww["slug"] == ws]
        if not items:
            continue
        lis = "".join(
            f'<li><span class="ex-year">{a["year"]}</span><span class="ex-title">{e(a["title_zh"] or a["title"])}</span>'
            f'<span class="ex-type">{a["type"]}</span><p>{e(a["approach"])}</p></li>'
            for a in sorted(items, key=lambda a: a["year"]))
        groups.append(f"""<div class="ex-group" style="--accent:{w['cover']['bg']}">
  <h3><a href="../works/{ws}.html">《{e(w['title_zh'])}》</a><span>{len(items)} 筆</span></h3>
  <ul>{lis}</ul>
</div>""")
    others = "\n".join(concept_card(o, len(examples.get(s, [])), "../") for s, o in concepts.items() if s != c["slug"])
    body = f"""
<article class="concept">
  <p class="crumb"><a href="../index.html#concepts">改編手法</a> / {e(c['title'])}</p>
  <header class="hero hero-sm">
    <p class="eyebrow">{e(c['title_en'])}</p>
    <h1>{e(c['title'])}</h1>
    <p class="lede">{e(c['summary'])}</p>
  </header>
  <section class="section two-col">
    <div class="prose">{md_html(c['_body'], works, concepts, '../')}</div>
    <div>
      <h2>本站收錄的例子</h2>
      <p class="hint">以下清單由各作品頁的改編標籤自動彙整，共 {len(ex)} 筆。</p>
      {''.join(groups)}
    </div>
  </section>
  <section class="section">
    <div class="section-head"><h2>其他改編手法</h2></div>
    <div class="concept-grid">
{others}
    </div>
  </section>
  <p class="updated">本頁更新於 {c['updated']}</p>
</article>
"""
    return page(f"{c['title']}｜改編手法｜{SITE_NAME}", body, 1, "concepts")


def build_upcoming(works):
    cards = "\n".join(upcoming_card(w, u, 0) for w, u in all_upcoming(works))
    body = f"""
<section class="hero hero-sm">
  <p class="eyebrow">Coming Soon</p>
  <h1>即將推出</h1>
  <p class="lede">所有收錄名著的新改編，依預計日期排列，尚未定檔的項目放在最後。每一筆都標有查證日期，日期過後請以最新消息為準。</p>
</section>
<section class="section">
  <div class="up-grid up-grid-wide">
{cards}
  </div>
</section>
"""
    return page(f"即將推出｜{SITE_NAME}", body, 0, "upcoming")


# ────────────────────────────────────────────────────────────

def main():
    errors, warnings = [], []

    concepts = {}
    for f in sorted(glob.glob(os.path.join(WIKI, "concepts", "*.md"))):
        meta, body = read_page(f, errors)
        if meta is None:
            continue
        validate_concept(meta, body, f, errors)
        meta["_body"] = body
        concepts[meta.get("slug", os.path.basename(f))] = meta

    works = {}
    for f in sorted(glob.glob(os.path.join(WIKI, "works", "*.md"))):
        meta, body = read_page(f, errors)
        if meta is None:
            continue
        validate_work(meta, body, f, set(concepts), errors, warnings)
        meta["_body"] = body
        works[meta.get("slug", os.path.basename(f))] = meta
    if not works:
        errors.append("wiki/works/ 裡沒有任何作品頁")

    all_slugs = set(works) | set(concepts)
    overlap = set(works) & set(concepts)
    for s in overlap:
        errors.append(f"slug「{s}」同時用在作品與概念頁，請改名")
    pages = [(f"wiki/works/{s}.md", w["_body"]) for s, w in works.items()]
    pages += [(f"wiki/concepts/{s}.md", c["_body"]) for s, c in concepts.items()]
    check_links(pages, all_slugs, errors)

    examples = {}
    for w in works.values():
        for a in w.get("adaptations", []):
            for c in a.get("concepts", []):
                if c in concepts:
                    examples.setdefault(c, []).append((w, a))
    try:
        lint_wiki(works, concepts, examples, errors, warnings)
    except (KeyError, TypeError) as ex:
        errors.append(f"健檢中斷：資料欄位不完整（{ex}），請先修正上方錯誤")

    if errors:
        print(f"✗ 檢查未通過，共 {len(errors)} 個問題：")
        for err in errors:
            print("  -", err)
        sys.exit(1)

    n_adapt = sum(len(w["adaptations"]) for w in works.values())
    n_up = sum(len(w["upcoming"]) for w in works.values())
    summary = f"{len(works)} 部名著、{len(concepts)} 個改編手法、{n_adapt} 筆改編、{n_up} 項即將推出"

    if warnings:
        print(f"! {len(warnings)} 個提醒（不影響建置，lint 時請處理）：")
        for wa in warnings:
            print("  -", wa)

    if LINT:
        print(f"✓ 健檢完成：{summary}")
        return

    works = dict(sorted(works.items(), key=lambda kv: kv[1]["year"]))
    outputs = {"index.html": build_index(works, concepts, examples), "upcoming.html": build_upcoming(works)}
    for s, w in works.items():
        outputs[f"works/{s}.html"] = build_work(w, works, concepts)
    for s, c in concepts.items():
        outputs[f"concepts/{s}.html"] = build_concept(c, works, concepts, examples)

    for folder in ("works", "concepts"):
        os.makedirs(os.path.join(ROOT, folder), exist_ok=True)
        for old in os.listdir(os.path.join(ROOT, folder)):
            if old.endswith(".html") and f"{folder}/{old}" not in outputs:
                print(f"  ! {folder}/{old} 沒有對應的 wiki 頁面，請確認是否要刪除（腳本不會自動刪除）")

    for relpath, content in outputs.items():
        with open(os.path.join(ROOT, relpath), "w", encoding="utf-8") as fh:
            fh.write(content)

    print(f"✓ 全部通過：{summary}")
    for relpath in outputs:
        print("  →", relpath)


if __name__ == "__main__":
    main()
