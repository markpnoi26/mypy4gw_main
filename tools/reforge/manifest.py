"""Resolves upstream paths to their transformed destinations via layout.toml."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_MANIFEST = HERE / "layout.toml"


def split_alternatives(body: str) -> list[str]:
    out, depth, current = [], 0, []
    for ch in body:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(current))
            current = []
        else:
            current.append(ch)
    out.append("".join(current))
    return out


def expand_braces(pattern: str) -> list[str]:
    start = pattern.find("{")
    if start == -1:
        return [pattern]
    depth, end = 0, -1
    for i in range(start, len(pattern)):
        if pattern[i] == "{":
            depth += 1
        elif pattern[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return [pattern]
    head, body, tail = pattern[:start], pattern[start + 1 : end], pattern[end + 1 :]
    out = []
    for alt in split_alternatives(body):
        out.extend(expand_braces(head + alt + tail))
    return out


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    parts, i = ["^"], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            parts.append("(?:[^/]+/)*")
            i += 3
        elif pattern.startswith("**", i):
            parts.append(".*")
            i += 2
        elif pattern[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            parts.append("[^/]")
            i += 1
        else:
            parts.append(re.escape(pattern[i]))
            i += 1
    parts.append("$")
    return re.compile("".join(parts))


def literal_prefix_len(pattern: str) -> int:
    hit = re.search(r"[*?\[]", pattern)
    return len(pattern) if hit is None else hit.start()


@dataclass
class Entry:
    match: str
    dest: str
    tier: str
    pack: str
    note: str
    is_override: bool
    variants: list[tuple[re.Pattern[str], int]]

    def best_match(self, src: str) -> int | None:
        scores = [plen for rx, plen in self.variants if rx.match(src)]
        return max(scores) if scores else None


@dataclass
class Resolution:
    src: str
    dest: str
    entry: Entry

    @property
    def is_keep(self) -> bool:
        return self.dest == "keep"

    @property
    def is_drop(self) -> bool:
        return self.dest == "drop"

    @property
    def moves(self) -> bool:
        return not self.is_keep and not self.is_drop and self.dest != self.src


class Ambiguous(Exception):
    pass


def render_dest(template: str, src: str) -> str:
    if template in ("keep", "drop"):
        return template
    segments = src.split("/")
    out = template

    for prefix in re.findall(r"\{rel:([^}]*)\}", out):
        stripped = src[len(prefix) :].lstrip("/") if src.startswith(prefix) else src
        out = out.replace("{rel:%s}" % prefix, stripped)

    for n in re.findall(r"\{seg:(\d+)\}", out):
        out = out.replace("{seg:%s}" % n, segments[int(n)])

    for n in re.findall(r"\{strip:(\d+)\}", out):
        out = out.replace("{strip:%s}" % n, "/".join(segments[int(n) :]))

    return out.replace("{name}", segments[-1])


class Manifest:
    def __init__(self, doc: dict):
        self.meta = doc.get("meta", {})
        self.codemods = doc.get("codemod", [])
        self.legacy_ids = {row["old"]: row["new"] for row in doc.get("legacy_id", [])}
        self.entries = [self.build(row, False) for row in doc.get("rule", [])]
        self.entries += [self.build(row, True) for row in doc.get("override", [])]

    @staticmethod
    def build(row: dict, is_override: bool) -> Entry:
        variants = [
            (glob_to_regex(pattern), literal_prefix_len(pattern))
            for pattern in expand_braces(row["match"])
        ]
        return Entry(
            match=row["match"],
            dest=row["dest"],
            tier=row.get("tier", ""),
            pack=row.get("pack", ""),
            note=row.get("note", ""),
            is_override=is_override,
            variants=variants,
        )

    def resolve(self, src: str) -> Resolution | None:
        ranked = []
        for entry in self.entries:
            score = entry.best_match(src)
            if score is not None:
                ranked.append(((entry.is_override, score), entry))
        if not ranked:
            return None
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        top_key, top_entry = ranked[0]
        rivals = [e for key, e in ranked[1:] if key == top_key and e.dest != top_entry.dest]
        if rivals:
            raise Ambiguous(
                "%s matches %r and %r at equal specificity"
                % (src, top_entry.match, rivals[0].match)
            )
        return Resolution(src=src, dest=render_dest(top_entry.dest, src), entry=top_entry)


def load(path: Path | None = None) -> Manifest:
    target = path or DEFAULT_MANIFEST
    with open(target, "rb") as fh:
        return Manifest(tomllib.load(fh))
