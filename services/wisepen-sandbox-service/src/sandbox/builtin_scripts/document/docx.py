"""Inspect and edit body paragraphs, tables, comments and inline text revisions."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.text.run import Run

RUN = qn("w:r")
TEXT_RUN_CHILDREN = {qn("w:rPr"), qn("w:t")}
REVISION_TAGS = {qn(f"w:{name}") for name in ("ins", "del", "moveFrom", "moveTo")}


def paragraphs(container, prefix=""):
    # 各层位置按文档顺序从 1 开始。合并单元格取其首次出现的坐标。
    for block, item in enumerate(container.iter_inner_content(), 1):
        location = f"{prefix}{block}"
        if isinstance(item, Paragraph):
            yield location, item
        elif isinstance(item, Table):
            seen = set()
            for row_number, row in enumerate(item.rows, 1):
                for column, cell in enumerate(row.cells, row.grid_cols_before + 1):
                    if cell._tc not in seen:
                        seen.add(cell._tc)
                        yield from paragraphs(cell, f"{location}:{row_number}:{column}:")


def text_groups(paragraph):
    group = []
    for element in paragraph._p:
        if element.tag == RUN and all(child.tag in TEXT_RUN_CHILDREN for child in element):
            group.append(Run(element, paragraph))
        else:
            # 跳过字段、超链接、已有修订和绘图等边界，避免将两侧文字当成连续文本。
            if group:
                yield group
            group = []
    if group:
        yield group


def find_text(paragraph, text):
    matches = []
    for group in text_groups(paragraph):
        joined = "".join(run.text for run in group)
        start = joined.find(text)
        while start >= 0:
            end = start + len(text)
            spans = []
            offset = 0
            for run in group:
                limit = offset + len(run.text)
                if offset < end and limit > start:
                    spans.append((run, max(start - offset, 0), min(end - offset, limit - offset)))
                offset = limit
            matches.append(spans)
            start = joined.find(text, start + 1)
    if len(matches) != 1:
        raise ValueError(f"expected one exact editable match, found {len(matches)}")
    return matches[0]


def copy_run(run, text):
    element = deepcopy(run._r)
    Run(element, run._parent).text = text
    return element


def isolate(runs):
    # 批注和修订需要以 run 为边界。拆出的文字沿用原 run 的格式。
    for run, start, end in runs:
        text = run.text
        if start:
            run._r.addprevious(copy_run(run, text[:start]))
        if end < len(text):
            run._r.addnext(copy_run(run, text[end:]))
        run.text = text[start:end]


def replace(doc, spans, new, author):
    if not author:
        # 替换文字按原命中的 run 分配，额外字符使用最后一个命中 run 的样式。
        for index, (run, start, end) in enumerate(spans):
            count = len(new) if index == len(spans) - 1 else end - start
            run.text = run.text[:start] + new[:count] + run.text[end:]
            new = new[count:]
        return []

    isolate(spans)
    nodes = [run._r for run, _, _ in spans]
    first = nodes[0]
    last = nodes[-1]
    changes = doc.element.xpath(".//w:ins | .//w:del | .//w:moveFrom | .//w:moveTo")
    revision_id = max((int(item.get(qn("w:id"))) for item in changes), default=-1) + 1
    created = []
    for offset, name in enumerate(("del", "ins")):
        if name == "ins" and not new:
            break
        change = OxmlElement(f"w:{name}")
        change.set(qn("w:id"), str(revision_id + offset))
        created.append(revision_id + offset)
        change.set(qn("w:author"), author)
        change.set(qn("w:date"), datetime.now(timezone.utc).isoformat(timespec="seconds"))
        first.addprevious(change) if name == "del" else deletion.addnext(change)
        if name == "del":
            deletion = change
            for node in nodes:
                for child in node.findall(qn("w:t")):
                    child.tag = qn("w:delText")
                change.append(node)
        else:
            for node in nodes:
                count = len(new) if node is last else sum(len(child.text or "") for child in node.findall(qn("w:delText")))
                if new:
                    element = deepcopy(node)
                    Run(element, spans[0][0]._parent).text = new[:count]
                    if count:
                        change.append(element)
                    new = new[count:]
    return created


def accept_inline(doc, ids):
    changes = [node for node in doc.element.body.iter() if node.tag in REVISION_TAGS]
    if ids:
        selected = set(ids)
        changes = [node for node in changes if int(node.get(qn("w:id"))) in selected]
        if len(changes) != len(selected):
            raise ValueError("each requested revision ID must identify one change")
    for node in changes:
        if (node.tag in {qn("w:moveFrom"), qn("w:moveTo")} or node.getparent().tag != qn("w:p")
                or not all(child.tag == RUN and all(part.tag in TEXT_RUN_CHILDREN | {qn("w:delText")}
                                                    for part in child) for child in node)):
            raise ValueError("accept supports inline text revisions in the document body and its tables")
    for node in changes:
        if node.tag == qn("w:ins"):
            for child in list(node):
                node.addprevious(child)
        node.getparent().remove(node)
    return len(changes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    commands = parser.add_subparsers(dest="operation", required=True)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--contains", default="")
    inspect.add_argument("--offset", type=int, default=0)
    commands.add_parser("comments")
    for name in ("replace", "add-comment", "accept"):
        command = commands.add_parser(name)
        command.add_argument("output", type=Path)
        if name == "accept":
            command.add_argument("--id", action="append", type=int)
        if name != "accept":
            command.add_argument("--at", required=True)
            command.add_argument("--text", required=True)
        if name == "replace":
            command.add_argument("--with", dest="replacement", required=True)
            command.add_argument("--track-author")
        if name == "add-comment":
            command.add_argument("--body", required=True)
            command.add_argument("--author", required=True)
    args = parser.parse_args()
    workspace = Path.cwd().resolve()
    args.source.resolve().relative_to(workspace)
    doc = Document(args.source)
    if args.operation == "inspect":
        if args.offset < 0:
            parser.error("offset must be nonnegative")
        entries = [(location, p) for location, p in paragraphs(doc) if args.contains in p.text]
        window = entries[args.offset:args.offset + 30]
        result = {"paragraphs": [{"at": location, "style": p.style.name, "text": p.text[:500]}
                                for location, p in window],
                  "next_offset": args.offset + len(window) if args.offset + len(window) < len(entries) else None}
    elif args.operation == "comments":
        anchors = {}
        for location, paragraph in paragraphs(doc):
            for marker in paragraph._p.xpath(".//w:commentRangeStart"):
                anchors[marker.get(qn("w:id"))] = location
        result = {"comments": [{"id": comment.comment_id, "author": comment.author, "text": comment.text,
                                "at": anchors.get(str(comment.comment_id))} for comment in doc.comments]}
    else:
        path = args.output.resolve().relative_to(workspace)
        if args.operation == "accept":
            result = {"accepted": accept_inline(doc, args.id)}
        else:
            paragraph = dict(paragraphs(doc)).get(args.at)
            if paragraph is None or not args.text:
                parser.error("supply a paragraph location from inspect and nonempty text")
            spans = find_text(paragraph, args.text)
            if args.operation == "replace":
                revisions = replace(doc, spans, args.replacement, args.track_author)
                result = {"at": args.at, "revision_ids": revisions}
            else:
                isolate(spans)
                comment = doc.add_comment([run for run, _, _ in spans], text=args.body, author=args.author)
                result = {"at": args.at, "comment_id": comment.comment_id}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as output:
            doc.save(output)
        result["path"] = path.as_posix()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        sys.exit(str(exc))
