"""Independent, offline capture-HTML -> record preservation check.

This does not certify completeness of the source website. A successful check
only covers information present in the submitted capture HTML. No network,
capture_contract, CaptureFragment, producer code, or content repair is used.
"""
from __future__ import annotations

import hashlib
import base64
import binascii
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlsplit

VERSION = "1"
MAX_HTML_BYTES = 96 * 1024 * 1024
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
SKIP = {"script", "style", "noscript", "template"}
BODY_CLASSES = {"tweet-text", "article-body", "comment-text"}


@dataclass(eq=False)
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)
    parent: Node | None = None

    def classes(self):
        return set(self.attrs.get("class", "").split())


class CaptureTree(HTMLParser):
    def __init__(self, raw):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]
        self.nodes = []
        self.feed(raw)
        self.close()

    def handle_starttag(self, tag, attrs):
        if len(self.nodes) >= 200_000 or len(self.stack) >= 512:
            raise ValueError("capture_structure_limit")
        node = Node(tag, dict(attrs), parent=self.stack[-1])
        self.nodes.append(node)
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _compact(value):
    return re.sub(r"\s+", "", str(value or ""))


def _http(value):
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        return ""
    try:
        u = urlsplit(value)
        return value if u.scheme in {"http", "https"} and u.hostname and not u.username and not u.password else ""
    except ValueError:
        return ""


def _image_hash(value):
    if not isinstance(value, str):
        return ""
    match = re.fullmatch(r"data:image/(?:png|jpeg|gif|webp|avif);base64,([A-Za-z0-9+/]*={0,2})", value)
    if not match:
        return ""
    try:
        return hashlib.sha256(base64.b64decode(match[1], validate=True)).hexdigest()
    except (ValueError, binascii.Error):
        return ""


def _is_record(node):
    return ((node.tag == "article" and (bool(node.classes() & {"tweet", "web-article", "reply"})
                                        or bool(node.attrs.get("data-tweet-id"))))
            or (node.tag == "blockquote" and bool(node.classes() & {"quoted-tweet", "reply-quote"})))


def _owner(node):
    node = node.parent
    while node and not _is_record(node):
        node = node.parent
    return node


def _text(node):
    return "".join(_text(x) if isinstance(x, Node) else x for x in node.children)


def _meta_name(owner):
    """Read only a capture's direct metadata, never descendants' metadata."""
    def label(node):
        if node.tag == "a":
            return ""
        return "".join(label(x) if isinstance(x, Node) else x for x in node.children)
    pending = [owner]
    while pending:
        node = pending.pop()
        if node is not owner and _is_record(node):
            continue
        if node.tag == "p" and "meta" in node.classes():
            value = label(node).strip(" ·\t\r\n").split("·", 1)[0].strip()
            return value if value and not value.startswith(("http://", "https://")) else ""
        pending.extend(reversed([x for x in node.children if isinstance(x, Node)]))
    return ""


def _source_features(owner):
    """Gather semantic body pieces without menus, metadata or child records."""
    pieces, links, images, videos = [], set(), set(), set()
    found_body = False

    def walk(node, in_body=False, in_caption=False):
        nonlocal found_body
        if node is not owner and _is_record(node) or node.tag in SKIP:
            return
        in_body = in_body or bool(node.classes() & BODY_CLASSES)
        found_body |= in_body
        in_caption = in_caption or node.tag == "figcaption"
        if "meta" in node.classes():
            return
        if node.tag == "a":
            href = _http(node.attrs.get("href"))
            if href and (in_body or in_caption):
                links.add(href)
            # Producers abbreviate displayed URLs. Compare the real href,
            # not a shortened label that the ingester correctly replaces.
            label = _text(node).strip()
            if href and label.startswith(("http://", "https://", "www.")):
                return
        if node.tag == "img":
            src = node.attrs.get("src", "")
            if src:
                if re.match(r"^https?://abs\.twimg\.com/emoji/", src, re.I) and node.attrs.get("alt"):
                    if in_body:
                        pieces.append(_compact(node.attrs["alt"]))
                    return
                images.add(src)
                if node.attrs.get("data-archive-video-thumbnail") == "true" or node.attrs.get("alt") == "동영상 썸네일":
                    videos.add(src)
        if node.tag == "video":
            videos.add("unconverted-video")
        for child in node.children:
            if isinstance(child, Node):
                walk(child, in_body, in_caption)
            elif in_body and not in_caption and _compact(child):
                pieces.append(_compact(child))

    walk(owner)
    return dict(pieces=pieces, links=links, images=images, videos=videos, body=found_body)


def _record_index(records):
    index, duplicates = {}, []

    def visit(rec, path, depth=0):
        if depth > 32 or not isinstance(rec, dict):
            raise ValueError("record_structure_invalid")
        tid = str(rec.get("tweetId") or rec.get("id") or "")
        key = path + (tid,)
        if key in index:
            duplicates.append(key)
        index[key] = rec
        quote = rec.get("quoted")
        if quote is not None:
            visit(quote, key + ("quote",), depth + 1)
        replies = rec.get("replies", [])
        if not isinstance(replies, list):
            raise ValueError("record_replies_invalid")
        for reply in replies:
            visit(reply, key + ("reply",), depth + 1)

    if not isinstance(records, list):
        raise ValueError("records_not_array")
    for rec in records:
        visit(rec, ())
    return index, duplicates


def audit_capture(raw: str, records: list) -> dict:
    """Return bounded, JSON-safe evidence; URLs/text are not copied to logs.

    `blocked` means submitted information is demonstrably absent in JSON, or
    input structure prevents reliable comparison. `capture_completeness`
    never becomes complete, including when reported comment counts match.
    """
    encoded = raw.encode("utf-8")
    report = dict(version=VERSION, scope="capture_html_to_record",
                  source_sha256=hashlib.sha256(encoded).hexdigest(),
                  conversion_status="preserved", capture_completeness="unknown",
                  blocked=False, issues=[], counts={})

    def issue(code, path=(), blocking=False):
        if len(report["issues"]) < 2000:
            report["issues"].append(dict(code=code, path=list(path), blocking=blocking))
        if blocking:
            report["blocked"] = True
            report["conversion_status"] = "loss_or_uncheckable"

    if len(encoded) > MAX_HTML_BYTES:
        issue("capture_size_limit", blocking=True)
        return report
    try:
        tree = CaptureTree(raw)
        index, duplicates = _record_index(records)
    except (ValueError, RecursionError) as exc:
        issue(str(exc) if isinstance(exc, ValueError) else "capture_structure_limit", blocking=True)
        return report
    for key in duplicates:
        issue("duplicate_record_identity", key, True)
    nodes = [n for n in tree.nodes if _is_record(n)]
    main_nodes = [n for n in nodes if _owner(n) is None]
    if not main_nodes:
        issue("capture_main_unrecognized", blocking=True)
    keys = {}
    for node in nodes:
        tid = node.attrs.get("data-tweet-id") or node.attrs.get("data-comment-id") or ""
        parent = _owner(node)
        if parent and parent not in keys:
            issue("capture_parent_unrecognized", blocking=True)
            continue
        key = keys[parent] + ("quote" if node.tag == "blockquote" else "reply", tid) if parent else (tid,)
        if not tid:
            issue("capture_identity_missing", key, True)
        if key in keys.values():
            issue("duplicate_capture_identity", key, True)
        keys[node] = key
        rec = index.get(key)
        if rec is None:
            issue("record_missing", key, True)
            continue
        handle = node.attrs.get("data-author-handle", "").lstrip("@").lower()
        actual_author = rec.get("author") or {}
        actual_handle = actual_author.get("handle", "").lstrip("@").lower() if isinstance(actual_author, dict) else ""
        if handle and handle != actual_handle:
            issue("author_handle_changed", key, True)
        name = _meta_name(node)
        if name and isinstance(actual_author, dict) and name != actual_author.get("name"):
            issue("author_name_changed", key, True)
        created = node.attrs.get("data-created-at", "")
        if created and created != rec.get("createdAt"):
            issue("publication_time_changed", key, True)
        source = _source_features(node)
        actual = _compact(rec.get("fullText") or rec.get("text"))
        cursor = 0
        for piece in source["pieces"]:
            position = actual.find(piece, cursor)
            if position < 0:
                issue("body_text_missing_or_reordered", key, True)
                break
            cursor = position + len(piece)
        if not source["body"] and actual:
            issue("body_scope_unrecognized", key, True)
        media = rec.get("media") or []
        urls = {m.get("url") for m in media if isinstance(m, dict)}
        if source["images"] - urls:
            issue("image_missing", key, True)
        linked = {v.get("url") for v in rec.get("links", []) if isinstance(v, dict)}
        linked |= {v.get("sourceUrl") for v in media if isinstance(v, dict)}
        for url in source["links"]:
            if url not in linked and url not in str(rec.get("fullText") or ""):
                issue("hyperlink_destination_missing", key, True)
        for url in source["videos"]:
            if url == "unconverted-video":
                issue("video_thumbnail_unverified", key)
                report["capture_completeness"] = "partial"
            elif not any(isinstance(m, dict) and m.get("url") == url
                         and m.get("kind") == "video_thumbnail" and _http(m.get("sourceUrl")) for m in media):
                issue("video_kind_or_source_missing", key, True)
    for key in set(index) - set(keys.values()):
        issue("record_not_in_capture", key, True)
    manifests = [n for n in tree.nodes if n.tag == "script" and n.attrs.get("id") == "archive-capture-manifest"]
    if len(manifests) > 1:
        issue("capture_manifest_duplicate", blocking=True)
    elif manifests:
        try:
            manifest = json.loads(_text(manifests[0]))
            if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
                raise ValueError()
            comments = manifest.get("comments", {})
            if not isinstance(comments, dict):
                raise ValueError()
            saved, expected = comments.get("saved"), comments.get("expected")
            replies = sum(n.tag == "article" and _owner(n) is not None for n in nodes)
            if type(saved) is not int or saved != replies:
                issue("manifest_saved_comments_mismatch", blocking=True)
            if expected is not None and (type(expected) is not int or expected < 0):
                issue("manifest_expected_comments_invalid", blocking=True)
            elif expected is not None and expected > replies:
                issue("capture_comments_partial")
                report["capture_completeness"] = "partial"
            elif expected == replies and report["capture_completeness"] != "partial":
                report["capture_completeness"] = "count_matched_unverified"
            warnings = manifest.get("warnings", [])
            if not isinstance(warnings, list):
                raise ValueError()
            if warnings:
                issue("capture_producer_warnings")
                report["capture_completeness"] = "partial"
            images = manifest.get("images", [])
            if not isinstance(images, list):
                raise ValueError()
            if any(not isinstance(im, dict) for im in images):
                raise ValueError()
            if any(im.get("status") not in {"embedded"} for im in images):
                issue("capture_images_not_embedded")
                report["capture_completeness"] = "partial"
            html_hashes = {_image_hash(n.attrs.get("src")) for n in tree.nodes if n.tag == "img" and _owner(n) is not None}
            for im in images:
                sha = im.get("sha256")
                if im.get("status") == "embedded" and isinstance(sha, str) and re.fullmatch(r"[a-f0-9]{64}", sha):
                    if sha not in html_hashes:
                        issue("manifest_embedded_image_absent_from_html", blocking=True)
        except (ValueError, TypeError):
            issue("capture_manifest_invalid", blocking=True)
    else:
        issue("capture_manifest_absent")
    if any(n.attrs.get("data-testid") == "tweet-text-show-more-link" for n in tree.nodes):
        issue("capture_folded_text_present")
        report["capture_completeness"] = "partial"
    report["counts"] = dict(main=len(main_nodes), records=len(nodes),
                            replies=sum(n.tag == "article" and _owner(n) is not None for n in nodes),
                            quotes=sum(n.tag == "blockquote" for n in nodes))
    return report
