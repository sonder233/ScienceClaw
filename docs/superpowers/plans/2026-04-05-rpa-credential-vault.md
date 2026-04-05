# RPA 密码脱敏与凭据保险箱 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect password fields during RPA recording, mask them in the UI, store credentials securely with AES-256-GCM encryption, and inject them at execution time without exposing plaintext in scripts.

**Architecture:** A new `credential` backend module provides encrypted CRUD storage (local JSON file or MongoDB, selected by `STORAGE_BACKEND`). The browser-side capture script detects `input[type="password"]` and replaces the value with a `{{credential}}` placeholder. The ConfigurePage lets users bind sensitive parameters to stored credentials. At execution time, the backend decrypts credentials and injects them into `kwargs` before calling `execute_skill()`.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, `cryptography` (Fernet/AES-GCM), Vue 3 + TypeScript, Tailwind CSS, Reka UI

---

### Task 1: Add `CREDENTIAL_KEY` to backend config

**Files:**
- Modify: `ScienceClaw/backend/config.py`

- [ ] **Step 1: Add the credential_key setting**

In `ScienceClaw/backend/config.py`, add after the existing settings (around line 50, near other env vars):

```python
credential_key: str = os.environ.get("CREDENTIAL_KEY", "")
```

- [ ] **Step 2: Commit**

```bash
git add ScienceClaw/backend/config.py
git commit -m "feat(credential): add CREDENTIAL_KEY config setting"
```

---

### Task 2: Create credential vault module

**Files:**
- Create: `ScienceClaw/backend/credential/__init__.py`
- Create: `ScienceClaw/backend/credential/models.py`
- Create: `ScienceClaw/backend/credential/vault.py`

- [ ] **Step 1: Create `__init__.py`**

```python
"""Credential vault — encrypted credential storage."""
```

- [ ] **Step 2: Create `models.py`**

```python
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
import uuid


class Credential(BaseModel):
    id: str = Field(default_factory=lambda: f"cred_{uuid.uuid4().hex[:12]}")
    name: str
    username: str = ""
    encrypted_password: str = ""
    domain: str = ""
    user_id: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class CredentialCreate(BaseModel):
    name: str
    username: str = ""
    password: str  # plaintext, will be encrypted before storage
    domain: str = ""


class CredentialUpdate(BaseModel):
    name: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None  # plaintext, empty means no change
    domain: Optional[str] = None


class CredentialResponse(BaseModel):
    """Response model — never includes password."""
    id: str
    name: str
    username: str
    domain: str
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 3: Create `vault.py`**

```python
"""AES-256-GCM encrypted credential storage."""
from __future__ import annotations

import base64
import json
import logging
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.config import settings
from backend.credential.models import (
    Credential,
    CredentialCreate,
    CredentialResponse,
    CredentialUpdate,
)

logger = logging.getLogger(__name__)

_NONCE_SIZE = 12  # 96-bit nonce for AES-GCM


class CredentialVault:
    """Encrypt/decrypt credentials. Storage via get_repository('credentials')."""

    def __init__(self):
        key_hex = settings.credential_key
        if not key_hex:
            key_hex = self._generate_and_save_key()
        self._key = bytes.fromhex(key_hex)
        if len(self._key) != 32:
            raise ValueError("CREDENTIAL_KEY must be 64 hex chars (32 bytes)")
        self._aesgcm = AESGCM(self._key)

    @staticmethod
    def _generate_and_save_key() -> str:
        """Generate a random 256-bit key and append to .env."""
        key_hex = secrets.token_hex(32)
        env_path = Path(".env")
        try:
            with open(env_path, "a") as f:
                f.write(f"\nCREDENTIAL_KEY={key_hex}\n")
            logger.info("Generated new CREDENTIAL_KEY and saved to .env")
        except OSError:
            logger.warning("Could not write CREDENTIAL_KEY to .env — set it manually")
        # Also set in current process
        os.environ["CREDENTIAL_KEY"] = key_hex
        return key_hex

    def encrypt(self, plaintext: str) -> str:
        """Encrypt plaintext → base64(nonce + ciphertext)."""
        nonce = secrets.token_bytes(_NONCE_SIZE)
        ct = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        return base64.b64encode(nonce + ct).decode("ascii")

    def decrypt(self, encrypted: str) -> str:
        """Decrypt base64(nonce + ciphertext) → plaintext."""
        raw = base64.b64decode(encrypted)
        nonce = raw[:_NONCE_SIZE]
        ct = raw[_NONCE_SIZE:]
        return self._aesgcm.decrypt(nonce, ct, None).decode("utf-8")

    async def create(self, user_id: str, data: CredentialCreate) -> CredentialResponse:
        from backend.storage import get_repository
        repo = get_repository("credentials")

        cred = Credential(
            name=data.name,
            username=data.username,
            encrypted_password=self.encrypt(data.password),
            domain=data.domain,
            user_id=user_id,
        )
        doc = cred.model_dump()
        doc["_id"] = doc.pop("id")
        await repo.insert_one(doc)
        return CredentialResponse(**{**doc, "id": doc["_id"]})

    async def list_for_user(self, user_id: str) -> List[CredentialResponse]:
        from backend.storage import get_repository
        repo = get_repository("credentials")

        docs = await repo.find_many(
            {"user_id": user_id},
            sort=[("created_at", -1)],
        )
        return [
            CredentialResponse(**{**d, "id": d["_id"]})
            for d in docs
        ]

    async def update(
        self, user_id: str, cred_id: str, data: CredentialUpdate
    ) -> Optional[CredentialResponse]:
        from backend.storage import get_repository
        repo = get_repository("credentials")

        existing = await repo.find_one({"_id": cred_id, "user_id": user_id})
        if not existing:
            return None

        updates: dict = {"updated_at": datetime.now()}
        if data.name is not None:
            updates["name"] = data.name
        if data.username is not None:
            updates["username"] = data.username
        if data.password:
            updates["encrypted_password"] = self.encrypt(data.password)
        if data.domain is not None:
            updates["domain"] = data.domain

        await repo.update_one({"_id": cred_id}, {"$set": updates})
        doc = await repo.find_one({"_id": cred_id})
        return CredentialResponse(**{**doc, "id": doc["_id"]}) if doc else None

    async def delete(self, user_id: str, cred_id: str) -> bool:
        from backend.storage import get_repository
        repo = get_repository("credentials")
        count = await repo.delete_one({"_id": cred_id, "user_id": user_id})
        return count > 0

    async def decrypt_credential(self, user_id: str, cred_id: str) -> Optional[str]:
        """Decrypt and return the plaintext password. Internal use only."""
        from backend.storage import get_repository
        repo = get_repository("credentials")
        doc = await repo.find_one({"_id": cred_id, "user_id": user_id})
        if not doc:
            return None
        return self.decrypt(doc["encrypted_password"])


# Module-level singleton
_vault: Optional[CredentialVault] = None


def get_vault() -> CredentialVault:
    global _vault
    if _vault is None:
        _vault = CredentialVault()
    return _vault
```

- [ ] **Step 4: Commit**

```bash
git add ScienceClaw/backend/credential/
git commit -m "feat(credential): add vault module with AES-256-GCM encryption"
```

---

### Task 3: Create credential REST API

**Files:**
- Create: `ScienceClaw/backend/route/credential.py`
- Modify: `ScienceClaw/backend/main.py`

- [ ] **Step 1: Create `route/credential.py`**

```python
"""Credential management API."""
import logging
from fastapi import APIRouter, Depends, HTTPException

from backend.auth import get_current_user, User
from backend.credential.models import CredentialCreate, CredentialUpdate
from backend.credential.vault import get_vault

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/credentials", tags=["credentials"])


@router.get("")
async def list_credentials(current_user: User = Depends(get_current_user)):
    vault = get_vault()
    creds = await vault.list_for_user(str(current_user.id))
    return {"status": "success", "credentials": [c.model_dump() for c in creds]}


@router.post("")
async def create_credential(
    body: CredentialCreate,
    current_user: User = Depends(get_current_user),
):
    vault = get_vault()
    cred = await vault.create(str(current_user.id), body)
    return {"status": "success", "credential": cred.model_dump()}


@router.put("/{cred_id}")
async def update_credential(
    cred_id: str,
    body: CredentialUpdate,
    current_user: User = Depends(get_current_user),
):
    vault = get_vault()
    cred = await vault.update(str(current_user.id), cred_id, body)
    if not cred:
        raise HTTPException(status_code=404, detail="Credential not found")
    return {"status": "success", "credential": cred.model_dump()}


@router.delete("/{cred_id}")
async def delete_credential(
    cred_id: str,
    current_user: User = Depends(get_current_user),
):
    vault = get_vault()
    deleted = await vault.delete(str(current_user.id), cred_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Credential not found")
    return {"status": "success"}
```

- [ ] **Step 2: Register the router in `main.py`**

In `ScienceClaw/backend/main.py`, add the import alongside other router imports:

```python
from backend.route.credential import router as credential_router
```

Add the registration alongside other `app.include_router` calls (around line 154):

```python
app.include_router(credential_router, prefix="/api/v1")
```

- [ ] **Step 3: Register `credentials` collection in storage init**

In `ScienceClaw/backend/storage/__init__.py`, add `"credentials"` to the list of collections in `init_storage()` (line 42):

```python
for name in (
    "users", "user_sessions", "sessions", "models",
    "skills", "blocked_tools", "task_settings", "session_events",
    "session_runtimes", "credentials",
):
```

- [ ] **Step 4: Commit**

```bash
git add ScienceClaw/backend/route/credential.py ScienceClaw/backend/main.py ScienceClaw/backend/storage/__init__.py
git commit -m "feat(credential): add CRUD REST API and register router"
```

---

### Task 4: Add `sensitive` field to RPAStep and detect password fields in CAPTURE_JS

**Files:**
- Modify: `ScienceClaw/backend/rpa/manager.py`

- [ ] **Step 1: Add `sensitive` field to RPAStep model**

In `ScienceClaw/backend/rpa/manager.py`, add `sensitive` to the `RPAStep` class (after line 30, after `source`):

```python
class RPAStep(BaseModel):
    id: str
    action: str
    target: Optional[str] = None
    value: Optional[str] = None
    screenshot_url: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)
    description: Optional[str] = None
    tag: Optional[str] = None
    label: Optional[str] = None
    url: Optional[str] = None
    source: str = "record"  # "record" or "ai"
    prompt: Optional[str] = None
    sensitive: bool = False
```

- [ ] **Step 2: Modify CAPTURE_JS fill handler to detect password fields**

In the `document.addEventListener('input', ...)` handler (around line 456), change:

```javascript
    document.addEventListener('input', function(e) {
        if (!e.isTrusted) return;
        if (window.__rpa_paused) return;
        var el = e.target;
        clearTimeout(el.__rpa_timer);
        el.__rpa_timer = setTimeout(function() {
            emit({action:'fill', locator:generateLocator(el),
                  value:el.value||'', tag:el.tagName});
        }, 800);
    }, true);
```

To:

```javascript
    document.addEventListener('input', function(e) {
        if (!e.isTrusted) return;
        if (window.__rpa_paused) return;
        var el = e.target;
        clearTimeout(el.__rpa_timer);
        el.__rpa_timer = setTimeout(function() {
            var isPassword = (el.type === 'password');
            emit({action:'fill', locator:generateLocator(el),
                  value: isPassword ? '{{credential}}' : (el.value||''),
                  tag:el.tagName,
                  sensitive: isPassword});
        }, 800);
    }, true);
```

- [ ] **Step 3: Update `_handle_event` to pass `sensitive` flag with defensive validation**

In `_handle_event` (around line 624), add `sensitive` to `step_data` and enforce value masking:

```python
        is_sensitive = evt.get("sensitive", False)
        step_data = {
            "action": evt.get("action", "unknown"),
            "target": json.dumps(locator_info) if locator_info else "",
            "value": "{{credential}}" if is_sensitive else evt.get("value", ""),
            "label": "",
            "tag": evt.get("tag", ""),
            "url": evt.get("url", ""),
            "description": self._make_description(evt),
            "sensitive": is_sensitive,
        }
```

- [ ] **Step 4: Mask password in `_make_description`**

In `_make_description` (around line 659), change the fill description:

```python
        if action == "fill":
            display_value = '*****' if evt.get("sensitive") else f'"{value}"'
            return f'输入 {display_value} 到 {target}'
```

- [ ] **Step 5: Commit**

```bash
git add ScienceClaw/backend/rpa/manager.py
git commit -m "feat(rpa): detect password fields and add sensitive flag to RPAStep"
```

---

### Task 5: Update generator to handle sensitive parameters

**Files:**
- Modify: `ScienceClaw/backend/rpa/generator.py`

- [ ] **Step 1: Update `_maybe_parameterize` for sensitive params**

In `ScienceClaw/backend/rpa/generator.py`, change `_maybe_parameterize` (line 312):

```python
    def _maybe_parameterize(self, value: str, params: Dict[str, Any]) -> str:
        """Check if value should be a parameter."""
        for param_name, param_info in params.items():
            if param_info.get("original_value") == value:
                if param_info.get("sensitive"):
                    # No default value for sensitive params
                    return f"kwargs['{param_name}']"
                return f"kwargs.get('{param_name}', '{value}')"
        safe = value.replace("'", "\\'")
        return f"'{safe}'"
```

- [ ] **Step 2: Commit**

```bash
git add ScienceClaw/backend/rpa/generator.py
git commit -m "feat(rpa): sensitive params use kwargs[] without default value"
```

---

### Task 6: Inject credentials at execution time

**Files:**
- Modify: `ScienceClaw/backend/credential/vault.py`
- Modify: `ScienceClaw/backend/route/rpa.py`

- [ ] **Step 1: Add credential injection helper**

Add to `ScienceClaw/backend/credential/vault.py` at the bottom, before the `_vault` singleton:

```python
async def inject_credentials(user_id: str, params: dict, kwargs: dict) -> dict:
    """Resolve credential_id references in params and inject into kwargs.

    Args:
        user_id: The user who owns the credentials.
        params: Parameter config dict, e.g. {"password": {"sensitive": true, "credential_id": "cred_xxx"}}.
        kwargs: The kwargs dict that will be passed to execute_skill.

    Returns:
        Updated kwargs with decrypted credential values injected.
    """
    vault = get_vault()
    result = dict(kwargs)
    for param_name, param_info in params.items():
        if not isinstance(param_info, dict):
            continue
        cred_id = param_info.get("credential_id")
        if not cred_id:
            continue
        plaintext = await vault.decrypt_credential(user_id, cred_id)
        if plaintext is not None:
            result[param_name] = plaintext
    return result
```

- [ ] **Step 2: Inject credentials in `route/rpa.py` test endpoint**

In `ScienceClaw/backend/route/rpa.py`, add import at top:

```python
from backend.credential.vault import inject_credentials
```

In the local mode execution block (around line 363), change:

```python
                _skill_result = await asyncio.wait_for(namespace["execute_skill"](page), timeout=RPA_TEST_TIMEOUT_S)
```

To:

```python
                # Inject credentials into kwargs for sensitive params
                test_kwargs = {}
                if request.params:
                    test_kwargs = await inject_credentials(
                        str(current_user.id), request.params, {}
                    )
                _skill_result = await asyncio.wait_for(
                    namespace["execute_skill"](page, **test_kwargs),
                    timeout=RPA_TEST_TIMEOUT_S,
                )
```

- [ ] **Step 3: Commit**

```bash
git add ScienceClaw/backend/credential/vault.py ScienceClaw/backend/route/rpa.py
git commit -m "feat(credential): inject decrypted credentials at execution time"
```

---

### Task 7: Frontend — mask sensitive steps in RecorderPage

**Files:**
- Modify: `ScienceClaw/frontend/src/pages/rpa/RecorderPage.vue`

- [ ] **Step 1: Pass sensitive flag through in step mapping**

In `ScienceClaw/frontend/src/pages/rpa/RecorderPage.vue`, in the `startPollingSteps` function (around line 104), update the step mapping to include `sensitive`:

```typescript
          ...serverSteps.map((s: any, i: number) => ({
            id: String(i + 1),
            title: s.description || s.action,
            description: s.source === 'ai' ? (s.prompt || s.description || 'AI 操作') : `${s.action} → ${s.target || s.label || ''}`,
            status: 'completed',
            source: s.source || 'record',
            sensitive: s.sensitive || false,
          }))
```

Apply the same `sensitive: s.sensitive || false` addition to the `deleteStep` function's step mapping (around line 279) and the `agent_step_done` handler (around line 382).

- [ ] **Step 2: Commit**

```bash
git add ScienceClaw/frontend/src/pages/rpa/RecorderPage.vue
git commit -m "feat(rpa): pass sensitive flag through to frontend step display"
```

---

### Task 8: Frontend — mask sensitive values in ConfigurePage

**Files:**
- Modify: `ScienceClaw/frontend/src/pages/rpa/ConfigurePage.vue`

- [ ] **Step 1: Detect sensitive params in auto-extraction**

In `ScienceClaw/frontend/src/pages/rpa/ConfigurePage.vue`, in the `loadSession` function (around line 35), update the parameter extraction to include `sensitive` and `credential_id`:

```typescript
    params.value = steps.value
      .filter((s: any) => s.action === 'fill' || s.action === 'select')
      .map((s: any, i: number) => {
        let label = `参数${i + 1}`;
        try {
          const loc = typeof s.target === 'string' ? JSON.parse(s.target) : s.target;
          if (loc?.name) label = loc.name;
          else if (loc?.value) label = loc.value;
        } catch { /* use default */ }
        return {
          id: `param_${i}`,
          name: `param_${i}`,
          label,
          original_value: s.value || '',
          current_value: s.value || '',
          enabled: true,
          step_id: s.id,
          sensitive: s.sensitive || false,
          credential_id: '',
        };
      });
```

- [ ] **Step 2: Mask sensitive step values in the steps list**

In the template (around line 209), change:

```vue
            <span v-if="step.value" class="text-xs text-gray-400 font-mono truncate max-w-[200px]">
              "{{ step.value }}"
            </span>
```

To:

```vue
            <span v-if="step.value" class="text-xs text-gray-400 font-mono truncate max-w-[200px]">
              "{{ step.sensitive ? '*****' : step.value }}"
            </span>
```

- [ ] **Step 3: Replace input with credential selector for sensitive params**

In the parameters section template (around line 245), replace the `<input>` for param values:

```vue
            <template v-if="param.sensitive">
              <select
                v-model="param.credential_id"
                class="text-sm text-gray-600 border border-gray-200 rounded px-2 py-1 w-48"
              >
                <option value="">选择凭据...</option>
                <option
                  v-for="cred in credentials"
                  :key="cred.id"
                  :value="cred.id"
                >
                  {{ cred.name }} ({{ cred.username }})
                </option>
              </select>
            </template>
            <template v-else>
              <input
                v-model="param.current_value"
                class="text-sm text-gray-600 border border-gray-200 rounded px-2 py-1 w-48"
                placeholder="默认值"
              />
            </template>
```

- [ ] **Step 4: Load credentials list and include credential_id in params**

Add to the `<script setup>` section:

```typescript
const credentials = ref<any[]>([]);

const loadCredentials = async () => {
  try {
    const resp = await apiClient.get('/credentials');
    credentials.value = resp.data.credentials || [];
  } catch {
    // Silently fail — credentials feature is optional
  }
};
```

Call `loadCredentials()` inside `onMounted`:

```typescript
onMounted(() => {
  loadSession();
  loadCredentials();
});
```

Update `generateScript` and `goToTest` to include `sensitive` and `credential_id` in the param map (around line 78):

```typescript
const generateScript = async () => {
  try {
    const paramMap: Record<string, any> = {};
    params.value.filter(p => p.enabled).forEach(p => {
      paramMap[p.name] = {
        original_value: p.original_value,
        sensitive: p.sensitive || false,
        credential_id: p.credential_id || '',
      };
    });
    // ... rest unchanged
```

Apply the same change to `goToTest` (around line 94).

- [ ] **Step 5: Commit**

```bash
git add ScienceClaw/frontend/src/pages/rpa/ConfigurePage.vue
git commit -m "feat(rpa): mask sensitive values and add credential selector in ConfigurePage"
```

---

### Task 9: Frontend — create credential API client

**Files:**
- Create: `ScienceClaw/frontend/src/api/credential.ts`

- [ ] **Step 1: Create the API client**

```typescript
import { apiClient } from './client';

export interface Credential {
  id: string;
  name: string;
  username: string;
  domain: string;
  created_at: string;
  updated_at: string;
}

export interface CredentialCreate {
  name: string;
  username: string;
  password: string;
  domain?: string;
}

export interface CredentialUpdate {
  name?: string;
  username?: string;
  password?: string;
  domain?: string;
}

export async function listCredentials(): Promise<Credential[]> {
  const resp = await apiClient.get('/credentials');
  return resp.data.credentials;
}

export async function createCredential(data: CredentialCreate): Promise<Credential> {
  const resp = await apiClient.post('/credentials', data);
  return resp.data.credential;
}

export async function updateCredential(id: string, data: CredentialUpdate): Promise<Credential> {
  const resp = await apiClient.put(`/credentials/${id}`, data);
  return resp.data.credential;
}

export async function deleteCredential(id: string): Promise<void> {
  await apiClient.delete(`/credentials/${id}`);
}
```

- [ ] **Step 2: Commit**

```bash
git add ScienceClaw/frontend/src/api/credential.ts
git commit -m "feat(credential): add frontend API client"
```

---

### Task 10: Frontend — create CredentialsPage

**Files:**
- Create: `ScienceClaw/frontend/src/pages/CredentialsPage.vue`
- Modify: `ScienceClaw/frontend/src/main.ts`

- [ ] **Step 1: Create `CredentialsPage.vue`**

```vue
<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { Shield, Plus, Trash2, Edit3, X } from 'lucide-vue-next';
import {
  listCredentials,
  createCredential,
  updateCredential,
  deleteCredential,
  type Credential,
  type CredentialCreate,
} from '@/api/credential';

const credentials = ref<Credential[]>([]);
const loading = ref(true);
const showForm = ref(false);
const editingId = ref<string | null>(null);

const form = ref<CredentialCreate>({
  name: '',
  username: '',
  password: '',
  domain: '',
});

const resetForm = () => {
  form.value = { name: '', username: '', password: '', domain: '' };
  editingId.value = null;
  showForm.value = false;
};

const load = async () => {
  loading.value = true;
  try {
    credentials.value = await listCredentials();
  } finally {
    loading.value = false;
  }
};

const save = async () => {
  if (!form.value.name) return;
  if (editingId.value) {
    await updateCredential(editingId.value, {
      name: form.value.name,
      username: form.value.username,
      password: form.value.password || undefined,
      domain: form.value.domain,
    });
  } else {
    if (!form.value.password) return;
    await createCredential(form.value);
  }
  resetForm();
  await load();
};

const startEdit = (cred: Credential) => {
  editingId.value = cred.id;
  form.value = {
    name: cred.name,
    username: cred.username,
    password: '',
    domain: cred.domain,
  };
  showForm.value = true;
};

const remove = async (id: string) => {
  if (!confirm('确定删除此凭据？')) return;
  await deleteCredential(id);
  await load();
};

onMounted(load);
</script>

<template>
  <div class="min-h-screen bg-[#f5f6f7]">
    <header class="h-16 bg-white border-b border-gray-200 flex items-center px-8 gap-4">
      <Shield class="text-[#831bd7]" :size="24" />
      <h1 class="text-gray-900 font-extrabold text-xl">凭据管理</h1>
      <div class="flex-1"></div>
      <button
        @click="showForm = true; editingId = null; form = { name: '', username: '', password: '', domain: '' }"
        class="flex items-center gap-2 bg-[#831bd7] text-white px-4 py-2 rounded-lg text-sm font-bold hover:bg-[#7018b8]"
      >
        <Plus :size="16" />
        新增凭据
      </button>
    </header>

    <div class="max-w-4xl mx-auto p-8 space-y-6">
      <!-- Form -->
      <div v-if="showForm" class="bg-white rounded-xl p-6 shadow-sm border border-gray-200">
        <div class="flex justify-between items-center mb-4">
          <h2 class="font-bold text-lg">{{ editingId ? '编辑凭据' : '新增凭据' }}</h2>
          <button @click="resetForm" class="p-1 hover:bg-gray-100 rounded"><X :size="18" /></button>
        </div>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="text-xs text-gray-500 font-medium mb-1 block">名称</label>
            <input v-model="form.name" class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-[#831bd7] outline-none" placeholder="如：GitHub 登录" />
          </div>
          <div>
            <label class="text-xs text-gray-500 font-medium mb-1 block">用户名</label>
            <input v-model="form.username" class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-[#831bd7] outline-none" placeholder="用户名或邮箱" />
          </div>
          <div>
            <label class="text-xs text-gray-500 font-medium mb-1 block">密码</label>
            <input v-model="form.password" type="password" class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-[#831bd7] outline-none" :placeholder="editingId ? '留空则不修改' : '密码'" />
          </div>
          <div>
            <label class="text-xs text-gray-500 font-medium mb-1 block">域名（可选）</label>
            <input v-model="form.domain" class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-[#831bd7] outline-none" placeholder="如：github.com" />
          </div>
        </div>
        <div class="flex justify-end mt-4">
          <button @click="save" class="bg-[#831bd7] text-white px-6 py-2 rounded-lg text-sm font-bold hover:bg-[#7018b8]">
            {{ editingId ? '保存' : '创建' }}
          </button>
        </div>
      </div>

      <!-- List -->
      <div class="bg-white rounded-xl p-6 shadow-sm border border-gray-200">
        <div v-if="loading" class="text-center text-gray-400 py-8">加载中...</div>
        <div v-else-if="credentials.length === 0" class="text-center text-gray-400 py-8">
          暂无凭据，点击上方按钮新增
        </div>
        <div v-else class="space-y-3">
          <div
            v-for="cred in credentials"
            :key="cred.id"
            class="flex items-center gap-4 p-4 bg-gray-50 rounded-lg"
          >
            <Shield class="text-[#831bd7] flex-shrink-0" :size="18" />
            <div class="flex-1 min-w-0">
              <p class="text-sm font-semibold text-gray-900">{{ cred.name }}</p>
              <p class="text-xs text-gray-500">{{ cred.username }} {{ cred.domain ? `· ${cred.domain}` : '' }}</p>
            </div>
            <span class="text-xs text-gray-400 font-mono">*****</span>
            <button @click="startEdit(cred)" class="p-1.5 hover:bg-gray-200 rounded" title="编辑">
              <Edit3 :size="14" class="text-gray-500" />
            </button>
            <button @click="remove(cred.id)" class="p-1.5 hover:bg-red-50 rounded" title="删除">
              <Trash2 :size="14" class="text-red-500" />
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
```

- [ ] **Step 2: Add route in `main.ts`**

In `ScienceClaw/frontend/src/main.ts`, add the import:

```typescript
import CredentialsPage from './pages/CredentialsPage.vue';
```

Add the route inside the routes array, as a sibling to the `/rpa` route (around line 70):

```typescript
    {
      path: '/credentials',
      component: CredentialsPage,
      meta: { requiresAuth: true },
    },
```

- [ ] **Step 3: Commit**

```bash
git add ScienceClaw/frontend/src/pages/CredentialsPage.vue ScienceClaw/frontend/src/main.ts
git commit -m "feat(credential): add CredentialsPage with CRUD UI"
```

---

### Task 11: Add i18n strings

**Files:**
- Modify: `ScienceClaw/frontend/src/locales/en.ts`
- Modify: `ScienceClaw/frontend/src/locales/zh.ts`

- [ ] **Step 1: Add English strings**

In `ScienceClaw/frontend/src/locales/en.ts`, add:

```typescript
  'Credentials': 'Credentials',
  'Credential Management': 'Credential Management',
  'New Credential': 'New Credential',
  'Edit Credential': 'Edit Credential',
  'Credential Name': 'Credential Name',
  'Username': 'Username',
  'Password': 'Password',
  'Domain': 'Domain (optional)',
  'Select credential...': 'Select credential...',
  'No credentials yet': 'No credentials yet. Click above to add one.',
  'Delete credential confirm': 'Are you sure you want to delete this credential?',
  'Leave empty to keep': 'Leave empty to keep current password',
```

- [ ] **Step 2: Add Chinese strings**

In `ScienceClaw/frontend/src/locales/zh.ts`, add:

```typescript
  'Credentials': '凭据',
  'Credential Management': '凭据管理',
  'New Credential': '新增凭据',
  'Edit Credential': '编辑凭据',
  'Credential Name': '凭据名称',
  'Username': '用户名',
  'Password': '密码',
  'Domain': '域名（可选）',
  'Select credential...': '选择凭据...',
  'No credentials yet': '暂无凭据，点击上方按钮新增',
  'Delete credential confirm': '确定删除此凭据？',
  'Leave empty to keep': '留空则不修改',
```

- [ ] **Step 3: Commit**

```bash
git add ScienceClaw/frontend/src/locales/en.ts ScienceClaw/frontend/src/locales/zh.ts
git commit -m "feat(credential): add i18n strings for credential management"
```

---

### Task 12: Install `cryptography` dependency

**Files:**
- Modify: `ScienceClaw/backend/pyproject.toml` (or `requirements.txt`)

- [ ] **Step 1: Check which dependency file exists**

```bash
ls ScienceClaw/backend/pyproject.toml ScienceClaw/backend/requirements.txt 2>/dev/null
```

- [ ] **Step 2: Add `cryptography` to dependencies**

If `pyproject.toml`:
```toml
# Add to [project.dependencies] or [tool.uv.dependencies]
"cryptography>=43.0",
```

If `requirements.txt`:
```
cryptography>=43.0
```

- [ ] **Step 3: Commit**

```bash
git add ScienceClaw/backend/pyproject.toml  # or requirements.txt
git commit -m "chore: add cryptography dependency for credential vault"
```
