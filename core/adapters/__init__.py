"""Site adapters.

Each supported website gets one module implementing `DirectoryAdapter`. Fixing a
redesigned site means editing (or adding) one file here; the engine, validation,
export and UI stay untouched.
"""
from .base import AdapterContext, DirectoryAdapter
from .registry import available_adapters, resolve_adapter

__all__ = ["AdapterContext", "DirectoryAdapter", "available_adapters", "resolve_adapter"]
