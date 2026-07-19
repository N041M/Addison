"""Type-only view of the shared server state the handler mixins consume.

The handler bodies live in per-namespace mixins (``conversation.py``,
``providers.py``, ...) that ``JsonRpcServer`` composes. Each mixin references the
shared state and plumbing the composition root (``agent_core/main.py``) owns —
``self.store``, ``self._respond(...)``, ``self._mode()`` and friends. ``JsonRpcServer``
supplies all of them, but the type checker analyses each mixin in isolation, so it
needs those members *declared* somewhere the mixin inherits.

``ServerContext`` is that declaration and nothing more: at runtime it is an EMPTY
class (the body is entirely under ``TYPE_CHECKING``), so it contributes no behaviour
and no state — the real ``__init__``, properties, and helpers all live on
``JsonRpcServer``. Mixins inherit ``ServerContext`` purely so ``self.store`` etc.
resolve for the checker, WITHOUT importing ``main`` (which would be a cycle). The
declarations here are kept in lockstep with ``JsonRpcServer``; a mixin method that
overrides one (``_selection_error``, ``_set_provider_models``, ``_connections`` —
called across namespace boundaries) simply matches its signature.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable
    from typing import Any

    from agent_core.memory.store import Store
    from agent_core.models_catalog import CloudModel
    from agent_core.orchestrator import Conversation, Orchestrator
    from agent_core.permissions.gate import PermissionGate
    from agent_core.policy import PolicyMode
    from agent_core.profiles import Profile
    from agent_core.providers.base import ModelRole
    from agent_core.providers.router import ModelRouter
    from agent_core.routines.builder import RoutineBuilder
    from agent_core.routines.engine import RoutineEngine
    from agent_core.routines.library import RoutineLibrary
    from agent_core.routines.model import Routine
    from agent_core.shell_bridge import IpcShellBridge
    from agent_core.snapshots.undo_manager import UndoManager
    from agent_core.tools.registry import ToolRegistry


class ServerContext:
    """Declares (for the type checker only) the shared state + plumbing the mixins
    rely on; ``JsonRpcServer`` provides the real implementations."""

    if TYPE_CHECKING:
        # --- worker-built singletons (narrowing properties on JsonRpcServer) ---
        @property
        def store(self) -> Store: ...
        @property
        def undo_manager(self) -> UndoManager: ...
        @property
        def orchestrator(self) -> Orchestrator: ...
        @property
        def routine_builder(self) -> RoutineBuilder: ...
        @property
        def routine_library(self) -> RoutineLibrary: ...
        @property
        def routine_engine(self) -> RoutineEngine: ...

        # --- collaborators + shared state ---
        _store: Store | None
        conversation: Conversation
        model_router: ModelRouter
        tool_registry: ToolRegistry
        permission_gate: PermissionGate
        _shell_bridge: IpcShellBridge | None
        _active_profile: Profile | None
        _draft_routine: Routine | None
        _draft_widget: dict | None
        _last_run_routine_id: str | None
        _message_ids: list[str]
        _conversation_created: bool
        _conversation_titled: bool
        _next_role: ModelRole | None
        _next_model_name: str | None
        _next_effort: str | None
        _cloud_catalog: list[CloudModel]
        _cloud_fetcher: Callable[[], list[CloudModel]] | None
        _cloud_provider_factory: Callable[[CloudModel], Any] | None
        _cloud_catalog_loaded: bool
        _connect_provider: Callable[[str, str | None], list[CloudModel]] | None
        _provider_key_probe: Callable[[str], bool] | None
        _providers_reconnected: bool
        _ollama_base_url: str | None
        _ollama_client: Any | None
        _setup_prompt: str | None
        _primary_prompt: str | None
        _perm_lock: threading.Lock
        _permission_waiters: dict[str, dict]
        _local_setup_lock: threading.Lock
        _local_setup_active: bool

        # --- shared plumbing (implemented on JsonRpcServer) ---
        def _respond(self, request_id, result) -> None: ...
        def _respond_error(
            self, request_id, code: int, message: str, data: dict | None = None
        ) -> None: ...
        def _notify(self, method: str, params: dict) -> None: ...
        def _mode(self) -> PolicyMode: ...
        def _label(self, tool_id: str) -> str: ...
        def _ensure_built(self) -> None: ...
        def _primary_key_available(self) -> bool: ...
        @staticmethod
        def _role_from(role: str | None) -> ModelRole | None: ...

        # --- cross-namespace handler helpers (each defined in one mixin, called
        #     from another; declared here so both sides type-check) ---
        def _selection_error(
            self, role: ModelRole | None, model_id: str | None, effort: str | None
        ) -> str | None: ...
        def _set_provider_models(self, provider_id: str, models: list[CloudModel]) -> None: ...
        def _connections(self, latency: list[dict]) -> list[dict]: ...
