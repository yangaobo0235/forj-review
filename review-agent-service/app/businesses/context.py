"""Per-request immutable declarations. Async tasks inherit context, other requests do not."""

import inspect
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

_pack = ContextVar("review_business_pack", default=None)


def current_pack():
    return _pack.get()


@contextmanager
def business_context(profile):
    token = _pack.set(getattr(profile, "pack_declaration", None))
    try:
        yield
    finally:
        _pack.reset(token)


def with_business_context(function):
    signature = inspect.signature(function)

    def resolve(args, kwargs):
        bound = signature.bind(*args, **kwargs).arguments
        profile = bound.get("profile")
        if profile is None:
            owner = bound["self"]
            service = getattr(owner, "service", owner)
            profile = service.resolve_profile(bound["request"])
        return profile

    if inspect.iscoroutinefunction(function):
        @wraps(function)
        async def asynchronous(*args, **kwargs):
            profile = resolve(args, kwargs)
            bound = signature.bind(*args, **kwargs)
            if "profile" in signature.parameters:
                bound.arguments["profile"] = profile
            with business_context(profile):
                return await function(*bound.args, **bound.kwargs)
        return asynchronous

    @wraps(function)
    def synchronous(*args, **kwargs):
        profile = resolve(args, kwargs)
        bound = signature.bind(*args, **kwargs)
        if "profile" in signature.parameters:
            bound.arguments["profile"] = profile
        with business_context(profile):
            return function(*bound.args, **bound.kwargs)
    return synchronous


class ContextMapping(Mapping):
    def __init__(self, fallback, project):
        self.fallback, self.project = fallback, project

    def data(self):
        pack = current_pack()
        return self.fallback if pack is None else self.project(pack)

    def __getitem__(self, key):
        return self.data()[key]

    def __iter__(self):
        return iter(self.data())

    def __len__(self):
        return len(self.data())


class ContextSequence(Sequence):
    def __init__(self, fallback, project):
        self.fallback, self.project = fallback, project

    def data(self):
        pack = current_pack()
        return self.fallback if pack is None else self.project(pack)

    def __getitem__(self, key):
        return self.data()[key]

    def __len__(self):
        return len(self.data())
