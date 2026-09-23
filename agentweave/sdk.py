from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import RuntimeConfig, RuntimeFactory
from .plugins import PluginManager


@dataclass
class AgentWeaveConfig:
    """Backward-compatible flat configuration for the legacy orchestration CLI.

    New plug-and-play runtime applications should use ``RuntimeConfig``.  This class is
    intentionally retained through the pre-1.0 transition so existing JSON/YAML config
    files continue to load without requiring model/catalog runtime sections.
    """

    db_path: str = "agentweave.db"
    max_agents: int = 5
    rounds: int = 2
    use_native: bool = True
    require_signed_cards: bool = False
    settings: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path):
        source = Path(path)
        text = source.read_text()
        if source.suffix.lower() in {".yaml", ".yml"}:
            try:
                import yaml
            except ImportError as exc:
                raise RuntimeError(
                    "Install YAML support with: pip install 'agentweave-router[yaml]'"
                ) from exc
            data = yaml.safe_load(text) or {}
        else:
            data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("legacy AgentWeave config must be an object")
        payload = dict(data)
        known = {
            key: payload.pop(key)
            for key in list(payload)
            if key in cls.__dataclass_fields__ and key != "settings"
        }
        explicit_settings = payload.pop("settings", None)
        settings = dict(explicit_settings or {})
        settings.update(payload)
        return cls(**known, settings=settings)

    def to_dict(self):
        return {
            "db_path": self.db_path,
            "max_agents": self.max_agents,
            "rounds": self.rounds,
            "use_native": self.use_native,
            "require_signed_cards": self.require_signed_cards,
            **self.settings,
        }


class AgentWeaveSDK:
    """Stable facade for the legacy agent-orchestration surface."""

    API_VERSION = "1"

    def __init__(self, weave):
        self.weave = weave

    async def register(self, agent):
        return self.weave.register(agent)

    async def solve(self, requirement, **kwargs):
        return await self.weave.solve(requirement, **kwargs)

    async def ingest(self, connector, **kwargs):
        return await self.weave.ingest_marketplace(connector, **kwargs)

    async def interop(self, targets, **kwargs):
        return await self.weave.test_interoperability(targets, **kwargs)

    def agents(self):
        return self.weave.registry.all()

    def graph_stats(self):
        return self.weave.knowledge_graph.stats()

    def observability(self):
        return self.weave.observability.snapshot()


def _version():
    for distribution in ("agentweave-router", "agentweave"):
        try:
            return importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            continue
    return "0+local"


def _doctor(weave):
    checks = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "native_acceleration": weave.matcher.native_available,
        "docker": bool(shutil.which("docker")),
        "bubblewrap": bool(shutil.which("bwrap")),
        "llama_cpp": bool(shutil.which("llama-cli")),
    }
    try:
        import psycopg  # noqa: F401

        checks["postgres_driver"] = True
    except Exception:
        checks["postgres_driver"] = False
    try:
        import grpc  # noqa: F401

        checks["grpc"] = True
    except Exception:
        checks["grpc"] = False
    try:
        import mcp  # noqa: F401

        checks["mcp"] = True
    except Exception:
        checks["mcp"] = False
    return checks


async def _run_cli(args):
    from .orchestrator import AgentWeave

    if args.command == "version":
        print(_version())
        return 0

    if args.command == "config-check":
        if not args.config:
            raise SystemExit("--config is required for config-check")
        cfg = RuntimeConfig.load(args.config)
        print(json.dumps(cfg.to_dict(), indent=2, default=str))
        return 0

    if args.command == "run":
        if not args.config:
            raise SystemExit("--config is required for run")
        runtime = RuntimeFactory.from_file(args.config)
        result = await runtime.run(args.requirement)
        print(json.dumps(asdict(result), indent=2, default=str))
        return 0

    legacy_cfg = AgentWeaveConfig.load(args.config) if args.config else AgentWeaveConfig()
    weave = AgentWeave(db_path=legacy_cfg.db_path, use_native=legacy_cfg.use_native)
    weave.registry.load_persisted()
    if args.command == "agents":
        print(
            json.dumps(
                [agent.to_dict() for agent in weave.registry.all()],
                indent=2,
                default=str,
            )
        )
        return 0
    if args.command == "solve":
        print(
            json.dumps(
                await weave.solve(
                    args.requirement,
                    max_agents=args.max_agents or legacy_cfg.max_agents,
                    rounds=args.rounds or legacy_cfg.rounds,
                    semantic_verify=args.semantic_verify,
                ),
                indent=2,
                default=str,
            )
        )
        return 0
    if args.command == "doctor":
        print(json.dumps(_doctor(weave), indent=2))
        return 0
    if args.command == "graph-stats":
        print(json.dumps(weave.knowledge_graph.stats(), indent=2))
        return 0
    if args.command == "plugins":
        manager = PluginManager()
        manager.discover()
        print(json.dumps(sorted(manager.plugins), indent=2))
        return 0
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="agentweave")
    parser.add_argument("--config")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("agents")

    solve = sub.add_parser("solve")
    solve.add_argument("requirement")
    solve.add_argument("--semantic-verify", action="store_true")
    solve.add_argument("--max-agents", type=int)
    solve.add_argument("--rounds", type=int)

    run = sub.add_parser("run")
    run.add_argument("requirement")

    sub.add_parser("doctor")
    sub.add_parser("graph-stats")
    sub.add_parser("plugins")
    sub.add_parser("version")
    sub.add_parser("config-check")
    args = parser.parse_args(argv)
    import asyncio

    return asyncio.run(_run_cli(args))
