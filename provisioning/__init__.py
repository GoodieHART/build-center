"""Provisioner registry.

Provides a decorator-based registry for provisioner classes,
mirroring the pattern established in ``builders``.
"""

from typing import Any, Dict, Type


PROVISIONER_REGISTRY: Dict[str, Type] = {}


def register(type_name: str):
    """Decorator factory that registers a provisioner class under *type_name*.

    Usage::

        @register("android")
        class AndroidProvisioner:
            ...
    """
    def decorator(cls: Type) -> Type:
        PROVISIONER_REGISTRY[type_name] = cls
        return cls
    return decorator


def get_provisioner(type_name: str) -> Type:
    """Look up a provisioner class by type name.

    Raises ``KeyError`` with a helpful message listing available provisioners
    when *type_name* has not been registered.
    """
    try:
        return PROVISIONER_REGISTRY[type_name]
    except KeyError:
        available = list_provisioners()
        raise KeyError(
            f"Unknown provisioner '{type_name}'. "
            f"Available provisioners: {available}"
        ) from None


def list_provisioners() -> list:
    """Return a sorted list of registered provisioner type names."""
    return sorted(PROVISIONER_REGISTRY.keys())
