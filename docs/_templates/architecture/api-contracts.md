---
type: architecture
status: draft
last_updated: YYYY-MM-DD
mvp: true
verifiable_by:
  - src/<package>/*/infrastructure/dbus/*_interface.py
  - "busctl --user introspect dev.meetingscribe.Service /dev/meetingscribe/Service"
ai_instructions:
  - "This doc is the SOURCE OF TRUTH for D-Bus contracts. If a method, property or signal changes, update THIS file in the same commit."
  - "No client or server work starts without this doc updated."
  - "If this doc and the introspected interface drift, this doc wins and the code is fixed."
---

# API Contracts

**Bus**: session bus. **Name**: `dev.meetingscribe.Service`. **Object path**: `/dev/meetingscribe/Service`
**Auth**: none beyond the per-user session bus (any process of the same user can call it).
**Errors**: D-Bus errors named `dev.meetingscribe.Error.<ERROR_CODE>` with a message (see `error-codes-catalog.md`).

---

## Conventions

| Topic | Rule |
|---|---|
| Method and signal names | `PascalCase` (D-Bus convention) |
| IDs | string (`s`) |
| Timestamps | ISO 8601 with timezone (`s`) |
| Durations | seconds (`d`) |
| Errors | `dev.meetingscribe.Error.<ERROR_CODE>` |

---

## Interfaces

### `dev.meetingscribe.<Name>1`

| Member | Kind | Signature | Description | Errors |
|---|---|---|---|---|
| `Method` | method | `(s) -> s` | ... | `<ERROR_CODE>` when ... |
| `Property` | property | `s` (read) | ... | — |
| `Signal` | signal | `(s)` | ... | — |

Example call: `gdbus call --session --dest dev.meetingscribe.Service --object-path /dev/meetingscribe/Service --method dev.meetingscribe.<Name>1.Method "arg"`

---

## Out-of-scope members (documented for visibility)

| Member | Status | Reason |
|---|---|---|
| `...` | next release | ... |

---

## How to add a new method, property or signal

1. Update this doc FIRST with the expected request/response.
2. Write the failing test (RED).
3. Define the interface adapter in the domain's `infrastructure/dbus/` (`ServiceInterface` subclass).
4. Implement the member; call the application controller and map its `Result` to a return value or a D-Bus error.
5. Add the new error codes to `error-codes-catalog.md`.
