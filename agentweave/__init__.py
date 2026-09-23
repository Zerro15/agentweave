from __future__ import annotations

import importlib
import warnings

from .config import (
    AgentWeaveBuilder,
    CatalogConfig,
    ModelConfig,
    RoutingConfig,
    RuntimeConfig,
    RuntimeFactory,
    RuntimeLimits,
)
from .orchestrator import AgentWeave
from .plugins import (
    PLUGIN_API_VERSION,
    AgentWeavePlugin,
    ComponentRegistry,
    PluginManager,
)
from .runtime import (
    AgentWeaveRuntime,
    CallableExecutor,
    CatalogProvider,
    DefaultScopePolicy,
    Executor,
    RoutingPreview,
    RuntimeAuthorizationPolicy,
    ScopePolicy,
    StaticToolCatalog,
    ToolSearchProvider,
    normalize_model_response,
)
from .runtime_types import (
    ModelResponse,
    RunContext,
    RuntimeResult,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from .safe_http import SafeHttpTransport

__version__ = "0.6.0"

__all__ = [
    "AgentWeave",
    "AgentWeaveRuntime",
    "ToolSpec",
    "ToolCall",
    "ToolResult",
    "ModelResponse",
    "RunContext",
    "RuntimeResult",
    "RoutingPreview",
    "CatalogProvider",
    "Executor",
    "ScopePolicy",
    "ToolSearchProvider",
    "StaticToolCatalog",
    "CallableExecutor",
    "DefaultScopePolicy",
    "RuntimeAuthorizationPolicy",
    "normalize_model_response",
    "RuntimeConfig",
    "ModelConfig",
    "CatalogConfig",
    "RoutingConfig",
    "RuntimeLimits",
    "AgentWeaveBuilder",
    "RuntimeFactory",
    "SafeHttpTransport",
    "AgentWeavePlugin",
    "ComponentRegistry",
    "PluginManager",
    "PLUGIN_API_VERSION",
]

_LEGACY_MODULES = (
    "agentweave.models",
    "agentweave.requirements",
    "agentweave.graph",
    "agentweave.advanced_graph",
    "agentweave.engine",
    "agentweave.optimizer",
    "agentweave.validation",
    "agentweave.semantic",
    "agentweave.discovery",
    "agentweave.marketplaces",
    "agentweave.a2a",
    "agentweave.interoperability",
    "agentweave.lifecycle",
    "agentweave.protocol_depth",
    "agentweave.edge",
    "agentweave.edge_lab",
    "agentweave.identity",
    "agentweave.identity_proof",
    "agentweave.sandbox",
    "agentweave.security_lab",
    "agentweave.storage",
    "agentweave.storage_proof",
    "agentweave.chaos",
    "agentweave.observability",
    "agentweave.policy",
    "agentweave.benchmarks",
    "agentweave.research",
    "agentweave.sdk",
    "agentweave.native",
    "agentweave.recovery",
    "agentweave.workflow",
    "agentweave.durable",
)


def __getattr__(name: str):
    for module_name in _LEGACY_MODULES:
        module = importlib.import_module(module_name)
        if hasattr(module, name):
            value = getattr(module, name)
            warnings.warn(
                f"agentweave.{name} is a legacy root import and is not part of the "
                "pre-1.0 stable surface; import it from its defining module instead",
                DeprecationWarning,
                stacklevel=2,
            )
            globals()[name] = value
            return value
    raise AttributeError(f"module 'agentweave' has no attribute {name!r}")
