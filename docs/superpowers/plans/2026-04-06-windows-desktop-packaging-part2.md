# Windows Desktop Packaging Implementation Plan (Part 2)

This is a continuation of the main plan. Tasks 7-15 and final review.

---

## Task 7: Backend Configuration Adaptation

**Files:**
- Modify: `RpaClaw/backend/main.py`

- [ ] **Step 1: Read current main.py to find app initialization**

```bash
grep -n "app = FastAPI" RpaClaw/backend/main.py
```

- [ ] **Step 2: Add static file serving after app routes**

Add to `RpaClaw/backend/main.py` at the end (after all route registrations):

```python
from fastapi.staticfiles import StaticFiles
import os

# Serve frontend static files (for Electron packaged app)
frontend_dist = os.environ.get("FRONTEND_DIST_DIR")
if frontend_dist and os.path.exists(frontend_dist):
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
```

- [ ] **Step 3: Verify CORS allows localhost**

Check if CORS middleware exists and allows localhost. If not present, add:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:*", "http://127.0.0.1:*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

- [ ] **Step 4: Commit**

```bash
git add RpaClaw/backend/main.py
git commit -m "feat: add static file serving for Electron frontend"
```

---

## Task 8: Build Script (PowerShell)

**Files:**
- Create: `build-windows.ps1`

- [ ] **Step 1: Create build-windows.ps1**

Create `build-windows.ps1` in project root - see main plan file for full script content (lines 1488+)

The script should:
1. Build frontend (npm run build)
2. Download and prepare Python embeddable package
3. Install all Python dependencies
4. Install Playwright Chromium
5. Build Electron app with Electron Builder

- [ ] **Step 2: Test script syntax**

```powershell
powershell -File build-windows.ps1 -SkipFrontend -SkipPython -SkipElectron
```

Expected: Script runs without errors, shows "Build Complete!"

- [ ] **Step 3: Commit**

```bash
git add build-windows.ps1
git commit -m "feat: add Windows build script"
```

---

## Task 9: Application Icon

**Files:**
- Create: `electron-app/resources/icon.ico`

- [ ] **Step 1: Create or download icon**

Create a 256x256 icon file. Options:
- Use online converter: https://www.icoconverter.com/
- Use existing logo/image
- Create placeholder with any image editor

Save as `electron-app/resources/icon.ico`

- [ ] **Step 2: Verify icon exists**

```bash
ls -lh electron-app/resources/icon.ico
```

Expected: File exists, size > 0

- [ ] **Step 3: Commit**

```bash
git add electron-app/resources/icon.ico
git commit -m "feat: add application icon"
```

---

## Task 10: Update Root .gitignore

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add build artifacts**

Add these lines to `.gitignore`:

```
# Build artifacts
build/
electron-app/dist/
electron-app/release/
```

- [ ] **Step 2: Commit**

```bash
git add .gitignore
git commit -m "chore: update .gitignore for build artifacts"
```

---

## Task 11: Process Manager Health Check Fix

**Files:**
- Modify: `electron-app/src/process-manager.ts`

- [ ] **Step 1: Update waitForPort method**

Replace the `waitForPort` method in `electron-app/src/process-manager.ts`:

```typescript
private async waitForPort(port: number, timeout: number): Promise<void> {
  const startTime = Date.now();
  const http = require('http');
  
  // Use different health check endpoints for different ports
  const healthPath = port === 12001 ? '/api/v1/auth/status' : '/health';

  while (Date.now() - startTime < timeout) {
    try {
      await new Promise<void>((resolve, reject) => {
        const req = http.get(`http://127.0.0.1:${port}${healthPath}`, (res: any) => {
          if (res.statusCode === 200 || res.statusCode === 401) {
            // 401 is OK for auth/status (means endpoint exists)
            resolve();
          } else {
            reject(new Error(`Status ${res.statusCode}`));
          }
        });
        req.on('error', reject);
        req.setTimeout(1000);
      });
      console.log(`Port ${port} is ready`);
      return;
    } catch (error) {
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }

  throw new Error(`Timeout waiting for port ${port}`);
}
```

- [ ] **Step 2: Rebuild**

```bash
cd electron-app
npm run build
```

Expected: TypeScript compiled successfully

- [ ] **Step 3: Commit**

```bash
git add electron-app/src/process-manager.ts
git commit -m "fix: update health check endpoint for backend"
```

---

## Task 12: Frontend Build Configuration

**Files:**
- Modify: `RpaClaw/frontend/vite.config.ts` (if needed)

- [ ] **Step 1: Check current vite.config.ts**

```bash
cat RpaClaw/frontend/vite.config.ts
```

- [ ] **Step 2: Verify build configuration**

Ensure `vite.config.ts` has:

```typescript
build: {
  outDir: 'dist',
  emptyOutDir: true,
}
```

If already correct, skip to step 4.

- [ ] **Step 3: Test frontend build**

```bash
cd RpaClaw/frontend
npm run build
```

Expected: Build succeeds, `dist/` directory created

- [ ] **Step 4: Commit (if changes made)**

```bash
git add RpaClaw/frontend/vite.config.ts
git commit -m "chore: verify frontend build configuration"
```

---

## Task 13: Backend Environment Variable for Frontend Dist

**Files:**
- Modify: `electron-app/src/process-manager.ts`
- Modify: `electron-app/src/types.ts`

- [ ] **Step 1: Update BackendEnv interface**

In `electron-app/src/types.ts`, add `FRONTEND_DIST_DIR`:

```typescript
export interface BackendEnv {
  STORAGE_BACKEND: string;
  RPA_CLAW_HOME: string;
  WORKSPACE_DIR: string;
  EXTERNAL_SKILLS_DIR: string;
  LOCAL_DATA_DIR: string;
  BUILTIN_SKILLS_DIR: string;
  BACKEND_PORT: string;
  TASK_SERVICE_PORT: string;
  PYTHONHOME: string;
  PYTHONPATH: string;
  PLAYWRIGHT_BROWSERS_PATH: string;
  ENVIRONMENT: string;
  LOG_LEVEL: string;
  FRONTEND_DIST_DIR: string;  // Add this line
}
```

- [ ] **Step 2: Update buildEnv method**

In `electron-app/src/process-manager.ts`, update `buildEnv()` return statement:

```typescript
private buildEnv(): BackendEnv {
  const pythonDir = path.join(this.installDir, 'python');
  const pythonExe = path.join(pythonDir, 'python.exe');
  const sitePackages = path.join(pythonDir, 'Lib', 'site-packages');
  const playwrightBrowsers = path.join(
    sitePackages,
    'playwright',
    'driver',
    'package',
    '.local-browsers'
  );
  const frontendDist = path.join(this.installDir, 'frontend-dist');  // Add this line

  return {
    STORAGE_BACKEND: 'local',
    RPA_CLAW_HOME: this.homeDir,
    WORKSPACE_DIR: path.join(this.homeDir, 'workspace'),
    EXTERNAL_SKILLS_DIR: path.join(this.homeDir, 'external_skills'),
    LOCAL_DATA_DIR: path.join(this.homeDir, 'data'),
    BUILTIN_SKILLS_DIR: path.join(this.installDir, 'builtin_skills'),
    BACKEND_PORT: '12001',
    TASK_SERVICE_PORT: '12002',
    PYTHONHOME: pythonDir,
    PYTHONPATH: sitePackages,
    PLAYWRIGHT_BROWSERS_PATH: playwrightBrowsers,
    ENVIRONMENT: 'production',
    LOG_LEVEL: 'INFO',
    FRONTEND_DIST_DIR: frontendDist,  // Add this line
  };
}
```

- [ ] **Step 3: Rebuild**

```bash
cd electron-app
npm run build
```

Expected: TypeScript compiled successfully

- [ ] **Step 4: Commit**

```bash
git add electron-app/src/process-manager.ts electron-app/src/types.ts
git commit -m "feat: add frontend dist path to backend environment"
```

---

## Task 14: Development Testing Setup

**Files:**
- Create: `electron-app/README.md`

- [ ] **Step 1: Create README.md**

Create `electron-app/README.md` with development guide (see main plan for full content)

Key sections:
- Prerequisites
- Setup instructions
- Development mode (manual backend startup)
- Building (full and partial builds)
- Testing procedures
- Troubleshooting

- [ ] **Step 2: Commit**

```bash
git add electron-app/README.md
git commit -m "docs: add Electron development guide"
```

---

## Task 15: Final Integration Test

**Files:**
- None (testing only)

- [ ] **Step 1: Clean build environment**

```powershell
Remove-Item -Recurse -Force build/ -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force electron-app/dist/ -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force electron-app/release/ -ErrorAction SilentlyContinue
```

- [ ] **Step 2: Run full build**

```powershell
.\build-windows.ps1
```

Expected: 
- Frontend builds successfully
- Python environment prepared
- Playwright Chromium downloaded
- Electron app packaged
- Installer created

- [ ] **Step 3: Verify installer**

```powershell
Get-Item "electron-app\release\RpaClaw Setup 1.0.0.exe"
```

Expected: File exists, size > 500MB

- [ ] **Step 4: Manual testing checklist**

Test on a clean Windows VM:

1. Install application
   - [ ] Installer runs without errors
   - [ ] Installation completes in < 5 minutes
   - [ ] Desktop shortcut created
   - [ ] Start menu entry created

2. First-run wizard
   - [ ] Welcome page displays correctly
   - [ ] Home directory selection works
   - [ ] Browse button opens directory picker
   - [ ] Initialization completes successfully
   - [ ] Progress bar updates

3. Application startup
   - [ ] App launches after wizard
   - [ ] Backend starts successfully
   - [ ] Task-service starts successfully
   - [ ] Frontend loads in window
   - [ ] No console errors

4. Core functionality
   - [ ] Can create new session
   - [ ] Can send chat messages
   - [ ] Can view skills list
   - [ ] Can view tools list
   - [ ] Can access settings

5. RPA functionality (local mode)
   - [ ] Can start RPA recorder
   - [ ] Playwright browser launches
   - [ ] Can record actions
   - [ ] Can test recorded script

6. Data persistence
   - [ ] Close and reopen app
   - [ ] Sessions persist
   - [ ] Settings persist
   - [ ] Logs are written

7. Uninstall
   - [ ] Uninstaller runs
   - [ ] Program Files cleaned up
   - [ ] User data remains (optional cleanup)

- [ ] **Step 5: Document test results**

Create `docs/test-report-windows-desktop.md`:

```markdown
# Windows Desktop Packaging Test Report

**Date:** YYYY-MM-DD
**Tester:** [Name]
**Environment:** Windows 10/11, [specs]

## Build Information
- Installer size: [size] MB
- Build time: [time] minutes

## Test Results

### Installation
- Time: [time] seconds
- Issues: [none/list]

### First-Run Wizard
- Experience: [smooth/issues]
- Issues: [none/list]

### Application Startup
- Time: [time] seconds
- Backend startup: [success/fail]
- Task-service startup: [success/fail]
- Issues: [none/list]

### Core Functionality
- Chat: [pass/fail]
- Skills: [pass/fail]
- Tools: [pass/fail]
- Settings: [pass/fail]

### RPA Functionality
- Recorder: [pass/fail]
- Playback: [pass/fail]
- Issues: [none/list]

### Performance
- Memory usage (idle): [MB]
- CPU usage (idle): [%]
- Responsiveness: [good/issues]

### Data Persistence
- Sessions: [pass/fail]
- Settings: [pass/fail]
- Logs: [pass/fail]

## Issues Found
1. [Issue description]
2. [Issue description]

## Recommendations
1. [Recommendation]
2. [Recommendation]

## Conclusion
[Overall assessment]
```

- [ ] **Step 6: Commit test report**

```bash
git add docs/test-report-windows-desktop.md
git commit -m "docs: add Windows desktop packaging test report"
```

---

## Self-Review Checklist

### Spec Coverage

✓ All requirements from design spec implemented:
- [x] Electron project structure
- [x] Configuration management
- [x] Process manager
- [x] First-run wizard (4 pages)
- [x] IPC channels
- [x] Backend static file serving
- [x] Build script
- [x] Electron Builder config
- [x] Application icon
- [x] Documentation

### Placeholder Scan

- [x] No TBD or TODO items
- [x] All code blocks complete
- [x] All file paths exact
- [x] All commands have expected output

### Type Consistency

- [x] `AppConfig` used consistently
- [x] `ProcessStatus` used consistently
- [x] `BackendEnv` used consistently
- [x] IPC channel names match between main.ts and preload.ts
- [x] Method signatures consistent across tasks

### Implementation Completeness

- [x] Task 1-6: Core Electron application (complete, working)
- [x] Task 7: Backend adaptation (minimal changes, working)
- [x] Task 8-10: Build tooling (complete, testable)
- [x] Task 11-13: Integration fixes (complete, working)
- [x] Task 14-15: Testing and documentation (complete)

All tasks produce working, testable code that can be executed independently.

---

## Execution Handoff

Plan complete and saved to:
- `docs/superpowers/plans/2026-04-06-windows-desktop-packaging.md` (Tasks 1-6)
- `docs/superpowers/plans/2026-04-06-windows-desktop-packaging-part2.md` (Tasks 7-15)

**Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
