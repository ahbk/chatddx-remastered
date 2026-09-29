import hashlib
import hmac
import json
import types
import typing
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import cached_property
from typing import (
    Annotated,
    Any,
    ClassVar,
    Literal,
    cast,
    get_args,
    get_origin,
    override,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    GetJsonSchemaHandler,
    JsonValue,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    StringConstraints,
    model_serializer,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
JsonPointer = Annotated[str, StringConstraints(pattern=r"^(/([^~/]|~[01])*)*$")]


class StructuralError(ValueError):
    pass


def canonical_bytes(doc: JsonValue) -> bytes:
    return json.dumps(
        doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def sha256_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class Frozen(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    # Canonical form omits fields equal to their default, so adding a field whose default
    # preserves old behavior leaves every stored digest intact. `kind` is always kept
    # because it discriminates unions.
    @model_serializer(mode="wrap")
    def _serialize(
        self, handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ) -> Any:
        data = handler(self)
        context = cast(dict[str, object] | None, info.context)
        if not (context and context.get("canonical")):
            return data
        fields = cast(dict[str, object], data)
        for name, field in type(self).model_fields.items():
            if name == "kind" or field.is_required():
                continue
            if getattr(self, name) == field.get_default(call_default_factory=True):
                _ = fields.pop(name, None)
        return fields


@dataclass(frozen=True, init=False)
class RefTo:
    kinds: tuple[str, ...]

    def __init__(self, *kinds: str) -> None:
        object.__setattr__(self, "kinds", kinds)

    def __get_pydantic_json_schema__(
        self, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        schema = handler(core_schema)
        schema["x-ref"] = list(self.kinds)
        return schema


@dataclass(frozen=True)
class RefSite:
    path: str
    kinds: tuple[str, ...]
    digest: str


def iter_refs(model: BaseModel, prefix: str = "") -> Iterator[RefSite]:
    for name, field in type(model).model_fields.items():
        tp: Any = field.annotation
        if field.metadata:
            tp = Annotated[tp, *field.metadata]
        yield from _walk(tp, getattr(model, name), f"{prefix}/{name}")


def _walk(tp: Any, value: Any, path: str) -> Iterator[RefSite]:
    if value is None:
        return
    origin = get_origin(tp)
    if origin is Annotated:
        base, *meta = get_args(tp)
        for m in meta:
            if isinstance(m, RefTo):
                yield RefSite(path, m.kinds, value)
                return
        yield from _walk(base, value, path)
    elif isinstance(value, BaseModel):
        yield from iter_refs(value, path)
    elif origin is typing.Union or origin is types.UnionType:  # pyright: ignore[reportDeprecated]
        for arm in get_args(tp):
            found = list(_walk(arm, value, path))
            if found:
                yield from found
                return
    elif origin in (tuple, list, frozenset):
        elem = get_args(tp)[0]
        for i, v in enumerate(value):
            yield from _walk(elem, v, f"{path}/{i}")
    elif origin is dict:
        elem = get_args(tp)[1]
        for k, v in value.items():
            yield from _walk(elem, v, f"{path}/{k}")


Resolver = Callable[[str], "Component"]


class Component(Frozen):
    # Bump when an existing field's meaning or default changes; additive fields don't.
    schema_version: ClassVar[int] = 1
    case_derived: ClassVar[bool] = False
    registry: ClassVar[dict[str, type["Component"]]] = {}

    @classmethod
    @override
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        field = cls.model_fields.get("kind")
        if field is None:
            return
        kind = field.default
        if not isinstance(kind, str):
            raise TypeError(f"{cls.__name__}.kind needs a literal default")
        if kind in Component.registry and Component.registry[kind] is not cls:
            raise TypeError(f"duplicate component kind {kind!r}")
        Component.registry[kind] = cls

    @property
    def kind_name(self) -> str:
        kind = type(self).model_fields["kind"].default
        assert isinstance(kind, str)
        return kind

    def canonical_doc(self) -> dict[str, JsonValue]:
        doc = self.model_dump(mode="json", context={"canonical": True})
        doc["v"] = type(self).schema_version
        return doc

    @cached_property
    def canonical(self) -> bytes:
        return canonical_bytes(self.canonical_doc())

    @cached_property
    def digest(self) -> str:
        return sha256_digest(self.canonical)

    def refs(self) -> list[RefSite]:
        return list(iter_refs(self))

    def cross_check(self, get: Resolver) -> list[str]:  # pyright: ignore[reportUnusedParameter]
        return []


def parse_component(data: bytes) -> Component:
    loaded: object = json.loads(data)
    if not isinstance(loaded, dict):
        raise StructuralError("component is not a JSON object")
    doc = cast(dict[str, object], loaded)
    kind = doc.get("kind")
    cls = Component.registry.get(kind) if isinstance(kind, str) else None
    if cls is None:
        raise StructuralError(f"unknown component kind {kind!r}")
    v = doc.pop("v", None)
    if v != cls.schema_version:
        raise StructuralError(f"{kind} v{v} is not readable by v{cls.schema_version}")
    return cls.model_validate(doc)


class Hmac(Frozen):
    key_id: str
    hex: Sha256Hex

    @classmethod
    def of(cls, key_id: str, key: bytes, data: bytes) -> "Hmac":
        return cls(key_id=key_id, hex=hmac.new(key, data, hashlib.sha256).hexdigest())


class Code(Frozen):
    distribution: str
    version: str
    revision: str | None = None


class Finding(Frozen):
    level: Literal["info", "warning"] = "warning"
    code: str
    message: str
    subject: str | None = None
