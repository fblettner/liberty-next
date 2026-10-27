"""Calculated fields — a small, typed expression language compiled to portable SQL.

An analyst writes ``revenue / nullif_qty`` style expressions against the model's fields; the
compiler type-checks them and emits SQL for the pool's dialect, never free SQL from the
client. The grammar::

    expr     := or_expr [ '?' expr ':' expr ]
    or_expr  := and_expr { 'or' and_expr }
    and_expr := not_expr { 'and' not_expr }
    not_expr := 'not' not_expr | cmp
    cmp      := sum [ ('=' | '!=' | '<>' | '<' | '<=' | '>' | '>=') sum
                    | 'in' '(' literal {',' literal} ')' | 'is' ['not'] 'empty' ]
    sum      := term { ('+' | '-') term }
    term     := unary { ('*' | '/') unary }
    unary    := '-' unary | atom
    atom     := number | 'string' | field | func '(' [expr {',' expr}] ')' | '(' expr ')'

A field is ``NAME`` (of the primary entity) or ``entity.NAME``. Types: ``num``, ``str``,
``bool``, ``date``. ``/`` is a real division by ``NULLIF(x, 0)``; a boolean result renders as ``'Y'`` /
``'N'``. Functions: ``year`` ``month`` ``day`` (of a date), ``days(from, to)``, ``round(x[, n])``,
``abs``, ``coalesce(a, b, …)``, ``upper`` ``lower`` ``length`` ``concat(a, b, …)``. A JDE Julian
date field (format ``jdedate``) is converted to a real date before a date function is applied,
per dialect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Literal

from liberty.explorer.model import ExplorerField

Type = Literal["num", "str", "bool", "date"]
MAX_EXPR = 2000
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,40}$")


class CalcError(ValueError):
    def __init__(self, message: str, pos: int | None = None) -> None:
        super().__init__(message if pos is None else f"{message} (at {pos})")
        self.pos = pos


# ── tokens ─────────────────────────────────────────────────────────────────────

_TOKEN = re.compile(
    r"\s*(?:(?P<num>\d+(?:\.\d+)?)|(?P<str>'(?:[^']|'')*')|(?P<name>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)"
    r"|(?P<op><=|>=|!=|<>|[-+*/()?:,=<>]))"
)
_KEYWORDS = {"and", "or", "not", "in", "is", "empty", "null"}


@dataclass(slots=True)
class Tok:
    kind: str       # num | str | name | op | kw | end
    value: str
    pos: int


def tokenize(text: str) -> list[Tok]:
    if len(text) > MAX_EXPR:
        raise CalcError(f"expression longer than {MAX_EXPR} characters")
    out: list[Tok] = []
    pos = 0
    while pos < len(text):
        if text[pos:].strip() == "":
            break
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise CalcError(f"unexpected character {text[pos:pos + 1]!r}", pos)
        pos = m.end()
        for kind in ("num", "str", "name", "op"):
            v = m.group(kind)
            if v is not None:
                if kind == "name" and v.lower() in _KEYWORDS:
                    out.append(Tok("kw", v.lower(), m.start(kind)))
                else:
                    out.append(Tok(kind, v, m.start(kind)))
                break
    out.append(Tok("end", "", len(text)))
    return out


# ── AST ────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Node:
    type: Type
    sql: str
    fields: set[str]      # the entity ids referenced


FieldResolver = Callable[[str], tuple[ExplorerField, str]]
"""``name`` → (field, SQL reference) — the engine supplies it with the current plan's aliases."""


class Compiler:
    """Parse + type-check + emit for one dialect. ``resolve`` maps a field name (``NAME`` or
    ``entity.NAME``) to its model field and the SQL reference the plan gives it."""

    def __init__(self, dialect: str, resolve: FieldResolver) -> None:
        self.dialect = dialect
        self.resolve = resolve
        self.toks: list[Tok] = []
        self.i = 0

    # ── public ──────────────────────────────────────────────────────────────────
    def compile(self, text: str) -> Node:
        self.toks = tokenize(text)
        self.i = 0
        node = self._expr()
        if self._peek().kind != "end":
            raise CalcError(f"unexpected {self._peek().value!r}", self._peek().pos)
        return node

    # ── helpers ─────────────────────────────────────────────────────────────────
    def _peek(self) -> Tok:
        return self.toks[self.i]

    def _next(self) -> Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def _accept(self, kind: str, value: str | None = None) -> Tok | None:
        t = self._peek()
        if t.kind == kind and (value is None or t.value == value):
            return self._next()
        return None

    def _expect(self, kind: str, value: str | None = None) -> Tok:
        t = self._accept(kind, value)
        if t is None:
            raise CalcError(f"expected {value or kind!r}, got {self._peek().value or 'end'!r}", self._peek().pos)
        return t

    @staticmethod
    def _want(node: Node, t: Type, what: str, pos: int) -> None:
        if node.type != t:
            raise CalcError(f"{what} needs a {t}, got {node.type}", pos)

    # ── grammar ─────────────────────────────────────────────────────────────────
    def _expr(self) -> Node:
        cond = self._or()
        if self._accept("op", "?"):
            pos = self._peek().pos
            a = self._expr()
            self._expect("op", ":")
            b = self._expr()
            self._want(cond, "bool", "the condition of ?:", pos)
            if a.type != b.type:
                raise CalcError(f"the two branches of ?: differ ({a.type} / {b.type})", pos)
            return Node(a.type, f"CASE WHEN {cond.sql} THEN {self._plain(a)} ELSE {self._plain(b)} END", cond.fields | a.fields | b.fields)
        return cond

    def _or(self) -> Node:
        left = self._and()
        while self._accept("kw", "or"):
            pos = self._peek().pos
            right = self._and()
            self._want(left, "bool", "or", pos)
            self._want(right, "bool", "or", pos)
            left = Node("bool", f"({left.sql} OR {right.sql})", left.fields | right.fields)
        return left

    def _and(self) -> Node:
        left = self._not()
        while self._accept("kw", "and"):
            pos = self._peek().pos
            right = self._not()
            self._want(left, "bool", "and", pos)
            self._want(right, "bool", "and", pos)
            left = Node("bool", f"({left.sql} AND {right.sql})", left.fields | right.fields)
        return left

    def _not(self) -> Node:
        if self._accept("kw", "not"):
            pos = self._peek().pos
            inner = self._not()
            self._want(inner, "bool", "not", pos)
            return Node("bool", f"(NOT {inner.sql})", inner.fields)
        return self._cmp()

    def _cmp(self) -> Node:
        left = self._sum()
        t = self._peek()
        if t.kind == "op" and t.value in ("=", "!=", "<>", "<", "<=", ">", ">="):
            self._next()
            right = self._sum()
            if left.type != right.type:
                raise CalcError(f"cannot compare {left.type} with {right.type}", t.pos)
            op = "<>" if t.value == "!=" else t.value
            ls, rs = (self._as_date(left), self._as_date(right)) if left.type == "date" else (left.sql, right.sql)
            return Node("bool", f"({ls} {op} {rs})", left.fields | right.fields)
        if self._accept("kw", "in"):
            self._expect("op", "(")
            items: list[Node] = []
            while True:
                lit = self._atom()
                if lit.type != left.type:
                    raise CalcError(f"in: {lit.type} item against a {left.type}", t.pos)
                items.append(lit)
                if not self._accept("op", ","):
                    break
            self._expect("op", ")")
            return Node("bool", f"({left.sql} IN ({', '.join(n.sql for n in items)}))", left.fields)
        if self._accept("kw", "is"):
            negate = self._accept("kw", "not") is not None
            self._expect("kw", "empty")
            if left.type == "str":
                sql = f"({left.sql} IS NULL OR TRIM({left.sql}) = '')"
            else:
                sql = f"({left.sql} IS NULL)"
            return Node("bool", f"(NOT {sql})" if negate else sql, left.fields)
        return left

    def _sum(self) -> Node:
        left = self._term()
        while True:
            t = self._peek()
            if t.kind == "op" and t.value in ("+", "-"):
                self._next()
                right = self._term()
                if left.type == "str" and right.type == "str" and t.value == "+":
                    left = Node("str", self._concat([left, right]), left.fields | right.fields)
                    continue
                self._want(left, "num", t.value, t.pos)
                self._want(right, "num", t.value, t.pos)
                left = Node("num", f"({left.sql} {t.value} {right.sql})", left.fields | right.fields)
            else:
                return left

    def _term(self) -> Node:
        left = self._unary()
        while True:
            t = self._peek()
            if t.kind == "op" and t.value in ("*", "/"):
                self._next()
                right = self._unary()
                self._want(left, "num", t.value, t.pos)
                self._want(right, "num", t.value, t.pos)
                # ``1.0 *`` keeps the quotient real on Postgres and SQLite, where integer / integer truncates
                sql = f"({left.sql} * {right.sql})" if t.value == "*" else f"((1.0 * {left.sql}) / NULLIF({right.sql}, 0))"
                left = Node("num", sql, left.fields | right.fields)
            else:
                return left

    def _unary(self) -> Node:
        t = self._peek()
        if t.kind == "op" and t.value == "-":
            self._next()
            inner = self._unary()
            self._want(inner, "num", "unary -", t.pos)
            return Node("num", f"(-{inner.sql})", inner.fields)
        return self._atom()

    def _atom(self) -> Node:
        t = self._next()
        if t.kind == "num":
            return Node("num", t.value, set())
        if t.kind == "str":
            return Node("str", t.value, set())
        if t.kind == "op" and t.value == "(":
            inner = self._expr()
            self._expect("op", ")")
            return inner
        if t.kind == "kw" and t.value == "null":
            return Node("str", "NULL", set())
        if t.kind == "name":
            if self._accept("op", "("):
                return self._call(t)
            try:
                fld, ref = self.resolve(t.value)
            except KeyError:
                raise CalcError(f"unknown field {t.value!r}", t.pos) from None
            return Node(_field_type(fld), ref, {fld.entity})
        raise CalcError(f"unexpected {t.value or 'end'!r}", t.pos)

    def _call(self, name_tok: Tok) -> Node:
        name = name_tok.value.lower()
        args: list[Node] = []
        if not self._accept("op", ")"):
            while True:
                args.append(self._expr())
                if not self._accept("op", ","):
                    break
            self._expect("op", ")")
        pos = name_tok.pos
        fields: set[str] = set().union(*(a.fields for a in args)) if args else set()

        def arity(lo: int, hi: int | None = None) -> None:
            hi = lo if hi is None else hi
            if not (lo <= len(args) <= (hi if hi >= 0 else len(args))):
                raise CalcError(f"{name}() takes {lo if lo == hi else f'{lo}..{hi}'} argument(s)", pos)

        if name in ("year", "month", "day"):
            arity(1)
            self._want_date(args[0], name, pos)
            return Node("num", self._extract(name, self._as_date(args[0])), fields)
        if name == "days":
            arity(2)
            self._want_date(args[0], name, pos)
            self._want_date(args[1], name, pos)
            a, b = self._as_date(args[0]), self._as_date(args[1])
            if self.dialect == "sqlite":
                sql = f"CAST(julianday({b}) - julianday({a}) AS INTEGER)"
            elif self.dialect == "oracle":
                sql = f"(TRUNC({b}) - TRUNC({a}))"
            else:
                sql = f"(CAST({b} AS DATE) - CAST({a} AS DATE))"
            return Node("num", sql, fields)
        if name == "round":
            arity(1, 2)
            self._want(args[0], "num", "round", pos)
            if len(args) == 2:
                self._want(args[1], "num", "round", pos)
                return Node("num", f"ROUND({args[0].sql}, {args[1].sql})", fields)
            return Node("num", f"ROUND({args[0].sql})", fields)
        if name == "abs":
            arity(1)
            self._want(args[0], "num", "abs", pos)
            return Node("num", f"ABS({args[0].sql})", fields)
        if name == "coalesce":
            arity(2, -1)
            for a in args[1:]:
                if a.type != args[0].type:
                    raise CalcError(f"coalesce: every argument must be a {args[0].type}", pos)
            return Node(args[0].type, f"COALESCE({', '.join(self._plain(a) for a in args)})", fields)
        if name in ("upper", "lower"):
            arity(1)
            self._want(args[0], "str", name, pos)
            return Node("str", f"{name.upper()}({args[0].sql})", fields)
        if name == "length":
            arity(1)
            self._want(args[0], "str", name, pos)
            return Node("num", f"LENGTH({args[0].sql})", fields)
        if name == "concat":
            arity(2, -1)
            for a in args:
                self._want(a, "str", "concat", pos)
            return Node("str", self._concat(args), fields)
        raise CalcError(f"unknown function {name!r}", pos)

    # ── dialect pieces ──────────────────────────────────────────────────────────
    def _plain(self, node: Node) -> str:
        """A node's SQL as a value: a tagged date reference becomes a real date."""
        return self._as_date(node) if node.type == "date" else node.sql

    def _concat(self, parts: list[Node]) -> str:
        return "(" + " || ".join(p.sql for p in parts) + ")"

    def _want_date(self, node: Node, what: str, pos: int) -> None:
        if node.type != "date":
            raise CalcError(f"{what} needs a date, got {node.type}", pos)

    def _as_date(self, node: Node) -> str:
        """The node as a real date expression: a JDE Julian number or an ISO text column is
        converted; a native date passes through. The conversion kind rides on the node's sql
        through a marker set by the resolver (the ``#jde:`` prefix)."""
        sql = node.sql
        if sql.startswith("#jde:"):
            d = sql[5:]
            if self.dialect == "oracle":
                return f"TO_DATE(TO_CHAR(1900 + TRUNC({d} / 1000)) || LPAD(MOD({d}, 1000), 3, '0'), 'YYYYDDD')"
            if self.dialect == "sqlite":
                return f"date((1900 + ({d} / 1000)) || '-01-01', '+' || (({d} % 1000) - 1) || ' days')"
            return f"TO_DATE(CAST(1900 + FLOOR({d} / 1000) AS TEXT) || LPAD(CAST({d} % 1000 AS TEXT), 3, '0'), 'YYYYDDD')"
        return sql

    def _extract(self, part: str, d: str) -> str:
        if self.dialect == "sqlite":
            fmt = {"year": "%Y", "month": "%m", "day": "%d"}[part]
            return f"CAST(strftime('{fmt}', {d}) AS INTEGER)"
        return f"EXTRACT({part.upper()} FROM {d})"


def _field_type(fld: ExplorerField) -> Type:
    if fld.type == "number":
        return "num"
    if fld.type in ("date", "datetime"):
        return "date"
    if fld.type == "boolean":
        return "str"
    return "str"


def date_ref(fld: ExplorerField, ref: str) -> str:
    """The SQL reference a resolver should hand the compiler for a field: a JDE Julian date is
    tagged so :meth:`Compiler._as_date` converts it before a date function; anything else is
    the column itself."""
    if (fld.format or "").lower() == "jdedate":
        return f"#jde:{ref}"
    return ref


def render(node: Node) -> str:
    """The final SQL of a compiled expression: a boolean becomes ``'Y'`` / ``'N'``; a tagged
    date reference used as a plain value is untagged (the column itself)."""
    sql = node.sql
    if node.type == "bool":
        return f"CASE WHEN {sql} THEN 'Y' ELSE 'N' END"
    if sql.startswith("#jde:"):
        return sql[5:]
    return sql


def check_name(name: str) -> str:
    if not _NAME.match(name or ""):
        raise CalcError(f"calculated field name {name!r}: a letter then letters, digits or '_' (max 41)")
    return name


def result_type(node: Node) -> str:
    """The model type class of a compiled expression (what the field becomes)."""
    return {"num": "number", "str": "string", "bool": "string", "date": "date"}[node.type]

