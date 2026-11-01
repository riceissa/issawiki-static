#!/usr/bin/env python3

# This script was written by Claude Opus 5.5.

"""Replay a MediaWiki full-history XML dump into git, one commit per revision.

Each wiki page becomes one file (spaces -> underscores, ".mediawiki" extension,
subpages like "Foo/Bar" become directories). Commits keep the original author,
timestamp and edit summary, and are ordered chronologically across all pages.

No third-party dependencies.
"""
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

EXT = ".mediawiki"            # GitHub renders .mediawiki files natively
BRANCH = "refs/heads/main"
EMAIL_DOMAIN = "example.com"  # placeholder: encodes the username, not a real address
KNOWN_EMAILS = {"Issa": "riceissa@gmail.com"}


def local(tag):
    return tag.rsplit("}", 1)[-1]


def child(el, name):
    for c in el:
        if local(c.tag) == name:
            return c
    return None


def text_of(el, name):
    c = child(el, name)
    return c.text if c is not None else None


def title_to_path(title):
    return title.replace(" ", "_") + EXT


def quote_path(p):
    if p.startswith('"') or any(ch in p for ch in '\\\n'):
        return '"' + p.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    return p


def ident(name):
    clean = name.replace("<", "").replace(">", "").replace("\n", " ").strip() or "Unknown"
    email = KNOWN_EMAILS.get(clean) or clean.replace(" ", "_") + "@" + EMAIL_DOMAIN
    return f"{clean} <{email}>"


def parse(path):
    revs = []
    for _, el in ET.iterparse(path, events=("end",)):
        if local(el.tag) != "page":
            continue
        title = text_of(el, "title")
        for r in el:
            if local(r.tag) != "revision":
                continue
            contrib = child(r, "contributor")
            author = None
            if contrib is not None:
                author = text_of(contrib, "username") or text_of(contrib, "ip")
            t = child(r, "text")
            if t is None or t.get("deleted") is not None:
                text = None
            else:
                text = t.text or ""
            ts = text_of(r, "timestamp")
            revs.append({
                "title": title,
                "id": int(text_of(r, "id")),
                "ts": int(datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
                          .replace(tzinfo=timezone.utc).timestamp()),
                "author": author or "Unknown",
                "comment": (text_of(r, "comment") or "").strip(),
                "minor": child(r, "minor") is not None,
                "text": text,
            })
        el.clear()
    revs.sort(key=lambda r: (r["ts"], r["id"]))
    return revs


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    revs = parse(sys.argv[1])
    out = sys.stdout.buffer

    def data(s):
        b = s.encode("utf-8")
        out.write(b"data %d\n" % len(b))
        out.write(b)
        out.write(b"\n")

    pages, skipped = set(), 0
    for i, r in enumerate(revs, 1):
        if r["text"] is None:
            skipped += 1
            print(f"skipping revision {r['id']} of {r['title']!r}: text deleted/suppressed",
                  file=sys.stderr)
            continue
        pages.add(r["title"])
        who = ident(r["author"])
        msg = r["comment"] or "(no edit summary)"
        msg += f"\n\nPage: {r['title']}\nMediaWiki-Revision: {r['id']}"
        if r["minor"]:
            msg += "\nMinor-Edit: yes"
        out.write(f"commit {BRANCH}\nmark :{i}\n".encode())
        out.write(f"author {who} {r['ts']} +0000\n".encode())
        out.write(f"committer {who} {r['ts']} +0000\n".encode())
        data(msg + "\n")
        out.write(f"M 100644 inline {quote_path(title_to_path(r['title']))}\n".encode())
        data(r["text"])
        out.write(b"\n")

    print(f"{len(revs) - skipped} revisions committed across {len(pages)} pages"
          + (f" ({skipped} skipped)" if skipped else ""), file=sys.stderr)


if __name__ == "__main__":
    main()
