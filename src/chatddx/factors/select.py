import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal
from urllib.parse import unquote

from pydantic import JsonValue

# A selector picks the values a view compares: a JSON Pointer (RFC 6901), which picks
# at most one value, or a JSONPath query (RFC 9535) from a small subset: member names
# (`.name`, `['name']`), indexes (`[0]`, `[-1]`), wildcards (`.*`, `[*]`), and filters
# that test a relative path for existence (`[?@.critical]`) or compare it with a literal
# (`[?@.critical == true]`, `!=`). Within the subset, every RFC 9535 implementation
# selects the same nodes. RFC 9535 leaves the order of an object's members open; this
# keeps the document's order.


@dataclass(frozen=True)
class Name:
    name: str


@dataclass(frozen=True)
class Index:
    index: int


@dataclass(frozen=True)
class Wild:
    pass


@dataclass(frozen=True)
class Filter:
    path: tuple["Name | Index", ...]
    op: Literal["==", "!="] | None = None
    value: JsonValue = None


# A JSON Pointer reference token: a member name, or an array index when it's one.
@dataclass(frozen=True)
class Token:
    token: str


Step = Name | Index | Wild | Filter | Token

_NAME = re.compile(r"[A-Za-z_\u0080-\U0010ffff][A-Za-z0-9_\u0080-\U0010ffff]*")
_INT = re.compile(r"0|-?[1-9][0-9]*")
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?")
_ARRAY_INDEX = re.compile(r"0|[1-9][0-9]*")
_ESCAPES = {
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "/": "/",
    "\\": "\\",
    "'": "'",
    '"': '"',
}


class _Parser:
    def __init__(self, text: str) -> None:
        self.text: str = text
        self.i: int = 0

    def fail(self, what: str) -> ValueError:
        return ValueError(f"{self.text!r}: {what} at position {self.i}")

    def take(self, s: str) -> bool:
        if self.text.startswith(s, self.i):
            self.i += len(s)
            return True
        return False

    def expect(self, s: str) -> None:
        if not self.take(s):
            raise self.fail(f"expected {s!r}")

    def match(self, pattern: re.Pattern[str]) -> str | None:
        m = pattern.match(self.text, self.i)
        if m is None:
            return None
        self.i = m.end()
        return m.group()

    def blank(self) -> None:
        while self.i < len(self.text) and self.text[self.i] in " \t\n\r":
            self.i += 1

    def at_end(self) -> bool:
        return self.i >= len(self.text)

    def string(self) -> str:
        quote = self.text[self.i]
        self.i += 1
        out: list[str] = []
        while not self.at_end():
            c = self.text[self.i]
            self.i += 1
            if c == quote:
                return "".join(out)
            if c == "\\":
                out.append(self.escape())
            elif ord(c) < 0x20:
                raise self.fail("a control character in a string")
            else:
                out.append(c)
        raise self.fail("an unclosed string")

    def escape(self) -> str:
        c = self.text[self.i : self.i + 1]
        self.i += 1
        if c in _ESCAPES:
            return _ESCAPES[c]
        if c == "u":
            code = self.hex4()
            if 0xD800 <= code < 0xDC00 and self.take("\\u"):
                low = self.hex4()
                code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
            return chr(code)
        raise self.fail(f"an unknown escape \\{c}")

    def hex4(self) -> int:
        digits = self.text[self.i : self.i + 4]
        if not re.fullmatch(r"[0-9A-Fa-f]{4}", digits):
            raise self.fail("a bad \\u escape")
        self.i += 4
        return int(digits, 16)

    def segment(self, relative: bool) -> Step:
        if self.take(".."):
            raise self.fail("a descendant segment (..), outside the subset")
        if self.take("."):
            if not relative and self.take("*"):
                return Wild()
            name = self.match(_NAME)
            if name is None:
                raise self.fail("a member name")
            return Name(name)
        if self.take("["):
            self.blank()
            step = self.bracketed(relative)
            self.blank()
            self.expect("]")
            return step
        raise self.fail("a segment")

    def bracketed(self, relative: bool) -> Step:
        if not relative and self.take("*"):
            return Wild()
        if not relative and self.take("?"):
            self.blank()
            return self.filter()
        if self.text[self.i : self.i + 1] in ("'", '"'):
            return Name(self.string())
        index = self.match(_INT)
        if index is None:
            raise self.fail("a name, an index, * or a filter")
        return Index(int(index))

    def filter(self) -> Filter:
        self.expect("@")
        path: list[Name | Index] = []
        while self.text[self.i : self.i + 1] in (".", "["):
            step = self.segment(relative=True)
            assert isinstance(step, Name | Index)
            path.append(step)
        self.blank()
        for op in ("==", "!="):
            if self.take(op):
                self.blank()
                return Filter(tuple(path), op, self.literal())
        return Filter(tuple(path))

    def literal(self) -> JsonValue:
        for word, value in (("true", True), ("false", False), ("null", None)):
            if self.take(word):
                return value
        if self.text[self.i : self.i + 1] in ("'", '"'):
            return self.string()
        number = self.match(_NUMBER)
        if number is None:
            raise self.fail("a literal")
        if re.fullmatch(r"-?[0-9]+", number):
            return int(number)
        return float(number)


def _pointer(text: str) -> tuple[Step, ...]:
    if text == "":
        return ()
    if not text.startswith("/"):
        raise ValueError(f"{text!r}: a JSON Pointer starts with /")
    tokens: list[Step] = []
    for raw in text[1:].split("/"):
        if re.search(r"~[^01]|~$", raw):
            raise ValueError(f"{text!r}: ~ must be followed by 0 or 1")
        tokens.append(Token(raw.replace("~1", "/").replace("~0", "~")))
    return tuple(tokens)


def parse_selector(text: str) -> tuple[Step, ...]:
    if text == "" or text.startswith("/"):
        return _pointer(text)
    if not text.startswith("$"):
        raise ValueError(
            f"{text!r}: a selector is a JSON Pointer or a JSONPath query starting with $"
        )
    p = _Parser(text)
    p.expect("$")
    steps: list[Step] = []
    while not p.at_end():
        steps.append(p.segment(relative=False))
    return tuple(steps)


def check_selector(text: str) -> str:
    _ = parse_selector(text)
    return text


_NOTHING = object()


def _equal(a: JsonValue, b: JsonValue) -> bool:
    match a, b:
        case (bool(), _) | (_, bool()):
            return type(a) is type(b) and a == b
        case int() | float(), int() | float():
            return a == b
        case list(), list():
            return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b))
        case dict(), dict():
            return a.keys() == b.keys() and all(_equal(a[k], b[k]) for k in a)
        case _:
            return type(a) is type(b) and a == b


def _children(node: JsonValue) -> list[JsonValue]:
    match node:
        case list():
            return list(node)
        case dict():
            return list(node.values())
        case _:
            return []


def _apply(step: Step, node: JsonValue) -> list[JsonValue]:
    match step, node:
        case Name(name), dict() if name in node:
            return [node[name]]
        case Index(index), list() if -len(node) <= index < len(node):
            return [node[index]]
        case Token(token), dict() if token in node:
            return [node[token]]
        case Token(token), list() if _ARRAY_INDEX.fullmatch(token) and int(token) < len(
            node
        ):
            return [node[int(token)]]
        case Wild(), _:
            return _children(node)
        case Filter(), _:
            return [c for c in _children(node) if _test(step, c)]
        case _:
            return []


def _test(f: Filter, node: JsonValue) -> bool:
    found = _select(node, f.path)
    if f.op is None:
        return bool(found)
    equal = bool(found) and _equal(found[0], f.value)
    return equal if f.op == "==" else not equal


def _select(document: JsonValue, steps: Iterable[Step]) -> list[JsonValue]:
    nodes = [document]
    for step in steps:
        nodes = [n for node in nodes for n in _apply(step, node)]
    return nodes


def select(document: JsonValue, selector: str) -> list[JsonValue]:
    return _select(document, parse_selector(selector))


# Splits applied to selected text, one name per behavior as with text-cleanup ops.
SplitOp = Literal["lines@1"]

_MARKER = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def _lines(text: str) -> list[str]:
    items = (_MARKER.sub("", line).strip() for line in text.splitlines())
    return [item for item in items if item]


def split(op: SplitOp, items: Iterable[JsonValue]) -> list[JsonValue]:
    assert op == "lines@1"
    out: list[JsonValue] = []
    for item in items:
        out.extend(_lines(item) if isinstance(item, str) else [item])
    return out


# Whether a selector can pick anything from documents that follow a schema. It errs
# towards yes: unknown refs, unconstrained schemas and unions count as reachable, so a
# no is certain and a yes isn't.


def _resolve(root: JsonValue, ref: str) -> JsonValue:
    node = root
    for raw in unquote(ref[2:]).split("/") if ref != "#" else ():
        token = raw.replace("~1", "/").replace("~0", "~")
        match node:
            case dict() if token in node:
                node = node[token]
            case list() if token.isdigit() and int(token) < len(node):
                node = node[int(token)]
            case _:
                return False
    return node


_SHAPE = frozenset(
    {
        "type",
        "properties",
        "patternProperties",
        "additionalProperties",
        "items",
        "prefixItems",
        "enum",
        "const",
    }
)


def _alternatives(
    root: JsonValue, node: JsonValue, seen: tuple[str, ...] = ()
) -> list[JsonValue]:
    if node is True or node == {}:
        return [True]
    if not isinstance(node, dict):
        return []
    ref = node.get("$ref")
    if isinstance(ref, str):
        if not ref.startswith("#") or ref in seen:
            return [True]
        return _alternatives(root, _resolve(root, ref), (*seen, ref))
    branches = [
        b
        for k in ("anyOf", "oneOf", "allOf")
        if isinstance(listed := node.get(k), list)
        for b in listed
    ]
    if not branches:
        return [node]
    own = [node] if node.keys() & _SHAPE else []
    return own + [a for b in branches for a in _alternatives(root, b, seen)]


def _allows(node: dict[str, JsonValue], kind: str) -> bool:
    match node.get("type"):
        case None:
            return True
        case str() as t:
            return t == kind
        case list() as ts:
            return kind in ts
        case _:
            return False


def _open(node: dict[str, JsonValue], key: str) -> list[JsonValue]:
    rest = node.get(key, True)
    return [] if rest is False else [rest]


def _member(node: dict[str, JsonValue], name: str) -> list[JsonValue]:
    properties = node.get("properties")
    if isinstance(properties, dict) and name in properties:
        return [properties[name]]
    patterns = node.get("patternProperties")
    if isinstance(patterns, dict):
        matched = [s for p, s in patterns.items() if re.search(p, name)]
        if matched:
            return matched
    return _open(node, "additionalProperties")


def _members(node: dict[str, JsonValue]) -> list[JsonValue]:
    out: list[JsonValue] = []
    for key in ("properties", "patternProperties"):
        if isinstance(listed := node.get(key), dict):
            out.extend(listed.values())
    return out + _open(node, "additionalProperties")


def _item(node: dict[str, JsonValue], index: int) -> list[JsonValue]:
    prefix = node.get("prefixItems")
    if isinstance(prefix, list) and 0 <= index < len(prefix):
        return [prefix[index]]
    return _open(node, "items")


def _items(node: dict[str, JsonValue]) -> list[JsonValue]:
    prefix = node.get("prefixItems")
    return (list(prefix) if isinstance(prefix, list) else []) + _open(node, "items")


def _schema_step(root: JsonValue, node: JsonValue, step: Step) -> list[JsonValue]:
    out: list[JsonValue] = []
    for alt in _alternatives(root, node):
        if not isinstance(alt, dict):
            out.append(True)
            continue
        objects, arrays = _allows(alt, "object"), _allows(alt, "array")
        match step:
            case Name(name):
                out += _member(alt, name) if objects else []
            case Token(token):
                out += _member(alt, token) if objects else []
                if arrays and _ARRAY_INDEX.fullmatch(token):
                    out += _item(alt, int(token))
            case Index(index):
                out += _item(alt, index) if arrays else []
            case Wild():
                out += (_items(alt) if arrays else []) + (
                    _members(alt) if objects else []
                )
            case Filter(path=path):
                candidates = (_items(alt) if arrays else []) + (
                    _members(alt) if objects else []
                )
                out += [c for c in candidates if _schema_reach(root, c, path)]
    return out


def _schema_reach(root: JsonValue, node: JsonValue, steps: Iterable[Step]) -> bool:
    nodes = [node]
    for step in steps:
        nodes = [n for s in nodes for n in _schema_step(root, s, step)]
        if not nodes:
            return False
    return True


def reaches(schema: JsonValue, selector: str) -> bool:
    steps = parse_selector(selector)
    if schema is None:
        return not steps
    return _schema_reach(schema, schema, steps)
