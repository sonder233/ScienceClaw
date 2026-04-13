# Windows Offline Document Runtime Plugin Design

## Background

The current Windows desktop package bundles Electron, embedded Python, backend, task-service, builtin skills, and Playwright/Chromium. In local mode, builtin document skills such as `docx`, `pdf`, `pptx`, and `xlsx` still fail after installation because they depend on both Python packages and external runtime tools that are currently only present in the sandbox image.

From [sandbox/Dockerfile](D:/code/MyScienceClaw/RpaClaw/sandbox/Dockerfile), the document-related runtime stack includes:

- Python packages for PDF and Office workflows
- LibreOffice (`soffice`)
- Pandoc
- Poppler utilities such as `pdftoppm`
- Tesseract OCR
- Node runtime
- npm modules `docx` and `pptxgenjs`

Bundling all of these into the main desktop installer would significantly increase package size. The goal is to keep the main installer lean while still supporting the full document skill set through an optional offline plugin package.

## Goals

- Keep the main Windows desktop installer small by bundling only Python dependencies that are required at runtime.
- Deliver the heavy document runtime as a separate offline plugin package.
- Allow users to install the plugin manually, fully offline, without modifying the main application installer.
- Install the plugin under a user-specified `RPA_CLAW_HOME`.
- Avoid any new plugin-specific UI, detection, or recovery logic in the desktop application.
- Make the installed runtime tools discoverable by the existing backend process through the existing `.env` loading path.

## Non-Goals

- Do not bundle LibreOffice, Pandoc, Poppler, Tesseract, or Node into the main installer.
- Do not add automatic download, auto-update, or online repair behavior.
- Do not add new frontend settings pages, installation wizards, or runtime status panels.
- Do not change the current user-facing behavior when the plugin is missing. Existing raw errors may continue to surface.
- Do not redesign the builtin skill workflows themselves beyond dependency delivery.

## Current Constraints

### Main desktop runtime

The desktop app already loads environment overrides from `<homeDir>/.env` and passes them into backend and task-service startup:

- [runtime.ts](D:/code/MyScienceClaw/electron-app/src/runtime.ts)
- [process-manager.ts](D:/code/MyScienceClaw/electron-app/src/process-manager.ts)

This means a plugin can integrate with the existing local-mode runtime without modifying Electron code, as long as it writes the correct environment variables into `<RPA_CLAW_HOME>/.env`.

### Builtin skill command assumptions

Multiple document-related scripts invoke tools by command name rather than by application-specific configuration:

- `soffice` in [soffice.py](D:/code/MyScienceClaw/RpaClaw/backend/builtin_skills/docx/scripts/office/soffice.py) and [recalc.py](D:/code/MyScienceClaw/RpaClaw/backend/builtin_skills/xlsx/scripts/recalc.py)
- `node` in the `docx` and `pptx` report generators
- `pdftoppm` and `tesseract` in PDF workflows

Because the desktop app must remain unchanged, the plugin installer must make these commands available through `PATH` and `NODE_PATH`.

### `.env` parsing behavior

The current `.env` loader in [runtime.ts](D:/code/MyScienceClaw/electron-app/src/runtime.ts) is a plain key-value parser. It does not expand shell placeholders such as `%PATH%`.

As a result, the installer cannot write:

```env
PATH=C:\foo;%PATH%
```

Instead, the installer must compute the full final `PATH` string at install time and persist that expanded value into `.env`.

## Decision

Use a separate offline Windows document runtime plugin package. The package contains:

- a preassembled `runtime-tools/` tree
- an `install.ps1` installer
- an `uninstall.ps1` uninstaller
- package metadata and documentation

The plugin is installed manually by the user into a user-specified `RPA_CLAW_HOME`. The installer writes runtime environment variables into `<RPA_CLAW_HOME>/.env`, which the existing desktop process manager already consumes.

The main desktop installer remains responsible only for Python-side dependencies and the existing desktop application runtime.

## Release Model

### Main installer

The main installer continues to ship:

- Electron desktop app
- embedded Python
- backend
- task-service
- builtin skills
- Playwright and Chromium
- document-related Python packages

The main installer does not ship:

- LibreOffice
- Pandoc
- Poppler
- Tesseract
- Node runtime
- npm packages `docx` and `pptxgenjs`

### Offline plugin package

The plugin package ships all heavy runtime tools required by local-mode document skills in one offline zip artifact.

Recommended artifact name:

```text
rpaclaw-document-runtime-win-x64.zip
```

## Package Layout

The offline plugin package should have this layout:

```text
rpaclaw-document-runtime-win-x64.zip
  install.ps1
  uninstall.ps1
  README.md
  runtime-tools/
    manifest.json
    libreoffice/
    pandoc/
    poppler/
    tesseract/
    node/
      node.exe
      node_modules/
        docx/
        pptxgenjs/
```

After installation, the target layout is:

```text
<RPA_CLAW_HOME>/
  .env
  runtime-tools/
    manifest.json
    libreoffice/
    pandoc/
    poppler/
    tesseract/
    node/
      node.exe
      node_modules/
        docx/
        pptxgenjs/
```

## Dependency Split

### Included in main installer

The main installer should explicitly bundle and lock Python packages required by builtin document skills, including at minimum:

- `reportlab`
- `pypdf`
- `pdfplumber`
- `pdf2image`
- `pytesseract`
- `openpyxl`
- `lxml`
- `defusedxml`
- `Pillow`

If any of these are currently only present through transitive dependencies, they should still be added explicitly to the packaging input to keep the desktop runtime stable.

### Included only in offline plugin

The offline plugin should contain:

- LibreOffice
- Pandoc
- Poppler
- Tesseract OCR
- Node runtime
- npm package `docx`
- npm package `pptxgenjs`

## Capability Mapping

### `docx`

Requires:

- main installer: XML and Python-side processing
- plugin: `node + docx`, `pandoc`, `LibreOffice`

Affected capabilities:

- structured report generation
- document conversion
- export to PDF
- tracked-change workflows that depend on LibreOffice

### `pptx`

Requires:

- main installer: XML and Python-side processing
- plugin: `node + pptxgenjs`, `LibreOffice`

Affected capabilities:

- report generation
- export to PDF

### `xlsx`

Requires:

- main installer: `openpyxl` and related Python stack
- plugin: `LibreOffice`

Affected capabilities:

- formula recalculation

### `pdf`

Requires:

- main installer: Python PDF and image libraries
- plugin: `Poppler`, `Tesseract`

Affected capabilities:

- PDF to image conversion
- OCR

## Installer Behavior

### User input

`install.ps1` must prompt the user to enter an installation root directory. That directory becomes `RPA_CLAW_HOME`.

The installer must not assume a default location and must not require a pre-existing `RPA_CLAW_HOME` environment variable.

### Installation steps

`install.ps1` should:

1. Prompt for `RPA_CLAW_HOME`.
2. Validate the path and create it if it does not exist.
3. If `<RPA_CLAW_HOME>/runtime-tools` already exists, rename it to a timestamped backup directory such as `runtime-tools.bak-YYYYMMDD-HHMMSS`.
4. Copy the bundled `runtime-tools/` tree into `<RPA_CLAW_HOME>/runtime-tools`.
5. Read the current effective `PATH` from the user environment or current process.
6. Build a final expanded `PATH` string by prepending plugin tool directories.
7. Write or update `<RPA_CLAW_HOME>/.env` with plugin-managed keys only.
8. Run a post-install smoke test and print success or failure.

### Uninstall steps

`uninstall.ps1` should:

1. Prompt for `RPA_CLAW_HOME`.
2. Remove `<RPA_CLAW_HOME>/runtime-tools`.
3. Remove only plugin-managed keys from `<RPA_CLAW_HOME>/.env`.
4. Leave all unrelated `.env` keys untouched.
5. Leave any timestamped backups untouched for manual rollback unless the user explicitly deletes them.

### Upgrade behavior

Reinstalling a newer plugin version should follow the same path as install:

- backup old `runtime-tools`
- copy the new version
- rewrite plugin-managed environment variables

No in-place partial patching is required.

## Environment Injection

The plugin installer should manage the following keys in `<RPA_CLAW_HOME>/.env`:

- `RPA_CLAW_HOME`
- `NODE_PATH`
- `TESSDATA_PREFIX`
- `PATH`

Recommended values:

```env
RPA_CLAW_HOME=<user-selected-path>
NODE_PATH=<RPA_CLAW_HOME>\runtime-tools\node\node_modules
TESSDATA_PREFIX=<RPA_CLAW_HOME>\runtime-tools\tesseract\tessdata
PATH=<RPA_CLAW_HOME>\runtime-tools\libreoffice\program;<RPA_CLAW_HOME>\runtime-tools\pandoc;<RPA_CLAW_HOME>\runtime-tools\poppler\bin;<RPA_CLAW_HOME>\runtime-tools\tesseract;<RPA_CLAW_HOME>\runtime-tools\node;<expanded-existing-path>
```

The installer must persist an already-expanded `PATH` string and must not rely on `%PATH%` placeholder expansion.

To avoid damaging unrelated configuration, the installer should only replace keys that it owns. A clear comment header may be added around plugin-managed entries.

## Manifest Format

`runtime-tools/manifest.json` should be included for versioning, diagnostics, and build reproducibility.

Suggested shape:

```json
{
  "schema_version": 1,
  "package_version": "2026.04.14",
  "components": {
    "libreoffice": "x.y.z",
    "pandoc": "x.y.z",
    "poppler": "x.y.z",
    "tesseract": "x.y.z",
    "node": "x.y.z",
    "docx": "x.y.z",
    "pptxgenjs": "x.y.z"
  },
  "paths": {
    "soffice": "runtime-tools/libreoffice/program/soffice.exe",
    "pandoc": "runtime-tools/pandoc/pandoc.exe",
    "pdftoppm": "runtime-tools/poppler/bin/pdftoppm.exe",
    "tesseract": "runtime-tools/tesseract/tesseract.exe",
    "node": "runtime-tools/node/node.exe"
  },
  "sha256": {
    "soffice.exe": "<sha256>",
    "pandoc.exe": "<sha256>",
    "pdftoppm.exe": "<sha256>",
    "tesseract.exe": "<sha256>",
    "node.exe": "<sha256>"
  }
}
```

The application does not need to consume this file immediately. It exists primarily for build traceability, installer diagnostics, and future compatibility checks.

## Build Pipeline

Add a separate Windows build script for the offline plugin package, for example:

```text
build-windows-document-runtime.ps1
```

Its responsibilities should be:

1. Fetch or collect pinned runtime binaries for LibreOffice, Pandoc, Poppler, Tesseract, and Node.
2. Normalize them into the expected `runtime-tools/` directory structure.
3. Install `docx` and `pptxgenjs` into `runtime-tools/node/node_modules`.
4. Generate `manifest.json`.
5. Generate the release zip containing `runtime-tools/`, `install.ps1`, `uninstall.ps1`, and `README.md`.

The plugin package build must be independent from the main desktop installer build.

## Validation Strategy

### Installer smoke tests

`install.ps1` should verify:

- `soffice --version`
- `pandoc --version`
- `pdftoppm -v`
- `tesseract --version`
- `node -v`
- `node -e "require('docx'); require('pptxgenjs')"`

This is enough to catch broken file layouts and missing binaries.

### Build-time integration tests

Before publishing the offline plugin, the build pipeline should validate a representative end-to-end workflow for each affected skill:

- `docx` report generation
- `pptx` report generation
- `xlsx` formula recalculation
- `pdf` to image conversion
- `pdf` OCR
- `docx` or `pptx` export to PDF

### Main installer validation

The main desktop package should also be verified in a plugin-absent environment to ensure:

- core desktop app still starts normally
- local backend still starts normally
- document skills still fail in the same raw manner as today when the plugin is missing

## Risks And Tradeoffs

- The offline plugin zip will still be relatively large, mainly because of LibreOffice.
- This shifts the size cost from every desktop user to only those users who need document workflows.
- Build and release management becomes slightly more complex because the document runtime is now a separate artifact.
- The approach is still preferable because it preserves a small main installer and avoids invasive changes to the application.

## Conclusion

Adopt a two-artifact Windows desktop delivery model:

- main desktop installer with Python-side document dependencies only
- separate offline document runtime plugin zip with `runtime-tools/` plus PowerShell install and uninstall scripts

The plugin installs into a user-specified `RPA_CLAW_HOME`, integrates only through `<RPA_CLAW_HOME>/.env`, and requires no application-side plugin management changes. This provides full local-mode document capability while keeping the main installer lean.
