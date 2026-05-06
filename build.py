#!/usr/bin/env python3
"""
Build script for Japan Insights for Professionals.

What it does:
1. Scans articles/**/*.html.
2. Extracts article metadata from each article HTML.
3. Generates public/index.html from templates/index.template.html.
4. Generates public/articles.html from templates/articles.template.html.
5. Generates public/sitemap.xml.
6. Copies static assets and fixed pages into public/.
7. Refreshes tag-based Related articles / Read next blocks in article pages.

Cloudflare Pages settings:
- Build command: python build.py
- Build output directory: public

No external Python packages are required.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import html
import json
import re
import shutil
from typing import Iterable

SITE_BASE_URL = "https://japan-insight-site.pages.dev"
OUTPUT_DIR = Path("public")
LATEST_LIMIT = 5
RELATED_LIMIT = 3

CATEGORY_ORDER = ["Students", "Academia", "Industry", "Finance"]
CATEGORY_IDS = {
    "Students": "students",
    "Academia": "academia",
    "Industry": "industry",
    "Finance": "finance",
}
CATEGORY_LABELS = {
    "Students": "Students",
    "Academia": "Academia",
    "Industry": "Industry",
    "Finance": "Finance",
}

COPY_EXCLUDE_NAMES = {
    ".git",
    ".github",
    ".DS_Store",
    "public",
    "templates",
    "build.py",
}
GENERATED_ROOT_FILES = {"index.html", "articles.html", "sitemap.xml"}


@dataclass(frozen=True)
class Article:
    path: Path
    rel_path: str
    url_path: str
    title: str
    description: str
    category: str
    article_type: str
    tags: tuple[str, ...]
    date_published: str
    image_path: str
    image_alt: str

    @property
    def full_url(self) -> str:
        return f"{SITE_BASE_URL}/{self.url_path.lstrip('/')}"

    @property
    def date_obj(self) -> datetime:
        try:
            return datetime.strptime(self.date_published, "%Y-%m-%d")
        except ValueError:
            return datetime.min

    @property
    def date_display(self) -> str:
        if self.date_obj == datetime.min:
            return self.date_published
        return f"{self.date_obj.strftime('%B')} {self.date_obj.day}, {self.date_obj.year}"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def strip_tags(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value, flags=re.DOTALL)
    return html.unescape(" ".join(value.split()))


def meta_content(source: str, name: str) -> str | None:
    patterns = [
        rf'<meta\s+name=["\']{re.escape(name)}["\']\s+content=["\']([^"\']*)["\']\s*/?>',
        rf'<meta\s+content=["\']([^"\']*)["\']\s+name=["\']{re.escape(name)}["\']\s*/?>',
        rf'<meta\s+property=["\']{re.escape(name)}["\']\s+content=["\']([^"\']*)["\']\s*/?>',
        rf'<meta\s+content=["\']([^"\']*)["\']\s+property=["\']{re.escape(name)}["\']\s*/?>',
    ]
    for pattern in patterns:
        m = re.search(pattern, source, flags=re.IGNORECASE)
        if m:
            return html.unescape(m.group(1).strip())
    return None


def json_ld_value(source: str, key: str) -> str | None:
    m = re.search(r'<script\s+type=["\']application/ld\+json["\']\s*>\s*(.*?)\s*</script>', source, flags=re.DOTALL | re.IGNORECASE)
    if not m:
        return None
    raw = m.group(1)
    try:
        data = json.loads(raw)
        value = data.get(key)
        if isinstance(value, str):
            return value
    except json.JSONDecodeError:
        pass
    m2 = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]+)"', raw)
    return m2.group(1) if m2 else None


def infer_category(path: Path, source: str) -> str:
    category = meta_content(source, "jiprof:category")
    if category:
        return category
    parent = path.parent.name.lower()
    return {
        "students": "Students",
        "academia": "Academia",
        "industry": "Industry",
        "finance": "Finance",
    }.get(parent, parent.title())


def normalize_tags(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return tuple()
    parts = re.split(r"[,\s]+", raw)
    tags: list[str] = []
    seen: set[str] = set()
    for part in parts:
        tag = part.strip().lower()
        if not tag:
            continue
        if tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tuple(tags)


def extract_article(path: Path) -> Article:
    source = read_text(path)
    rel_path = path.as_posix()
    url_path = rel_path

    title = None
    m = re.search(r"<h1[^>]*>(.*?)</h1>", source, flags=re.DOTALL | re.IGNORECASE)
    if m:
        title = strip_tags(m.group(1))
    if not title:
        title = meta_content(source, "og:title") or json_ld_value(source, "headline") or path.stem
        title = title.replace(" | Japan Insights for Professionals", "")

    description = meta_content(source, "description") or json_ld_value(source, "description") or ""
    category = infer_category(path, source)
    article_type = meta_content(source, "jiprof:article-type") or "guide"
    tags = normalize_tags(meta_content(source, "jiprof:tags"))

    date_published = json_ld_value(source, "datePublished")
    if not date_published:
        m = re.search(r"Published\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})", source)
        if m:
            try:
                date_published = datetime.strptime(m.group(1), "%B %d, %Y").strftime("%Y-%m-%d")
            except ValueError:
                date_published = ""
    if not date_published:
        date_published = "2026-05-04"

    image = meta_content(source, "og:image") or json_ld_value(source, "image") or ""
    image_path = image.replace(SITE_BASE_URL, "") if image.startswith(SITE_BASE_URL) else image
    if image_path and not image_path.startswith("/"):
        image_path = "/" + image_path

    image_alt = title
    m = re.search(r'<figure\s+class=["\']article-feature-image["\'].*?<img[^>]+alt=["\']([^"\']*)["\']', source, flags=re.DOTALL | re.IGNORECASE)
    if m:
        image_alt = html.unescape(m.group(1).strip()) or title

    return Article(
        path=path,
        rel_path=rel_path,
        url_path=url_path,
        title=title,
        description=description,
        category=category,
        article_type=article_type,
        tags=tags,
        date_published=date_published,
        image_path=image_path,
        image_alt=image_alt,
    )


def find_articles() -> list[Article]:
    article_paths = sorted(Path("articles").glob("*/*.html"))
    articles = [extract_article(path) for path in article_paths]
    return sorted(articles, key=lambda a: (a.date_obj, a.rel_path), reverse=True)


def esc(value: str) -> str:
    return html.escape(value, quote=True)


def article_card(article: Article) -> str:
    category = CATEGORY_LABELS.get(article.category, article.category)
    return f'''          <article class="latest-item latest-item-with-thumb">
            <a class="article-thumb" href="/{esc(article.url_path)}" aria-label="Read {esc(article.title)}">
              <img src="{esc(article.image_path)}" alt="{esc(article.image_alt)}" loading="lazy">
            </a>

            <div>
              <p class="article-meta">{esc(category)} · {esc(article.date_display)}</p>
              <h3>
                <a href="/{esc(article.url_path)}">
                  {esc(article.title)}
                </a>
              </h3>
              <p>
                {esc(article.description)}
              </p>
            </div>
          </article>'''


def render_cards(articles: Iterable[Article]) -> str:
    return "\n\n".join(article_card(article) for article in articles)


def render_index(articles: list[Article]) -> str:
    template = read_text(Path("templates/index.template.html"))
    latest_html = render_cards(articles[:LATEST_LIMIT])
    return template.replace("{{LATEST_ARTICLES}}", latest_html)


def render_articles_page(articles: list[Article]) -> str:
    template = read_text(Path("templates/articles.template.html"))
    for category in CATEGORY_ORDER:
        cat_articles = [a for a in articles if a.category == category]
        placeholder = "{{" + CATEGORY_IDS[category].upper() + "_ARTICLES}}"
        template = template.replace(placeholder, render_cards(cat_articles))
    return template


def today_lastmod() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def render_sitemap(articles: list[Article]) -> str:
    static_urls = [
        (f"{SITE_BASE_URL}/", today_lastmod(), "weekly", "1.0"),
        (f"{SITE_BASE_URL}/articles.html", today_lastmod(), "weekly", "0.9"),
        (f"{SITE_BASE_URL}/about.html", "2026-05-04", "monthly", "0.6"),
        (f"{SITE_BASE_URL}/privacy.html", "2026-05-04", "yearly", "0.3"),
        (f"{SITE_BASE_URL}/disclaimer.html", "2026-05-04", "yearly", "0.3"),
    ]

    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod, changefreq, priority in static_urls:
        lines.extend([
            "  <url>",
            f"    <loc>{esc(loc)}</loc>",
            f"    <lastmod>{esc(lastmod)}</lastmod>",
            f"    <changefreq>{esc(changefreq)}</changefreq>",
            f"    <priority>{esc(priority)}</priority>",
            "  </url>",
        ])
    for article in sorted(articles, key=lambda a: a.url_path):
        lines.extend([
            "  <url>",
            f"    <loc>{esc(article.full_url)}</loc>",
            f"    <lastmod>{esc(article.date_published)}</lastmod>",
            "    <changefreq>monthly</changefreq>",
            "    <priority>0.8</priority>",
            "  </url>",
        ])
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def relation_score(current: Article, candidate: Article) -> tuple[int, datetime, str]:
    current_tags = set(current.tags)
    candidate_tags = set(candidate.tags)
    overlap = len(current_tags & candidate_tags)
    same_category = 1 if current.category == candidate.category else 0
    score = overlap * 10 + same_category * 3
    return (score, candidate.date_obj, candidate.rel_path)


def related_articles(current: Article, articles: list[Article]) -> list[Article]:
    candidates = [a for a in articles if a.rel_path != current.rel_path]
    ranked = sorted(candidates, key=lambda a: relation_score(current, a), reverse=True)
    return ranked[:RELATED_LIMIT]


def tags_attr(article: Article) -> str:
    return " ".join(article.tags)


def sidebar_related_html(items: list[Article]) -> str:
    cards = []
    for item in items:
        cards.append(f'''              <div class="sidebar-related-card" data-category="{esc(item.category)}" data-tags="{esc(tags_attr(item))}">
                <a class="sidebar-related-thumb" href="/{esc(item.url_path)}" aria-label="Read: {esc(item.title)}">
                  <img src="{esc(item.image_path)}" alt="{esc(item.image_alt)}" loading="lazy">
                </a>
                <a href="/{esc(item.url_path)}">{esc(item.title)}</a>
              </div>''')
    return f'''          <div class="sidebar-related-box" data-related-mode="tag-overlap">
            <h2>Related articles</h2>
            <div class="sidebar-related-list">
{chr(10).join(cards)}
            </div>
          </div>'''


def read_next_html(items: list[Article]) -> str:
    cards = []
    for item in items:
        cards.append(f'''              <div class="read-next-card" data-category="{esc(item.category)}" data-tags="{esc(tags_attr(item))}">
                <a class="read-next-thumb" href="/{esc(item.url_path)}" aria-label="Read: {esc(item.title)}">
                  <img src="{esc(item.image_path)}" alt="{esc(item.image_alt)}" loading="lazy">
                </a>
                <div class="read-next-content">
                  <a href="/{esc(item.url_path)}">{esc(item.title)}</a>
                  <p>{esc(item.description)}</p>
                </div>
              </div>''')
    return f'''          <section class="read-next-box" data-related-mode="tag-overlap">
            <h2>Read next</h2>
            <div class="read-next-grid">
{chr(10).join(cards)}
            </div>
          </section>'''


def ensure_article_data_attributes(source: str, article: Article) -> str:
    m = re.search(r'<article\s+class=["\']article-main["\']([^>]*)>', source)
    if not m:
        return source
    tag = m.group(0)
    if "data-category=" in tag and "data-tags=" in tag:
        return source
    new_tag = f'<article class="article-main" data-category="{esc(article.category)}" data-tags="{esc(tags_attr(article))}">'
    return source[:m.start()] + new_tag + source[m.end():]


def replace_or_insert_related_blocks(source: str, current: Article, articles: list[Article]) -> str:
    related = related_articles(current, articles)
    sidebar_block = sidebar_related_html(related)
    read_next_block = read_next_html(related)

    marker = '<div class="sidebar-related-box" data-related-mode="tag-overlap">'
    if marker in source:
        start = source.index(marker)
        aside_end = source.index("        </aside>", start)
        line_start = source.rfind("\n", 0, start) + 1
        source = source[:line_start] + sidebar_block + "\n" + source[aside_end:]
    else:
        source = source.replace("        </aside>", sidebar_block + "\n        </aside>", 1)

    marker = '<section class="read-next-box" data-related-mode="tag-overlap">'
    if marker in source:
        start = source.index(marker)
        end = source.index("          </section>", start) + len("          </section>")
        line_start = source.rfind("\n", 0, start) + 1
        source = source[:line_start] + read_next_block + source[end:]
    elif '<p class="article-footer-note">' in source:
        source = source.replace('<p class="article-footer-note">', read_next_block + "\n\n" + '<p class="article-footer-note">', 1)

    return ensure_article_data_attributes(source, current)


def update_public_article_pages(articles: list[Article]) -> None:
    article_by_rel = {a.rel_path: a for a in articles}
    for rel_path, article in article_by_rel.items():
        public_path = OUTPUT_DIR / rel_path
        if not public_path.exists():
            continue
        source = read_text(public_path)
        source = replace_or_insert_related_blocks(source, article, articles)
        write_text(public_path, source)


def copy_source_to_public() -> None:
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True)

    for item in Path(".").iterdir():
        name = item.name
        if name in COPY_EXCLUDE_NAMES:
            continue
        if name in GENERATED_ROOT_FILES:
            continue
        if item.is_dir():
            shutil.copytree(item, OUTPUT_DIR / name, ignore=shutil.ignore_patterns(".DS_Store"))
        elif item.is_file():
            shutil.copy2(item, OUTPUT_DIR / name)


def main() -> None:
    required_templates = [Path("templates/index.template.html"), Path("templates/articles.template.html")]
    missing = [str(path) for path in required_templates if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing template file(s): " + ", ".join(missing))

    articles = find_articles()
    if not articles:
        raise RuntimeError("No article HTML files found under articles/*/*.html")

    copy_source_to_public()
    write_text(OUTPUT_DIR / "index.html", render_index(articles))
    write_text(OUTPUT_DIR / "articles.html", render_articles_page(articles))
    write_text(OUTPUT_DIR / "sitemap.xml", render_sitemap(articles))
    update_public_article_pages(articles)

    print(f"Built {OUTPUT_DIR}/ with {len(articles)} articles.")
    print("Generated: index.html, articles.html, sitemap.xml")
    print("Updated: article related links in public article pages")


if __name__ == "__main__":
    main()
