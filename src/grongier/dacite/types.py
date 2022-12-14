from dataclasses import InitVar
from typing import Type, Any, Optional, Union, Collection, TypeVar, Callable, Mapping, AbstractSet

from grongier.dacite.cache import Cache

NoneType: Type = type(None)

T = TypeVar("T", bound=Any)


def make_get_value_transformer(
    cache: Cache,
) -> Callable[[Mapping[Type, Callable[[Any], Any]], AbstractSet[Type], Type, Type], Callable[[Any], Any]]:
    @cache.cache
    def get_value_transformer(
        type_hooks: Mapping[Type, Callable[[Any], Any]], cast: AbstractSet[Type], target_type: Type, value_type: Type
    ) -> Callable[[Any], Any]:
        transformer = lambda x: x
        is_type_hook = False
        if target_type in type_hooks:
            is_type_hook = True
            transformer = type_hooks[target_type]
        else:
            sub_type = getattr(target_type, "__origin__", target_type)
            if isinstance(sub_type, type):
                for cast_type in cast:
                    if issubclass(sub_type, cast_type):
                        if is_generic_collection(target_type):
                            transformer = extract_origin_collection(target_type)
                        else:
                            transformer = target_type
        if not is_type_hook:
            if is_optional(target_type):
                original_transformer = transformer
                target_optional_type: Type = extract_optional(target_type)
                transformer = (
                    lambda x: original_transformer(
                        get_value_transformer(type_hooks, cast, target_optional_type, type(x))(x)
                    )
                    if x is not None
                    else original_transformer(None)
                )
            if is_generic_collection(target_type):
                original_transformer = transformer
                if issubclass(extract_origin_collection(target_type), Mapping):
                    key_cls, item_cls = target_type.__args__
                    transformer = lambda x: value_type(
                        {
                            original_transformer(
                                get_value_transformer(type_hooks, cast, key_cls, type(key))(key)
                            ): original_transformer(get_value_transformer(type_hooks, cast, item_cls, type(item))(item))
                            for key, item in x.items()
                        }
                    )
                else:
                    item_cls = target_type.__args__[0]
                    transformer = lambda x: original_transformer(
                        value_type(get_value_transformer(type_hooks, cast, item_cls, type(item))(item) for item in x)
                    )
        return transformer

    return get_value_transformer


def extract_origin_collection(collection: Type) -> Type:
    return getattr(collection, "__extra__", collection.__origin__)


def is_optional(type_: Type) -> bool:
    return getattr(type_, "__origin__", None) is Union and NoneType in type_.__args__


def extract_optional(optional: Type[Optional[T]]) -> T:
    for type_ in optional.__args__:  # type: ignore
        if type_ is not NoneType:
            return type_
    raise ValueError("can not find not-none value")


def is_union(type_: Type) -> bool:
    return getattr(type_, "__origin__", None) is Union


def is_literal(type_: Type) -> bool:
    try:
        from typing import Literal  # type: ignore

        return getattr(type_, "__origin__", None) is Literal
    except ImportError:
        return False


def is_new_type(type_: Type) -> bool:
    return hasattr(type_, "__supertype__")


def extract_new_type(type_: Type) -> Type:
    return type_.__supertype__


def is_init_var(type_: Type) -> bool:
    return isinstance(type_, InitVar) or type_ is InitVar


def is_instance(value: Any, type_: Type) -> bool:
    if type_ == Any:
        return True
    elif is_union(type_):
        types = []
        for inner_type in type_.__args__:
            if hasattr(inner_type, "__origin__") and not is_literal(inner_type):
                inner_type = extract_origin_collection(inner_type)
            if is_new_type(inner_type):
                inner_type = extract_new_type(inner_type)
            types.append(inner_type)
        return any(is_instance(value, t) for t in types)
    elif is_generic_collection(type_):
        origin = extract_origin_collection(type_)
        if not isinstance(value, origin):
            return False
        if not _has_specified_inner_types(type_):
            return True
        if isinstance(value, tuple):
            tuple_types = type_.__args__
            if len(tuple_types) == 1 and tuple_types[0] == ():
                return len(value) == 0
            elif len(tuple_types) == 2 and tuple_types[1] is ...:
                return all(is_instance(item, tuple_types[0]) for item in value)
            else:
                if len(tuple_types) != len(value):
                    return False
                return all(is_instance(item, item_type) for item, item_type in zip(value, tuple_types))
        if isinstance(value, Mapping):
            key_type, val_type = type_.__args__
            for key, val in value.items():
                if not is_instance(key, key_type) or not is_instance(val, val_type):
                    return False
            return True
        return all(is_instance(item, type_.__args__[0]) for item in value)
    elif is_new_type(type_):
        return is_instance(value, extract_new_type(type_))
    elif is_literal(type_):
        return value in type_.__args__
    elif is_init_var(type_):
        if hasattr(type_, "type"):
            return is_instance(value, type_.type)
        return True
    else:
        try:
            # As described in PEP 484 - section: "The numeric tower"
            if isinstance(value, (int, float)) and type_ in [float, complex]:
                return True
            return isinstance(value, type_)
        except TypeError:
            return False


def _has_specified_inner_types(type_: Type) -> bool:
    try:
        return not type_._special
    except AttributeError:
        return bool(type_.__args__)


def is_generic_collection(type_: Type) -> bool:
    origin = getattr(type_, "__origin__", None)
    return origin and isinstance(origin, type) and issubclass(origin, Collection)
