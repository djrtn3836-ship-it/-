# -*- coding: utf-8 -*-
"""
observability/auto_trace.py - V10 Auto-instrumentation utilities v1.1
(Session 33: mypy strict 적용 — 반환 타입/제네릭 타입 명시, 로직 무변경)
"""

import inspect
import sys
import types
from typing import Any, Callable, Optional, Set, Type

from observability.tracer import get_tracer


class TracedService:
    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        tracer = get_tracer(cls.__module__)
        for name, member in inspect.getmembers(cls, predicate=callable):
            if name.startswith("_"):
                continue
            if name not in cls.__dict__:
                continue
            if hasattr(member, "__wrapped__"):
                continue
            setattr(cls, name, tracer.traced(member))


def auto_trace_module(module_name: str, exclude: Optional[Set[str]] = None) -> None:
    if exclude is None:
        exclude = set()
    module = sys.modules.get(module_name)
    if module is None:
        return
    tracer = get_tracer(module_name)
    for name in dir(module):
        if name.startswith("_"):
            continue
        if name in exclude:
            continue
        attr = getattr(module, name)
        if not isinstance(attr, types.FunctionType):
            continue
        if attr.__module__ != module_name:
            continue
        if hasattr(attr, "__wrapped__"):
            continue
        setattr(module, name, tracer.traced(attr))


def trace_class(module_name: Optional[str] = None) -> Callable[[Type[Any]], Type[Any]]:
    def decorator(cls: Type[Any]) -> Type[Any]:
        target_module = module_name or cls.__module__
        tracer = get_tracer(target_module)
        for name, member in inspect.getmembers(cls, predicate=callable):
            if name.startswith("_"):
                continue
            if name not in cls.__dict__:
                continue
            if hasattr(member, "__wrapped__"):
                continue
            setattr(cls, name, tracer.traced(member))
        return cls
    return decorator
