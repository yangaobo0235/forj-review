"""Reject unknown nested declaration properties before dataclass conversion."""

from dataclasses import fields, is_dataclass
from math import isfinite
from types import UnionType
from typing import Annotated, Union, get_args, get_origin, get_type_hints


def validate_declaration_data(value, annotation, path="settings"):
    if value is None:
        return
    origin, arguments = get_origin(annotation), get_args(annotation)
    if origin is Annotated:
        return validate_declaration_data(value, arguments[0], path)
    if origin in (Union, UnionType):
        choices = [kind for kind in arguments if kind is not type(None)]
        if len(choices) == 1:
            validate_declaration_data(value, choices[0], path)
    elif is_dataclass(annotation) and isinstance(value, dict):
        hints = get_type_hints(annotation)
        unknown = value.keys() - {field.name for field in fields(annotation)}
        if unknown:
            raise ValueError(f"{path} 包含未知属性：{', '.join(sorted(unknown))}")
        for key, item in value.items():
            validate_declaration_data(item, hints[key], path + "." + key)
    elif origin in (tuple, list) and isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            kind = (
                arguments[0]
                if len(arguments) == 2 and arguments[1] is Ellipsis
                else (arguments[index] if index < len(arguments) else None)
            )
            if kind:
                validate_declaration_data(item, kind, path + f"[{index}]")
    elif annotation is bool and type(value) is not bool:
        raise ValueError(f"{path} 必须使用 JSON 布尔值")
    elif annotation is int and type(value) is not int:
        raise ValueError(f"{path} 必须使用 JSON 整数")
    elif annotation is float and (type(value) not in (int, float) or not isfinite(value)):
        raise ValueError(f"{path} 必须使用有限数值")
