#!/usr/bin/env python3

# This script was written by Claude Opus 5.5.

"""Export a MediaWiki's current pages to a static HTML site through its API.

MediaWiki still does the rendering: for each page this asks the API for the
rendered article body (action=parse), rewrites internal links, and wraps it
in a minimal page with MathJax and a link to the page's history on GitHub.

Output layout (keeps old URLs working when served at the same domain):
    site/wiki/<Title>.html              one per page, served as /wiki/<Title>
    site/wiki/<Redirect>.html           small redirect stubs
    site/wiki/Special:AllPages.html
    site/index.html                     -> Main Page
    site/assets/style.css
    site/assets/mathjax/tex-svg.js      (with --download-mathjax)

Standard library only; needs Python 3.6+.
"""
import argparse
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict

# ---- settings you may want to change ---------------------------------------
SITE_NAME = "Issawiki"
ARTICLE_PATH = "/wiki/"          # the wiki's $wgArticlePath without "$1"
SOURCE_EXT = ".mediawiki"        # must match replay.py
# MathJax delimiters. Check the "math markup found" report at the end of a
# run and adjust these to match what your wiki's HTML actually contains.
# SimpleMathJax (used on the live wiki) turns <math> into [math]...[/math] and
# treats nothing else as math, so "$" stays a plain dollar sign.
MATH_INLINE = [["[math]", "[/math]"]]
MATH_DISPLAY = []
# -----------------------------------------------------------------------------

MATHJAX_URL = "https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"
URL_SAFE = "/:;@$!*(),~-._'"     # characters MediaWiki leaves unencoded


class Wiki:
    def __init__(self, api, host=None, cache_path=None):
        self.api, self.host, self.cache_path = api, host, cache_path
        self.cache, self.hits = {}, 0
        if cache_path and os.path.exists(cache_path):
            with open(cache_path, encoding="utf-8") as f:
                self.cache = json.load(f)
            print(f"Loaded {len(self.cache)} cached API responses from {cache_path}",
                  file=sys.stderr)

    def save_cache(self):
        if self.cache_path:
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, ensure_ascii=False, sort_keys=True, indent=0)

    def call(self, **params):
        params.update(format="json", formatversion="2")
        key = urllib.parse.urlencode(sorted(params.items()))
        if key in self.cache:
            self.hits += 1
            return self.cache[key]
        if not self.api:
            sys.exit(f"Not in the cache and no --api given: {key}")
        data = self._fetch(params)
        if self.cache_path:
            self.cache[key] = data
        return data

    def _fetch(self, params):
        url = self.api + "?" + urllib.parse.urlencode(params)
        headers = {"User-Agent": "static-archive-export/1.0"}
        if self.host:
            headers["Host"] = self.host
        for attempt in range(4):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                            timeout=60) as r:
                    raw = r.read()
                break
            except OSError as e:
                if attempt == 3:
                    raise
                print(f"  retrying after error: {e}", file=sys.stderr)
                time.sleep(2 * (attempt + 1))
        try:
            data = json.loads(raw)
        except ValueError:
            sys.exit(f"Not JSON from {url!r} (a bot check or wrong --api URL?):\n"
                     + raw[:300].decode("utf-8", "replace"))
        if "error" in data:
            sys.exit(f"API error for {params}: {data['error']}")
        return data

    def query_all(self, list_key, **params):
        cont = {}
        while True:
            data = self.call(action="query", **params, **cont)
            yield from data.get("query", {}).get(list_key, [])
            if "continue" not in data:
                return
            cont = data["continue"]


def page_slug(title):
    """Title -> path segment(s), e.g. 'Foo bar/Baz' -> 'Foo_bar/Baz'."""
    parts = title.replace(" ", "_").split("/")
    return "/".join(p if p not in ("", ".", "..") else p.replace(".", "%2E") or "_"
                    for p in parts)


REPO = ""
SITE_REPO = ""
FILE_LINKS = False   # set by --file-links: end links in .html for file:// browsing
WIKI_DIR = ARTICLE_PATH.strip("/")


def root_for(rel_path):
    """Relative prefix from an output file back to the site root,
    e.g. 'wiki/Foo/Bar.html' -> '../../'."""
    return "../" * rel_path.count("/")


def page_href(root, slug_encoded, frag=""):
    # Extensionless links (/wiki/Foo -> wiki/Foo.html), as hosts like Cloudflare
    # Pages serve them; same URLs MediaWiki used.
    return (f"{root}{WIKI_DIR}/{slug_encoded}" + (".html" if FILE_LINKS else "")
            + (f"#{frag}" if frag else ""))


def href_for_title(root, title):
    return page_href(root, urllib.parse.quote(page_slug(title), safe=URL_SAFE))


HREF_RE = re.compile(r'href="([^"]*)"')
SMJ_FADE_RE = re.compile(r'<span style="opacity:\s*\.?0?\.5;?">(?=\[math\])')


WLH_DEAD_RE = re.compile(r'[\s\u200e]*<span class="mw-whatlinkshere-tools">\('
                         r'<a data-wlh-dead=""[^>]*>[^<]*</a>\)</span>')
WLH_PREFIX = "Special:WhatLinksHere"
WLH_ANCHOR = "What_links_here"


def rewrite_links(body, root, wlh_pages=frozenset()):
    """wlh_pages: titles whose page has its own "What links here" section, so
    "(← links)" links can point there; other such links are removed."""
    def wlh_link(target):
        target = urllib.parse.unquote(target).replace("_", " ")
        if target in wlh_pages:
            return 'href="%s"' % html.escape(page_href(
                root, urllib.parse.quote(page_slug(target), safe=URL_SAFE), WLH_ANCHOR))
        return 'data-wlh-dead=""'

    def fix(m):
        url = html.unescape(m.group(1))
        parts = urllib.parse.urlsplit(url)
        if parts.scheme or parts.netloc:
            return m.group(0)                     # external link
        if parts.path.startswith(ARTICLE_PATH):
            slug = parts.path[len(ARTICLE_PATH):]
            if urllib.parse.unquote(slug).startswith(WLH_PREFIX + "/"):
                return wlh_link(urllib.parse.unquote(slug)[len(WLH_PREFIX) + 1:])
            return 'href="%s"' % html.escape(page_href(root, slug, parts.fragment))
        if parts.path.endswith("index.php"):
            q = urllib.parse.parse_qs(parts.query)
            title = q.get("title", [""])[0]
            if title.replace(" ", "_") == WLH_PREFIX:
                return wlh_link(q.get("target", [""])[0])
            if title and not ({"action", "redlink", "oldid", "diff"} & q.keys()):
                return 'href="%s"' % html.escape(href_for_title(root, title))
            return 'data-dead-link=""'            # edit/redlink/history: drop
        return m.group(0)
    return WLH_DEAD_RE.sub("", HREF_RE.sub(fix, body))


def github_links(repo, branch, title):
    path = urllib.parse.quote(title.replace(" ", "_") + SOURCE_EXT, safe="/:")
    return (f"https://github.com/{repo}/commits/{branch}/{path}",
            f"https://github.com/{repo}/blob/{branch}/{path}")


def shell(root, title, heading_html, body, footer_html="", hist=None, src=None):
    mj = json.dumps({"tex": {"inlineMath": MATH_INLINE, "displayMath": MATH_DISPLAY,
                             "processEscapes": True}})
    e = html.escape
    right_tabs = '<a class="selected">Read</a>'
    if src:
        right_tabs += f'<a href="{e(src)}">View source</a>'
    if hist:
        right_tabs += f'<a href="{e(hist)}">View history</a>'
    repo_link = (f'<li><a href="https://github.com/{e(REPO)}">Revision history (git)</a></li>'
                 if REPO else "")
    if SITE_REPO:
        repo_link += (f'\n      <li><a href="https://github.com/{e(SITE_REPO)}">'
                      'Site source &amp; scripts</a></li>')
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} – {e(SITE_NAME)}</title>
<link rel="stylesheet" href="{root}assets/style.css">
<script>window.MathJax = {mj};</script>
<script defer src="{root}assets/mathjax/tex-svg.js"></script>
<script defer src="{root}assets/pages.js"></script>
</head>
<body>
<div id="sidebar">
  <a class="logo" href="{e(href_for_title(root, 'Main Page'))}">{e(SITE_NAME)}</a>
  <nav>
    <ul>
      <li><a href="{e(href_for_title(root, 'Main Page'))}">Main page</a></li>
      <li><a href="{e(page_href(root, 'Special:AllPages'))}">All pages</a></li>
      <li><a href="#" class="random-page" data-root="{e(root)}">Random page</a></li>
    </ul>
    <h3>Archive</h3>
    <ul>
      {repo_link}
    </ul>
  </nav>
</div>
<div id="tabs">
  <div class="left"><a class="selected">Page</a></div>
  <div class="right">{right_tabs}</div>
</div>
<main id="content">
<h1>{heading_html}</h1>
<div id="siteSub">From {e(SITE_NAME)} (static archive)</div>
{body}
</main>
<footer>
{footer_html}
<p>This is a static copy of a MediaWiki that used to be hosted at this same address.</p>
</footer>
</body>
</html>
"""


def redirect_stub(root, target_title, frag):
    url = href_for_title(root, target_title) + (f"#{frag}" if frag else "")
    u = html.escape(url)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Redirecting to {html.escape(target_title)}</title>
<link rel="canonical" href="{u}">
<meta http-equiv="refresh" content="0; url={u}">
<script>location.replace({json.dumps(url)});</script>
</head><body><p>Redirecting to <a href="{u}">{html.escape(target_title)}</a>.</p></body></html>
"""


CSS = """\
* { box-sizing: border-box; }
body { margin:0; background:#f6f6f6; color:#202122;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Lato, "Helvetica Neue",
    Arial, sans-serif; }
a { color:#0645ad; text-decoration:none; }
a:visited { color:#0b0080; }
a:hover { text-decoration:underline; }
a[data-dead-link] { color:#ba0000; cursor:default; text-decoration:none; }

/* sidebar */
#sidebar { position:absolute; top:0; left:0; width:11rem; padding:1.2em .5em 1em 1.3em;
  font-size:.875rem; }
#sidebar .logo { display:block; font-family:"Linux Libertine", Georgia, Times, serif;
  font-size:1.6em; color:#202122; margin:.6em 0 2.4em; line-height:1.2; }
#sidebar .logo:hover { text-decoration:none; }
#sidebar ul { list-style:none; margin:0 0 1.2em; padding:0; }
#sidebar li { padding:.25em 0; line-height:1.15; }
#sidebar h3 { font-size:.95em; font-weight:normal; color:#54595d; margin:.5em 0 .3em;
  padding-bottom:.3em; border-bottom:1px solid #c8ccd1; }

/* tabs */
#tabs { margin-left:11rem; padding-top:2.5em; display:flex; justify-content:space-between;
  font-size:.875rem; }
#tabs div { display:flex; }
#tabs a { padding:.55em .9em .45em; border:1px solid transparent; border-bottom:0;
  background:linear-gradient(to bottom, rgba(255,255,255,0) 0, #fff 100%); }
#tabs a + a { border-left:1px solid #c8ccd1; }
#tabs a.selected { background:#fff; color:#202122; border-color:#a7d7f9; margin-bottom:-1px; }

/* content */
#content { margin-left:11rem; background:#fff; border:1px solid #a7d7f9; border-right:0;
  padding:1.25em 1.5em 1.5em; font-size:.9375rem; line-height:1.6; min-height:30em; }
#content h1, #content h2 { font-family:"Linux Libertine", Georgia, Times, serif;
  font-weight:normal; border-bottom:1px solid #a2a9b1; margin:1em 0 .25em; line-height:1.3; }
#content h1 { font-size:1.9em; margin-top:0; }
#content h2 { font-size:1.5em; padding-bottom:0; }
#content h3, #content h4, #content h5, #content h6 { font-size:1.07em; font-weight:bold;
  margin:.3em 0 0; padding-top:.5em; }
#siteSub { font-size:.85em; color:#54595d; margin:-.1em 0 1.2em; }
#content p { margin:.4em 0 .6em; }
#content ul { margin:.3em 0 0 1.6em; padding:0; list-style-type:disc; }
#content ol { margin:.3em 0 0 3.2em; padding:0; }
#content li { margin-bottom:.1em; }
#content dd { margin-left:1.6em; }
pre, code, tt, kbd { font-family: Menlo, Consolas, "Liberation Mono", monospace; font-size:.9em; }
code { background:#f8f9fa; border:1px solid #eaecf0; border-radius:2px; padding:1px 4px; }
pre { background:#f8f9fa; border:1px solid #eaecf0; padding:1em; white-space:pre-wrap;
  line-height:1.3; overflow-x:auto; }
pre code { border:0; padding:0; }
blockquote { margin:1em 0; padding:0 1.6em; border-left:4px solid #eaecf0; }
a.external::after { content:"\\2197"; font-size:.75em; margin-left:.15em; color:#72777d; }
table.wikitable { background:#f8f9fa; border:1px solid #a2a9b1; border-collapse:collapse;
  margin:1em 0; color:#202122; }
table.wikitable th, table.wikitable td { border:1px solid #a2a9b1; padding:.2em .4em; }
table.wikitable th { background:#eaecf0; text-align:center; }
#toc, .toc { display:table; background:#f8f9fa; border:1px solid #a2a9b1; padding:.5em .9em;
  font-size:.95em; margin:.5em 0 1em; }
#toc .toctitle, .toc .toctitle { text-align:center; }
#toc h2, .toc h2 { font-family:inherit; font-size:1em; font-weight:bold; border:0; margin:0;
  display:inline; }
#toc ul, .toc ul { list-style:none; margin:.3em 0; padding:0; }
#toc ul ul, .toc ul ul { margin:0 0 0 2em; }
.tocnumber { color:#202122; padding-right:.4em; }
.mw-editsection, .toctoggle { display:none; }
.math mjx-container, mjx-container { font-size:105% !important; }
mjx-container[display="true"] { overflow-x:auto; overflow-y:hidden; }
.catlinks { border:1px solid #a2a9b1; background:#f8f9fa; padding:.4em .6em; margin-top:1.5em;
  font-size:.95em; }
.catlinks ul { display:inline; margin:0 !important; padding:0; list-style:none !important; }
.catlinks li { display:inline-block; margin:0 !important; padding:0 .5em;
  border-left:1px solid #a2a9b1; line-height:1.25; }
.catlinks li:first-child { border-left:0; }
.allpages ul { columns:3 15em; }

footer { margin-left:11rem; padding:.75em 1.5em 2em; font-size:.75rem; color:#202122; }
footer p { margin:.3em 0; }

@media (max-width: 720px) {
  #sidebar { position:static; width:auto; padding:.5em 1em; }
  #sidebar .logo { margin:.2em 0 .4em; }
  #sidebar ul { display:flex; flex-wrap:wrap; gap:.2em 1em; margin-bottom:.4em; }
  #sidebar h3 { display:none; }
  #tabs, #content, footer { margin-left:0; }
  #tabs { padding-top:.5em; overflow-x:auto; }
  #content { border-left:0; padding:1em; }
}
"""

PAGES_JS_TEMPLATE = """window.WIKI_PAGES = %s;
document.addEventListener("click", function (ev) {
  var a = ev.target.closest && ev.target.closest("a.random-page");
  if (!a || !window.WIKI_PAGES.length) return;
  ev.preventDefault();
  var p = window.WIKI_PAGES[Math.floor(Math.random() * window.WIKI_PAGES.length)];
  location.href = a.getAttribute("data-root") + p;
});
"""


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", help="URL of the wiki's api.php (not needed if --cache has everything)")
    ap.add_argument("--cache", help="JSON file of saved API responses: read if it exists, "
                    "and anything newly fetched is added to it")
    ap.add_argument("--repo", required=True,
                    help="GitHub repo with the revision history, e.g. riceissa/issawiki-archive")
    ap.add_argument("--site-repo",
                    help="GitHub repo with this static site and its scripts, e.g. riceissa/issawiki-static")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--out", default="site")
    ap.add_argument("--host", help="Host header to send (when --api points at 127.0.0.1)")
    ap.add_argument("--base", default="", help="URL prefix if not served at the domain root, "
                    "e.g. /issawiki (only used by 404.html; all other links are relative)")
    ap.add_argument("--file-links", action="store_true",
                    help="end page links in .html so the site can be browsed straight from "
                         "disk (file://) without a web server")
    ap.add_argument("--download-mathjax", action="store_true",
                    help="download MathJax into OUT/assets/mathjax/ (skipped if already there)")
    args = ap.parse_args()
    global FILE_LINKS, REPO, SITE_REPO
    FILE_LINKS = args.file_links
    REPO = args.repo
    SITE_REPO = args.site_repo or ""
    abs_root = args.base.rstrip("/") + "/"
    if not args.api and not (args.cache and os.path.exists(args.cache)):
        ap.error("give --api, or --cache pointing at an existing cache file")
    wiki = Wiki(args.api, args.host, args.cache)

    si = wiki.call(action="query", meta="siteinfo", siprop="namespaces")
    namespaces = {int(k): v for k, v in si["query"]["namespaces"].items() if int(k) >= 0}
    cat_ns = namespaces.get(14, {}).get("name", "Category")

    content, redirects = [], []
    for ns in sorted(namespaces):
        for p in wiki.query_all("allpages", list="allpages", apnamespace=ns,
                                aplimit="max", apfilterredir="nonredirects"):
            content.append(p["title"])
        for p in wiki.query_all("allpages", list="allpages", apnamespace=ns,
                                aplimit="max", apfilterredir="redirects"):
            redirects.append(p["title"])
    print(f"{len(content)} content pages + {len(redirects)} redirects "
          f"= {len(content) + len(redirects)} total", file=sys.stderr)

    # last-edit timestamps, 50 titles per request
    last_edit = {}
    for i in range(0, len(content), 50):
        data = wiki.call(action="query", prop="revisions", rvprop="timestamp",
                         titles="|".join(content[i:i + 50]))
        for p in data["query"]["pages"]:
            if p.get("revisions"):
                last_edit[p["title"]] = p["revisions"][0]["timestamp"][:10]

    # redirect targets, 50 at a time
    redirect_to = {}
    for i in range(0, len(redirects), 50):
        data = wiki.call(action="query", titles="|".join(redirects[i:i + 50]), redirects="1")
        for r in data["query"].get("redirects", []):
            redirect_to[r["from"]] = (r["to"], r.get("tofragment", ""))

    # parse every content page
    pages, members = {}, defaultdict(list)
    for n, title in enumerate(content, 1):
        print(f"[{n}/{len(content)}] {title}", file=sys.stderr)
        p = wiki.call(action="parse", page=title, prop="text|categories|displaytitle",
                      disableeditsection="1", disablelimitreport="1")["parse"]
        cats = sorted({c["category"].replace("_", " ") for c in p.get("categories", [])})
        pages[title] = (p.get("displaytitle") or html.escape(title), p["text"], cats)
        for c in cats:
            members[c].append(title)

    wiki.save_cache()
    if args.cache:
        print(f"API cache: {wiki.hits} responses reused, saved to {args.cache}", file=sys.stderr)

    formulas = sum(body.count(start) for _, body, _ in pages.values()
                   for start, _ in MATH_INLINE + MATH_DISPLAY)

    # make sure every category used has a page, even if it was never created on the wiki
    for c in members:
        t = f"{cat_ns}:{c}"
        if t not in pages:
            pages[t] = (html.escape(t), "", [])

    wlh_pages = frozenset(t for t, (_, body, _) in pages.items()
                          if f'id="{WLH_ANCHOR}"' in body)

    out = args.out
    for title, (disp, body, cats) in pages.items():
        rel = f"{WIKI_DIR}/{page_slug(title)}.html"
        root = root_for(rel)
        body = rewrite_links(body, root, wlh_pages)
        # SimpleMathJax fades formulas until its own JS un-fades them; drop that
        body = SMJ_FADE_RE.sub('<span class="math">', body)
        if title.startswith(cat_ns + ":"):
            mem = sorted(members.get(title[len(cat_ns) + 1:], []), key=str.lower)
            if mem:
                body += (f'<h2>Pages in this category ({len(mem)})</h2>\n<ul>\n'
                         + "".join(f'<li><a href="{html.escape(href_for_title(root, m))}">'
                                   f'{html.escape(m)}</a></li>\n' for m in mem) + "</ul>\n")
        if cats:
            body += ('<div class="catlinks">Categories: <ul>'
                     + "".join(f'<li><a href="{html.escape(href_for_title(root, cat_ns + ":" + c))}">'
                               f'{html.escape(c)}</a></li>' for c in cats) + "</ul></div>\n")
        footer, hist, src = "", None, None
        if title in content:
            hist, src = github_links(args.repo, args.branch, title)
        if title in last_edit:
            footer = f"<p>This page was last edited on {last_edit[title]}.</p>"
        write(os.path.join(out, rel), shell(root, title, disp, body, footer, hist, src))

    for r in redirects:
        if r in redirect_to:
            rel = f"{WIKI_DIR}/{page_slug(r)}.html"
            write(os.path.join(out, rel), redirect_stub(root_for(rel), *redirect_to[r]))
        else:
            print(f"warning: no target found for redirect {r!r}", file=sys.stderr)

    # All pages index, grouped by namespace
    by_ns = defaultdict(list)
    for t in content:
        prefix = t.split(":", 1)[0] if ":" in t else ""
        ns_name = prefix if any(prefix == v.get("name") for v in namespaces.values()) else ""
        by_ns[ns_name].append(t)
    rel = f"{WIKI_DIR}/Special:AllPages.html"
    root = root_for(rel)
    idx = ['<div class="allpages">']
    for ns_name in sorted(by_ns, key=lambda s: (s != "", s)):
        idx.append(f"<h2>{html.escape(ns_name or 'Articles')} ({len(by_ns[ns_name])})</h2>\n<ul>")
        idx += [f'<li><a href="{html.escape(href_for_title(root, t))}">{html.escape(t)}</a></li>'
                for t in sorted(by_ns[ns_name], key=str.lower)]
        idx.append("</ul>")
    idx.append("</div>")
    write(os.path.join(out, rel), shell(root, "All pages", "All pages", "\n".join(idx)))

    write(os.path.join(out, "index.html"), redirect_stub("", "Main Page", ""))
    write(os.path.join(out, "404.html"),
          # served at arbitrary depths, so this one page uses root-absolute links
          shell(abs_root, "Not found", "Page not found",
                f'<p>No page here. Try <a href="{page_href(abs_root, "Special:AllPages")}">'
                'all pages</a>.</p>'))
    write(os.path.join(out, "assets", "style.css"), CSS)
    main_ns = sorted(t for t in content if ":" not in t or not any(
        t.split(":", 1)[0] == v.get("name") for v in namespaces.values()))
    write(os.path.join(out, "assets", "pages.js"), PAGES_JS_TEMPLATE % json.dumps(
        [page_href("", urllib.parse.quote(page_slug(t), safe=URL_SAFE)) for t in main_ns]))

    print(f"\nWrote {len(pages)} pages and {len(redirect_to)} redirects to {out}/", file=sys.stderr)
    print(f"{formulas} formulas found for MathJax to typeset", file=sys.stderr)
    mj_path = os.path.join(out, "assets", "mathjax", "tex-svg.js")
    if args.download_mathjax and not os.path.exists(mj_path):
        print(f"Downloading MathJax from {MATHJAX_URL}", file=sys.stderr)
        req = urllib.request.Request(MATHJAX_URL, headers={"User-Agent": "static-archive-export/1.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        os.makedirs(os.path.dirname(mj_path), exist_ok=True)
        with open(mj_path, "wb") as f:
            f.write(data)
        print(f"  saved {len(data) // 1024} KB to {mj_path}", file=sys.stderr)
    if not os.path.exists(mj_path):
        print("\nReminder: MathJax is missing; re-run with --download-mathjax.",
              file=sys.stderr)


if __name__ == "__main__":
    main()
