"""
Workspace Scope — filesystem and datastore partitioning per active workspace.

Scope hooks are the Phase 2 companion to Phase 1 identity bindings. Where
identity hooks answer "who am I sending mail as?" per workspace, scope
hooks answer "which directory am I allowed to write to?" and "which
Store domain do my entities default to?"

Key distinctions from identity hooks:
  - Scope hooks are resolver-only: no credentials, no registry, no CLI.
  - Scope hooks have conventional defaults derived from the workspace
    name — no bindings.yaml entry needed for the common case.
  - There's exactly one filesystem and one Store table; scope hooks
    partition them, not multiplex them.

Enforcement mode (config.SCOPE_MODE):
  - "advisory"  — scope violations log a warning; tool proceeds. Default.
  - "enforce"   — scope violations raise ScopeViolationError; tool fails.

Advisory mode exists because the "enable, don't enforce" pattern
matters here: Claude-the-in-context-instance is the real source of truth
for "which workspace are we in", and the session-state cache is just a
hint. Until workspace_activate has been used enough to know it's
reliable, enforcement would produce false-positive failures on legitimate
cross-workspace work.

Usage:

    from core.scope import validate_fs_write, validate_fs_read, resolve_domain

    # Filesystem write/read with scope + sandbox both applied:
    path = await validate_fs_write(ctx, user_path, workspace_override=workspace)
    path = await validate_fs_read(ctx, user_path, workspace_override=workspace)

    # Datastore domain resolution:
    domain = await resolve_domain(ctx, explicit_domain, workspace_override=workspace)

Graceful degradation:
  If no active workspace is set, scope returns None — callers treat that
  as "full access" (matches Phase 1 identity behavior where no workspace
  means callers must pass explicit values).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastmcp import Context

from config import DATA_ROOT, WORKSPACES_DIR, SCOPE_MODE
from core.bindings import get_active_workspace, load_bindings
from core.paths import validate as base_validate

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────
# Errors
# ────────────────────────────────────────────────────────────────


@dataclass
class ScopeViolationError(Exception):
    """Raised when a tool call requests access outside the workspace's scope."""

    workspace: Optional[str]
    resource_type: str  # "filesystem" | "datastore"
    requested: str
    allowed: list[str]
    action: str  # "read" | "write" | "domain_access"

    def __str__(self) -> str:
        return self.user_message()

    def user_message(self) -> str:
        ws = self.workspace or "(no active workspace)"
        if self.resource_type == "filesystem":
            return (
                f"❌ Scope violation: workspace '{ws}' cannot {self.action} "
                f"path '{self.requested}'. Allowed roots: {self.allowed}."
            )
        if self.resource_type == "datastore":
            return (
                f"❌ Scope violation: workspace '{ws}' cannot access Store "
                f"domain '{self.requested}'. Allowed domains: {self.allowed}."
            )
        return f"❌ Scope violation ({self.resource_type}): {self.requested}"


# ────────────────────────────────────────────────────────────────
# Containment helper
# ────────────────────────────────────────────────────────────────


def is_within(path: Path, root: Path) -> bool:
    """Return True iff `path` is inside `root`, resolving symlinks first.

    Soft check — prevents accidents and obvious misuse. A determined
    attacker with shell access can still escape; that's a container
    concern, not a scope concern.
    """
    try:
        rp = path.resolve()
        rr = root.resolve()
        rp.relative_to(rr)
        return True
    except (ValueError, OSError):
        return False


# ────────────────────────────────────────────────────────────────
# Filesystem scope
# ────────────────────────────────────────────────────────────────


@dataclass
class FilesystemScope:
    """Per-workspace filesystem sandbox."""

    workspace: str
    writable_roots: list[Path] = field(default_factory=list)
    readable_roots: list[Path] = field(default_factory=list)

    def validate_write(self, path: Path) -> None:
        if not any(is_within(path, root) for root in self.writable_roots):
            raise ScopeViolationError(
                workspace=self.workspace,
                resource_type="filesystem",
                requested=str(path),
                allowed=[str(r) for r in self.writable_roots],
                action="write",
            )

    def validate_read(self, path: Path) -> None:
        # Writable roots are implicitly readable.
        roots = self.readable_roots + self.writable_roots
        if not any(is_within(path, root) for root in roots):
            raise ScopeViolationError(
                workspace=self.workspace,
                resource_type="filesystem",
                requested=str(path),
                allowed=[str(r) for r in roots],
                action="read",
            )

    def default_root(self) -> Path:
        """Where relative paths resolve to. First writable_root wins."""
        if self.writable_roots:
            return self.writable_roots[0]
        return WORKSPACES_DIR / self.workspace


def _conventional_fs_scope(workspace: str) -> FilesystemScope:
    return FilesystemScope(
        workspace=workspace,
        writable_roots=[WORKSPACES_DIR / workspace],
        readable_roots=[
            WORKSPACES_DIR / workspace,
            WORKSPACES_DIR / "_shared",
        ],
    )


def _resolve_scope_path(raw: str) -> Path:
    """Resolve a scope-config path string to an absolute Path.

    Accepts absolute ("/data/workspaces/myworkspace") or DATA_ROOT-relative
    ("workspaces/myworkspace"). Both yield the same absolute Path.
    """
    p = Path(raw)
    if p.is_absolute():
        return p
    return (DATA_ROOT / raw).resolve()


def _load_fs_scope_from_bindings(workspace: str) -> FilesystemScope:
    data = load_bindings(workspace) or {}
    scope_cfg = (data.get("scope") or {}).get("filesystem")
    default = _conventional_fs_scope(workspace)
    if not scope_cfg:
        return default
    writable = scope_cfg.get("writable_roots")
    readable = scope_cfg.get("readable_roots")
    return FilesystemScope(
        workspace=workspace,
        writable_roots=[_resolve_scope_path(p) for p in writable]
            if writable else default.writable_roots,
        readable_roots=[_resolve_scope_path(p) for p in readable]
            if readable else default.readable_roots,
    )


async def get_filesystem_scope(
    ctx: Context,
    workspace_override: Optional[str] = None,
) -> Optional[FilesystemScope]:
    """Return the filesystem scope for a workspace, or None for no workspace."""
    ws = workspace_override or await get_active_workspace(ctx)
    if not ws:
        return None
    return _load_fs_scope_from_bindings(ws)


def _maybe_raise(err: ScopeViolationError) -> None:
    """Apply SCOPE_MODE: raise in enforce mode, log warning in advisory mode."""
    if SCOPE_MODE == "enforce":
        raise err
    logger.warning(
        f"[scope advisory] {err.user_message()} "
        f"(SCOPE_MODE=advisory — tool proceeded)"
    )


async def validate_fs_write(
    ctx: Context,
    path: str,
    workspace_override: Optional[str] = None,
) -> Path:
    """Validate a path for writing and return the resolved absolute Path.

    Resolution order:
      1. Absolute path → use as-is.
      2. Relative path + scope → resolve against scope.default_root().
      3. Relative path + no scope → resolve against DATA_ROOT (legacy).

    Scope violation behavior: SCOPE_MODE=enforce raises; advisory warns.
    DATA_ROOT sandbox (paths.validate) is always hard-enforced.
    """
    scope = await get_filesystem_scope(ctx, workspace_override)

    if path.startswith("/"):
        resolved = base_validate(path)
    elif scope is not None:
        resolved = base_validate(str(scope.default_root() / path))
    else:
        resolved = base_validate(path)

    if scope is not None:
        try:
            scope.validate_write(resolved)
        except ScopeViolationError as e:
            _maybe_raise(e)

    return resolved


async def validate_fs_read(
    ctx: Context,
    path: str,
    workspace_override: Optional[str] = None,
) -> Path:
    """Validate a path for reading. Same semantics as validate_fs_write."""
    scope = await get_filesystem_scope(ctx, workspace_override)

    if path.startswith("/"):
        resolved = base_validate(path)
    elif scope is not None:
        resolved = base_validate(str(scope.default_root() / path))
    else:
        resolved = base_validate(path)

    if scope is not None:
        try:
            scope.validate_read(resolved)
        except ScopeViolationError as e:
            _maybe_raise(e)

    return resolved


# ────────────────────────────────────────────────────────────────
# Datastore scope
# ────────────────────────────────────────────────────────────────


@dataclass
class DatastoreScope:
    """Per-workspace Store domain partition."""

    workspace: str
    default_domain: str
    allowed_domains: list[str] = field(default_factory=list)

    def resolve(self, explicit: str) -> str:
        """Empty → default_domain. Non-empty → validate against allowed_domains."""
        if not explicit:
            return self.default_domain
        if explicit not in self.allowed_domains:
            raise ScopeViolationError(
                workspace=self.workspace,
                resource_type="datastore",
                requested=explicit,
                allowed=list(self.allowed_domains),
                action="domain_access",
            )
        return explicit


def _conventional_ds_scope(workspace: str) -> DatastoreScope:
    return DatastoreScope(
        workspace=workspace,
        default_domain=workspace,
        allowed_domains=[workspace],
    )


def _load_ds_scope_from_bindings(workspace: str) -> DatastoreScope:
    data = load_bindings(workspace) or {}
    scope_cfg = (data.get("scope") or {}).get("datastore")
    default = _conventional_ds_scope(workspace)
    if not scope_cfg:
        return default
    return DatastoreScope(
        workspace=workspace,
        default_domain=scope_cfg.get("default_domain") or default.default_domain,
        allowed_domains=list(scope_cfg.get("allowed_domains") or default.allowed_domains),
    )


async def get_datastore_scope(
    ctx: Context,
    workspace_override: Optional[str] = None,
) -> Optional[DatastoreScope]:
    """Return the datastore scope for a workspace, or None for no workspace."""
    ws = workspace_override or await get_active_workspace(ctx)
    if not ws:
        return None
    return _load_ds_scope_from_bindings(ws)


async def resolve_domain(
    ctx: Context,
    explicit_domain: str,
    workspace_override: Optional[str] = None,
) -> str:
    """Resolve a Store domain, applying scope defaults and validation.

    Empty + active workspace → workspace's default_domain.
    Empty + no workspace    → "" (caller gets what they gave).
    Non-empty + allowed      → passes through.
    Non-empty + not allowed  → advisory warn, returns the explicit value anyway;
                               enforce mode raises.
    """
    scope = await get_datastore_scope(ctx, workspace_override)

    if scope is None:
        return explicit_domain

    try:
        return scope.resolve(explicit_domain)
    except ScopeViolationError as e:
        _maybe_raise(e)
        return explicit_domain or scope.default_domain


# ────────────────────────────────────────────────────────────────
# Description for bindings_show / portal Config page
# ────────────────────────────────────────────────────────────────


async def describe_scope(ctx: Context, workspace: Optional[str] = None) -> dict:
    """Return a human/portal-friendly view of a workspace's scope config.

    Works for any workspace, not just the session's active one — used by
    bindings_list (future Portal Config page) to describe every workspace.
    """
    ws = workspace or await get_active_workspace(ctx)
    result: dict = {
        "workspace": ws,
        "filesystem": None,
        "datastore": None,
    }
    if not ws:
        return result

    data = load_bindings(ws) or {}
    scope_cfg = data.get("scope") or {}

    fs_scope = _load_fs_scope_from_bindings(ws)
    result["filesystem"] = {
        "writable_roots": [str(p) for p in fs_scope.writable_roots],
        "readable_roots": [str(p) for p in fs_scope.readable_roots],
        "source": "declared" if "filesystem" in scope_cfg else "conventional",
    }

    ds_scope = _load_ds_scope_from_bindings(ws)
    result["datastore"] = {
        "default_domain": ds_scope.default_domain,
        "allowed_domains": list(ds_scope.allowed_domains),
        "source": "declared" if "datastore" in scope_cfg else "conventional",
    }

    return result
