# Clawith — Agent Instructions

Clawith is an open-source multi-agent collaboration platform built with **React 19 + TypeScript** (frontend) and **FastAPI + Python** (backend). This file gives coding agents everything they need to work effectively in this repository.

---

## Architecture Overview

```
frontend/   React 19 · Vite 6 · TypeScript 5 · Zustand · TanStack Query · React Router 7
backend/    FastAPI · SQLAlchemy 2 (async) · PostgreSQL/SQLite · Redis · Alembic · Pydantic 2
```

- Frontend dev server: **port 3008**, proxies `/api` and `/ws` to backend at **port 8008**
- Backend API prefix: `/api`
- Full architecture details: `ARCHITECTURE_SPEC_EN.md`

---

## Build, Lint & Test Commands

### Frontend (`cd frontend`)

```bash
npm run dev          # Start Vite dev server (port 3008)
npm run build        # tsc + vite build (production)
npm run preview      # Preview production build
```

TypeScript type-check only (no emit):
```bash
cd frontend && npx tsc --noEmit
```

### Backend (`cd backend`)

```bash
# Start dev server
.venv/bin/uvicorn app.main:app --reload --port 8008

# Run ALL tests
cd backend && .venv/bin/python -m pytest

# Run a SINGLE test file
cd backend && .venv/bin/python -m pytest tests/test_skills_api.py

# Run a SINGLE test by name
cd backend && .venv/bin/python -m pytest tests/test_skills_api.py::test_org_admin_can_delete_custom_skill_via_browse

# Lint with Ruff
cd backend && .venv/bin/ruff check app/
cd backend && .venv/bin/ruff format app/

# Database migrations
cd backend && .venv/bin/alembic upgrade head
cd backend && .venv/bin/alembic revision --autogenerate -m "description"
```

### Full-stack startup

```bash
bash restart.sh      # Starts both services
bash setup.sh --dev  # First-time dev setup (installs pytest + test tools)
```

---

## Project Structure

```
frontend/src/
├── components/      # Reusable UI components (ConfirmModal, FileBrowser, etc.)
├── pages/           # Route-level page components (AgentDetail, Dashboard, etc.)
├── services/api.ts  # Centralised API client — ALL fetch calls go here
├── stores/index.ts  # Zustand global stores (auth, app state)
├── types/index.ts   # Shared TypeScript interfaces
└── utils/           # Pure utility functions

backend/app/
├── api/             # FastAPI route handlers (37 modules)
├── models/          # SQLAlchemy ORM models
├── schemas/         # Pydantic request/response schemas
├── services/        # Business logic layer
├── core/            # Security, permissions, middleware
├── main.py          # FastAPI entry point, lifespan, router registration
└── config.py        # Settings (pydantic-settings)
```

---

## TypeScript / Frontend Conventions

### Strict TypeScript
- `strict: true` in `tsconfig.json`; never use `@ts-ignore` without a comment
- Import path alias: `@/` maps to `src/` (e.g. `import { agentApi } from '@/services/api'`)
- Keep shared types in `src/types/index.ts`; use string literal unions instead of enums:
  ```typescript
  status: 'creating' | 'running' | 'idle' | 'stopped' | 'error'
  ```

### Imports — ordering convention
```typescript
// 1. React / framework
import React, { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
// 2. Third-party libraries
import { IconSend } from '@tabler/icons-react';
// 3. Local services / stores
import { agentApi } from '../services/api';
import { useAuthStore } from '../stores';
// 4. Local components (default imports)
import ConfirmModal from '../components/ConfirmModal';
// 5. Types (import type)
import type { Agent } from '../types';
```

### React components
- **Always functional components** — no class components
- Props defined as a named `interface` immediately above the component
- Default exports for page/component files
- Early-return guards: `if (!open) return null;`
- Inline styles are acceptable (project uses a custom CSS variable design system — no Tailwind, no CSS modules)

```typescript
interface ConfirmModalProps {
    open: boolean;
    title: string;
    onConfirm: () => void;
    onCancel: () => void;
}

export default function ConfirmModal({ open, title, onConfirm, onCancel }: ConfirmModalProps) {
    if (!open) return null;
    return <div style={{ background: 'var(--bg-primary)' }}>...</div>;
}
```

### API calls — always go through `services/api.ts`
Never call `fetch` directly in components. Use the typed `request<T>` helper or the resource-grouped objects:
```typescript
// Reading data — use TanStack Query
const { data: agents } = useQuery({ queryKey: ['agents'], queryFn: () => agentApi.list() });

// Mutations
const mutation = useMutation({ mutationFn: (id: string) => agentApi.delete(id) });
```

Add new endpoints to the appropriate namespace in `api.ts` (e.g. `agentApi`, `taskApi`).

### State management
- **Zustand** for global state (`useAuthStore`, `useAppStore` in `stores/index.ts`)
- **TanStack Query** for server state (caching, background refetch)
- Local `useState` for purely local UI state

---

## Python / Backend Conventions

### Style
- **Ruff** is the linter and formatter — line length **120**, target Python 3.11
- Run `ruff check app/ --fix` before committing
- All new code must be **async** (`async def`, `await`, `AsyncSession`)
- Use `loguru` (`from loguru import logger`) — never `print()` in production code

### Naming
- Variables, functions, module names: `snake_case`
- Classes, Pydantic models, SQLAlchemy models: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`
- Private helpers: prefix with `_` (e.g. `_serialize_dt`)

### SQLAlchemy 2.0 patterns
```python
# Always use Mapped[] type annotations
class Agent(Base):
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="idle")

# Async queries
result = await db.execute(select(Agent).where(Agent.id == agent_id))
agent = result.scalar_one_or_none()
```

### FastAPI patterns
```python
router = APIRouter(prefix="/agents", tags=["agents"])

@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentOut:
    """One-line docstring describing the endpoint."""
    agent = await db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent
```

- Always add `response_model=` to route decorators
- Use `Depends(get_current_user)` for auth; `Depends(get_db)` for DB sessions
- Raise `HTTPException` with explicit `status_code` and `detail`
- Add a module-level docstring to every new `api/*.py` file

### Pydantic schemas
```python
class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    role_description: str
    primary_model_id: uuid.UUID | None = None   # Python 3.10+ union syntax
```

- All schemas live in `app/schemas/`
- Use `Field(...)` for validation constraints
- Use `str | None` (not `Optional[str]`) — Python 3.10+ style

### Error handling
- **Never silence exceptions silently** — log them with `logger.warning()` / `logger.error()`
- Startup steps use isolated try/except so one failure doesn't block others (see `main.py`)
- Background task failures must call the `_bg_task_error` done-callback pattern

---

## Testing (Backend)

Framework: **pytest + pytest-asyncio** (`asyncio_mode = "auto"`)

```python
import pytest
from types import SimpleNamespace

@pytest.fixture
def org_admin_user():
    return SimpleNamespace(id=uuid.uuid4(), role="org_admin", tenant_id=uuid.uuid4(), is_active=True)

@pytest.mark.asyncio
async def test_something(monkeypatch, org_admin_user):
    # Use monkeypatch to replace DB sessions, not real DB connections
    monkeypatch.setattr(module_under_test, "async_session", FakeAsyncSessionFactory(session))
    app.dependency_overrides[get_current_user] = lambda: org_admin_user
    # ...
    app.dependency_overrides.clear()  # always clean up
```

- Mock the database session with a `FakeSession` class — do **not** require a live DB in unit tests
- Use `app.dependency_overrides` to inject test users
- Always call `app.dependency_overrides.clear()` after the test
- Tests live in `backend/tests/`; name files `test_<feature>.py`

---

## Database Migrations

- **Never** use `create_all` in migrations — only in the startup idempotent check in `main.py`
- Generate migrations: `alembic revision --autogenerate -m "add column X to agents"`
- Always review the generated migration before applying
- See `ALEMBIC_GUIDELINES.md` for detailed rules

---

## Internationalisation (i18n)

- Frontend uses **react-i18next**
- Translation files: `frontend/src/i18n/en.json` and `zh.json`
- Always add keys to **both** files when adding UI text
- Use `const { t } = useTranslation();` in components; never hard-code display strings

---

## Key Files to Know

| File | Purpose |
|---|---|
| `backend/app/api/websocket.py` | LLM streaming loop, tool-calling, agent heartbeat — most complex file |
| `backend/app/api/gateway.py` | OpenClaw edge-node protocol |
| `backend/app/services/agent_tools.py` | Agent tool hub (sandbox, A2A, Feishu) |
| `backend/app/services/agent_context.py` | Assembles LLM context window |
| `frontend/src/pages/AgentDetail.tsx` | Main agent UI, WebSocket rendering, 5000+ lines |
| `frontend/src/services/api.ts` | All API calls — add new endpoints here |
| `frontend/src/index.css` | Design system (CSS variables, Linear dark theme) |
| `ARCHITECTURE_SPEC_EN.md` | Full architecture documentation |
