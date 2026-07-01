"""Builder registry and abstract base class.

Provides a decorator-based registry for build strategy classes
and the BuilderBase ABC that all builders must implement.
"""

import abc
from typing import Any, Dict, Optional, Type


BUILDER_REGISTRY: Dict[str, Type["BuilderBase"]] = {}


def register(type_name: str):
    """Decorator factory that registers a builder class under *type_name*.

    Usage::

        @register("android")
        class AndroidBuilder(BuilderBase):
            ...
    """
    def decorator(cls: Type["BuilderBase"]) -> Type["BuilderBase"]:
        BUILDER_REGISTRY[type_name] = cls
        return cls
    return decorator


def get_builder(type_name: str) -> Type["BuilderBase"]:
    """Look up a builder class by type name.

    Raises ``KeyError`` with a helpful message listing available builders
    when *type_name* has not been registered.
    """
    try:
        return BUILDER_REGISTRY[type_name]
    except KeyError:
        available = list_builders()
        raise KeyError(
            f"Unknown builder '{type_name}'. "
            f"Available builders: {available}"
        ) from None


def list_builders() -> list:
    """Return a sorted list of registered builder type names."""
    return sorted(BUILDER_REGISTRY.keys())


class BuilderBase(abc.ABC):
    """Abstract base for all build strategies.

    Subclasses **must** define:
        - ``name`` (class or instance attribute)
        - ``provision(self, config)``
        - ``build(self, config)``
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Human-readable name for this builder."""

    @abc.abstractmethod
    def provision(self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Prepare the build environment / dependencies.

        Returns ``None`` or a dict of provisioning metadata.
        """

    @abc.abstractmethod
    def build(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the build.

        Returns a dict with build results (e.g. artifact paths).
        """
