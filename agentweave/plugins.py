from __future__ import annotations

import importlib.metadata
import inspect
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable


PLUGIN_API_VERSION = "1"


@dataclass
class ComponentRegistry:
    """Component registry used by plugins and the runtime factory."""

    models: dict[str, Any] = field(default_factory=dict)
    catalogs: dict[str, Any] = field(default_factory=dict)
    executors: dict[str, Any] = field(default_factory=dict)
    routers: dict[str, Any] = field(default_factory=dict)
    policies: dict[str, Any] = field(default_factory=dict)
    integrations: dict[str, Any] = field(default_factory=dict)
    telemetry: dict[str, Any] = field(default_factory=dict)

    def register(self, kind: str, name: str, component: Any) -> None:
        bucket = getattr(self, kind, None)
        if not isinstance(bucket, dict):
            raise KeyError(f"unknown component kind: {kind}")
        if name in bucket:
            raise ValueError(f"duplicate {kind} component: {name}")
        bucket[name] = component

    def get(self, kind: str, name: str) -> Any:
        bucket = getattr(self, kind, None)
        if not isinstance(bucket, dict):
            raise KeyError(f"unknown component kind: {kind}")
        return bucket[name]


@runtime_checkable
class AgentWeavePlugin(Protocol):
    """Versioned plugin lifecycle contract."""

    name: str
    api_version: str

    def configure(self, registry: ComponentRegistry) -> None:
        ...

    async def start(self, runtime: Any) -> None:
        ...

    async def stop(self) -> None:
        ...


@dataclass(frozen=True)
class PluginState:
    name: str
    api_version: str
    configured: bool
    started: bool


class PluginManager:
    """Discover, validate, configure and lifecycle-manage AgentWeave plugins."""

    def __init__(
        self,
        group: str = "agentweave.plugins",
        *,
        api_version: str = PLUGIN_API_VERSION,
        registry: ComponentRegistry | None = None,
    ) -> None:
        self.group = group
        self.api_version = str(api_version)
        self.registry = registry or ComponentRegistry()
        self.plugins: dict[str, Any] = {}
        self._configured: set[str] = set()
        self._started: set[str] = set()

    @staticmethod
    def _major(version: str) -> str:
        return str(version).split(".", 1)[0]

    def _validate(self, name: str, plugin: Any) -> Any:
        plugin_version = str(getattr(plugin, "api_version", ""))
        if not plugin_version:
            raise ValueError(f"plugin {name!r} does not declare api_version")
        if self._major(plugin_version) != self._major(self.api_version):
            raise RuntimeError(
                f"plugin {name!r} targets API {plugin_version}; "
                f"runtime plugin API is {self.api_version}"
            )
        if not callable(getattr(plugin, "configure", None)):
            raise TypeError(f"plugin {name!r} must define configure(registry)")
        return plugin

    @staticmethod
    def _instantiate(value: Any) -> Any:
        return value() if inspect.isclass(value) else value

    def discover(self) -> dict[str, Any]:
        eps = importlib.metadata.entry_points()
        entries = (
            eps.select(group=self.group)
            if hasattr(eps, "select")
            else eps.get(self.group, [])
        )
        for ep in entries:
            self.register(ep.name, self._instantiate(ep.load()))
        return dict(self.plugins)

    def register(self, name: str, plugin: Any) -> Any:
        if name in self.plugins:
            raise ValueError(f"duplicate plugin: {name}")
        plugin = self._validate(name, self._instantiate(plugin))
        self.plugins[name] = plugin
        return plugin

    def get(self, name: str) -> Any:
        return self.plugins[name]

    def configure(self) -> ComponentRegistry:
        for name, plugin in self.plugins.items():
            if name in self._configured:
                continue
            plugin.configure(self.registry)
            self._configured.add(name)
        return self.registry

    async def start(self, runtime: Any) -> None:
        self.configure()
        started_now: list[str] = []
        try:
            for name, plugin in self.plugins.items():
                if name in self._started:
                    continue
                hook = getattr(plugin, "start", None)
                if hook is not None:
                    result = hook(runtime)
                    if inspect.isawaitable(result):
                        await result
                self._started.add(name)
                started_now.append(name)
        except Exception:
            for name in reversed(started_now):
                hook = getattr(self.plugins[name], "stop", None)
                try:
                    if hook is not None:
                        result = hook()
                        if inspect.isawaitable(result):
                            await result
                finally:
                    self._started.discard(name)
            raise

    async def stop(self) -> None:
        first_error: Exception | None = None
        for name in reversed(list(self.plugins)):
            if name not in self._started:
                continue
            hook = getattr(self.plugins[name], "stop", None)
            try:
                if hook is not None:
                    result = hook()
                    if inspect.isawaitable(result):
                        await result
            except Exception as exc:
                if first_error is None:
                    first_error = exc
            finally:
                self._started.discard(name)
        if first_error is not None:
            raise first_error

    def states(self) -> Mapping[str, PluginState]:
        return {
            name: PluginState(
                name=name,
                api_version=str(getattr(plugin, "api_version", "")),
                configured=name in self._configured,
                started=name in self._started,
            )
            for name, plugin in self.plugins.items()
        }
