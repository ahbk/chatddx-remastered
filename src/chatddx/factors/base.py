import hashlib
import hmac
import json
import types
import typing
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from functools import cached_property
from typing import (
    Annotated,
    Any,
    ClassVar,
    Literal,
    Self,
    cast,
    get_args,
    get_origin,
    override,
)

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    JsonValue,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    StringConstraints,
    model_serializer,
    model_validator,
)

Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
JsonPointer = Annotated[str, StringConstraints(pattern=r"^(/([^~/]|~[01])*)*$")]
Api = Literal["chat.completions"]


class StructuralError(ValueError):
    pass


# JSON data keeps its key order: a schema's property order is part of what a model
# reads. Field names and settings are sorted where they are built (`sorted_keys`).
def canonical_bytes(doc: JsonValue) -> bytes:
    return json.dumps(
        doc, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def sorted_keys[V](mapping: dict[str, V]) -> dict[str, V]:
    return dict(sorted(mapping.items()))


def distinct[T](values: tuple[T, ...]) -> tuple[T, ...]:
    if len(set(values)) != len(values):
        raise ValueError("duplicates are not allowed")
    return values


# A settings mapping's keys name options whose order means nothing, so equivalent
# settings share a digest; their values are JSON data and keep their order.
Settings = Annotated[dict[str, JsonValue], AfterValidator(sorted_keys)]


def sha256_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class Frozen(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    # Canonical form omits fields equal to their default, so adding a field whose default
    # preserves old behavior leaves every stored digest intact. `kind` and `stage` are
    # always kept because they discriminate unions.
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
            if name in ("kind", "stage") or field.is_required():
                continue
            if getattr(self, name) == field.get_default(call_default_factory=True):
                _ = fields.pop(name, None)
        return sorted_keys(fields)


@dataclass(frozen=True, init=False)
class RefTo:
    kinds: tuple[str, ...]

    def __init__(self, *kinds: str) -> None:
        object.__setattr__(self, "kinds", kinds)


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
        return sorted_keys({**doc, "v": type(self).schema_version})

    @cached_property
    def canonical(self) -> bytes:
        return canonical_bytes(self.canonical_doc())

    @cached_property
    def digest(self) -> str:
        return sha256_digest(self.canonical)

    # A copy starts with the original's cached canonical form and digest.
    @override
    def model_copy(
        self, *, update: Mapping[str, Any] | None = None, deep: bool = False
    ) -> Self:
        copy = super().model_copy(update=update, deep=deep)
        for cached in ("canonical", "digest"):
            _ = vars(copy).pop(cached, None)
        return copy

    def refs(self) -> list[RefSite]:
        return list(iter_refs(self))

    def cross_check(self, get: Resolver) -> list[str]:  # pyright: ignore[reportUnusedParameter]
        return []


def resolve[C: Component](get: Resolver, digest: str, cls: type[C]) -> C:
    component = get(digest)
    if not isinstance(component, cls):
        raise StructuralError(
            f"{digest} is a {component.kind_name}, not a {cls.__name__}"
        )
    return component


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


class Fingerprint(Frozen):
    alg: Literal["sha256", "hmac-sha256"] = "sha256"
    key_id: str | None = None
    hex: Sha256Hex

    @model_validator(mode="after")
    def _key_id(self) -> "Fingerprint":
        if (self.alg == "hmac-sha256") != (self.key_id is not None):
            raise ValueError("key_id goes with hmac-sha256, and only with it")
        return self

    @classmethod
    def of(cls, data: bytes, key: tuple[str, bytes] | None = None) -> "Fingerprint":
        if key is None:
            return cls(hex=hashlib.sha256(data).hexdigest())
        key_id, secret = key
        return cls(
            alg="hmac-sha256",
            key_id=key_id,
            hex=hmac.new(secret, data, hashlib.sha256).hexdigest(),
        )


class Code(Frozen):
    distribution: str
    version: str
    revision: str | None = None


class Finding(Frozen):
    level: Literal["info", "warning"] = "warning"
    code: str
    message: str
    subject: str | None = None
