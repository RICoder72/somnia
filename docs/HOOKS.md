# Hooks — Service Abstraction & Account Resolution

Somnia uses a **hook system** to abstract service integrations. When Claude
sends a message, reads email, or checks a calendar, it doesn't care which
provider is behind it. The hook system resolves an abstract request to a
concrete account and adapter.

## Core Concepts

**Service type** — an abstract capability: `mail`, `calendar`, `contacts`,
`storage`, `notify`. Each service type defines an interface that all
adapters for that type must implement.

**Adapter** — a concrete implementation of a service type. `gmail` and
`outlook` are both adapters for the `mail` service. `signal`, `twilio`,
and `email` are adapters for `notify`.

**Account** — a named, configured instance of an adapter. `zannim@bsd-ri.net`
is a mail account using the `gmail` adapter. You can have multiple accounts
of the same adapter type (work Gmail, personal Gmail).

**Binding** — a workspace's declaration of which account to use for a
service type. The `burrillville` workspace binds `mail` to
`zannim@bsd-ri.net`.

## File Layout

```
config/
  hooks_registry.yaml      # All named accounts, grouped by service type
  global_bindings.yaml      # System-level default accounts

workspaces/{name}/
  bindings.yaml             # Per-workspace account bindings

vigil/
  core/
    registry.py             # Registry loader
    bindings.py             # Three-tier resolver
    binding_helpers.py      # resolve_or_error() convenience wrapper
  services/
    mail/
      interface.py          # MailAdapter abstract base class
      manager.py            # Account lifecycle & convenience methods
      adapters/
        gmail.py            # Gmail adapter
        outlook.py          # Outlook/M365 adapter
      tools.py              # MCP tool definitions
    calendar/               # Same pattern
    contacts/               # Same pattern
    storage/                # Same pattern
    notifications/          # Same pattern
    supernote/              # Same pattern (no CLI, Python-only)
```

## Resolution Chain

When a tool call needs an account and none was passed explicitly,
the resolver runs a three-tier chain:

```
1. Workspace binding
   → Active workspace's bindings.yaml has an identity entry for this service?
   → If the value is "global", fall through to tier 2.
   → Otherwise, use the bound account name.

2. Global default
   → config/global_bindings.yaml has a default for this service?
   → Use it.

3. Error
   → MissingBindingError with a list of available accounts from the registry.
   → Claude surfaces this in chat and asks the user which account to use.
```

Explicit `account=` on a tool call **always wins** — it bypasses the
resolver entirely. This is the escape hatch for one-off operations
outside the normal workspace context.

### Workspace Bindings

```yaml
# workspaces/burrillville/bindings.yaml
version: 1
identity:
  mail: zannim@bsd-ri.net       # Use this specific account
  calendar: zannim@bsd-ri.net
  contacts: zannim@bsd-ri.net
  storage: personal
  notify: global                # Defer to global default
```

Special values:
- **`global`** — explicitly defer to the global default for this service
- **absent** — same effect as `global` (falls through silently)

Fallback syntax for multiple allowed accounts:

```yaml
identity:
  mail:
    primary: zannim@bsd-ri.net
    fallbacks:
      - somnia.zanni@gmail.com
```

Fallbacks are **not** automatic failover. Primary is always used unless
the caller passes `account=` explicitly with a fallback name.

### Global Bindings

```yaml
# config/global_bindings.yaml
version: 1
defaults:
  mail: zannim@bsd-ri.net
  calendar: zannim@bsd-ri.net
  contacts: zannim@bsd-ri.net
  storage: personal
  notify: signal-matt
```

These apply when no workspace is active, or when the workspace defers.

## Hooks Registry

All accounts live in `config/hooks_registry.yaml`, grouped by service type:

```yaml
version: 1
accounts:
  mail:
    zannim@bsd-ri.net:
      adapter: gmail
      credentials_ref: ""
      config:
        token_path: /data/tokens/gmail_token.json
      display: "BIT Work Gmail"

    somnia.zanni@gmail.com:
      adapter: gmail
      credentials_ref: ""
      config:
        token_path: /data/tokens/gmail_token_somnia.json
      display: "Somnia Gmail"

  notify:
    signal-matt:
      adapter: signal
      credentials_ref: ""
      config:
        api_url: http://signal-api:8080
        sender: "+14013378064"
      display: "Signal"
```

Account names must be unique **within** their service type (not globally).
This matches how resolution works — the resolver always knows the service
type and account name.

### Fields

| Field | Required | Description |
|-------|----------|-------------|
| `adapter` | yes | Which adapter implementation to use |
| `credentials_ref` | no | Reference to credentials in a secrets backend |
| `config` | no | Adapter-specific configuration (token paths, API URLs, etc.) |
| `display` | no | Human-readable label Claude shows in chat |

## Writing a New Adapter

Every service type follows the same pattern: `interface.py` defines the
abstract base, adapters implement it, the manager handles lifecycle.

### 1. Define the interface (if new service type)

```python
# vigil/services/myservice/interface.py
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class MyServiceAccount:
    """Account configuration."""
    name: str
    adapter: str
    credentials_ref: str
    config: dict

class MyServiceAdapter(ABC):
    """Base class for myservice adapters."""
    adapter_type: str = "base"

    def __init__(self, account: MyServiceAccount):
        self.account = account

    @abstractmethod
    async def connect(self) -> bool: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def do_thing(self, ...) -> Result: ...
```

### 2. Implement the adapter

```python
# vigil/services/myservice/adapters/myprovider.py
from ..interface import MyServiceAdapter, MyServiceAccount

class MyProviderAdapter(MyServiceAdapter):
    adapter_type = "myprovider"

    async def connect(self) -> bool:
        # Initialize client using self.account.config
        return True

    async def disconnect(self) -> None:
        pass

    async def do_thing(self, ...) -> Result:
        # Implementation
        ...
```

### 3. Write the manager

```python
# vigil/services/myservice/manager.py
# Follow the pattern in mail/manager.py — load accounts from
# config, register adapter classes, resolve by name.
```

### 4. Write the MCP tools

```python
# vigil/services/myservice/tools.py
from core.binding_helpers import resolve_or_error

@mcp.tool()
async def myservice_do_thing(ctx: Context, account: str = "", ...) -> str:
    account, err = await resolve_or_error(ctx, account, "myservice")
    if err:
        return err
    # ... use account with manager ...
```

### 5. Register the account

Add to `config/hooks_registry.yaml`:

```yaml
accounts:
  myservice:
    my-account:
      adapter: myprovider
      credentials_ref: secrets:myservice/creds
      config:
        api_url: https://...
      display: "My Provider"
```

### 6. Set global default (optional)

Add to `config/global_bindings.yaml`:

```yaml
defaults:
  myservice: my-account
```

### 7. Bind to workspaces (optional)

Add to any workspace's `bindings.yaml`:

```yaml
identity:
  myservice: my-account
```

## Credentials

Adapters need credentials. The `credentials_ref` field in the registry
points to a secrets backend. The reference format is `backend:path`:

- `secrets:mail/gmail/oauth` — Vigil's postgres-backed secrets store
- `op://Vault/Item` — 1Password CLI reference
- `""` (empty) — adapter handles its own credential loading via `config`
  (e.g., token file paths)

The shared secrets module (`shared/secrets/`) provides a three-tier
fallback: environment variables → file-based secrets → 1Password CLI.
New adapters should use this rather than inventing credential handling.

## Notification Routing

The `notify` service has additional routing logic. Notifications are
dispatched by priority, not by explicit account selection:

```yaml
# In hooks_registry.yaml
notify_routing:
  urgent: [signal, twilio, email]   # Try adapters in order
  high:   [signal, twilio, email]
  normal: [signal, email]
  low:    [email]

notify_default_recipient:
  address: "+14014814468"
  name: "Matt"
```

The routing table uses **adapter types** (not account names) to determine
the cascade. The notification manager finds accounts matching each adapter
type and dispatches through the first that succeeds.
