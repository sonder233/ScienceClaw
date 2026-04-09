# RPA Configure Page Density Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the RPA configure page so it shows compact step summaries by default, uses a sticky right-side configuration rail, and moves script preview into a drawer to reduce scrolling.

**Architecture:** Keep the existing Recorder V2 data flow and backend APIs unchanged. Implement the redesign in `ConfigurePage.vue` by introducing local presentation state for step expansion and script drawer visibility, compressing step cards into summary/detail states, and moving skill info plus parameters into a persistent right rail inspired by `.claude/stepconfig.html`.

**Tech Stack:** Vue 3, TypeScript, Vite, Tailwind CSS, existing `apiClient`

---

## File Map

### Modified

- `RpaClaw/frontend/src/pages/rpa/ConfigurePage.vue`

### New Docs

- `docs/superpowers/specs/2026-04-09-rpa-configure-page-density-design.md`

## Verification Constraints

This frontend package does not currently include a dedicated unit test runner such as Vitest or Cypress. Because of that, implementation verification for this task should use:

- targeted local logic checks inside the page implementation
- `npm run type-check` as a repository-wide signal, with the known caveat that unrelated pre-existing failures already exist outside the RPA pages
- manual visual verification in the configure page

The engineer must explicitly distinguish:

- regressions introduced by this task
- unrelated pre-existing repo-wide frontend type issues

## Task 1: Refactor Configure Page State For Compact/Expanded Rendering

**Files:**
- Modify: `RpaClaw/frontend/src/pages/rpa/ConfigurePage.vue`

- [ ] **Step 1: Identify the state needed for the redesign**

Add local UI state requirements to the script section:

- `expandedStepIndex`
- `isScriptDrawerOpen`

Preserve existing state:

- `steps`
- `params`
- `skillName`
- `skillDescription`
- `generatedScript`
- `promotingStepIndex`

- [ ] **Step 2: Add helper functions for summary rendering**

Add or refactor helper functions in `ConfigurePage.vue` to support compact step rows:

- step action label
- step badge color
- short locator summary
- short frame summary
- short value summary
- candidate count summary

The helpers must keep technical locator formatting intact while returning short display strings.

- [ ] **Step 3: Add expand/collapse behavior**

Implement a toggle function so:

- clicking a summary row expands that step
- clicking the same row collapses it
- expanding a new row automatically collapses the previously open row

- [ ] **Step 4: Verify no existing behavior is removed in script logic**

Re-check these functions in the same file and keep them intact:

- `loadSession`
- `loadCredentials`
- `promoteLocator`
- `generateScript`
- `goToTest`

Expected result:

- the redesign only changes presentation and local UI state, not API behavior

## Task 2: Replace The Single-Column Page With A Two-Column Workspace

**Files:**
- Modify: `RpaClaw/frontend/src/pages/rpa/ConfigurePage.vue`

- [ ] **Step 1: Rewrite the page shell**

Change the template layout to:

- compact top toolbar
- two-column desktop content area
- single-column mobile fallback

Desktop structure:

- left column: skill info card + step list
- right column: sticky configuration rail

- [ ] **Step 2: Move skill info into a compact card**

Replace the current full-width skill info block with a denser card that keeps:

- skill name input
- skill description input

The card should sit at the top of the left work area or top of the right rail, depending on final spacing balance, but it must no longer consume large vertical space.

- [ ] **Step 3: Build the sticky right rail**

Move parameter editing into a right-side card with desktop `sticky` behavior.

Requirements:

- the rail remains visible while the left column scrolls
- long parameter lists can scroll inside the parameter card if necessary
- mobile view collapses the rail below the step list

- [ ] **Step 4: Keep existing parameter behavior unchanged**

Parameter rows must still support:

- enable toggle
- parameter name editing
- plain default value input
- credential selection for sensitive params

## Task 3: Redesign Step Cards Into Summary-First Rows

**Files:**
- Modify: `RpaClaw/frontend/src/pages/rpa/ConfigurePage.vue`

- [ ] **Step 1: Replace always-expanded step cards with compact summaries**

In collapsed state, each step row must show:

- index
- action badge
- primary description
- compact locator summary
- validation badge
- optional value preview
- frame hint when not in main frame

The row should be materially shorter than the current implementation.

- [ ] **Step 2: Add expanded diagnostics section**

When a step is expanded, render:

- `主定位器`
- `框架层级`
- `校验结果`
- `候选定位器`

All operator-facing labels should be Chinese.

- [ ] **Step 3: Compress locator candidate rendering**

Rules:

- no candidate list in collapsed state
- only render full candidates in expanded state
- each candidate row stays compact and one-line-first

Each expanded candidate row should include:

- `kind`
- `score`
- `strict`
- `当前使用` badge when selected
- compact locator text
- reason text when present
- `使用此定位器` action when not selected

- [ ] **Step 4: Preserve locator promotion behavior**

The expanded candidate action must still call the existing `promoteLocator(idx, candidateIndex)`.

Expected result:

- users can still switch locators without page redesign changing backend behavior

## Task 4: Move Script Preview Into A Drawer

**Files:**
- Modify: `RpaClaw/frontend/src/pages/rpa/ConfigurePage.vue`

- [ ] **Step 1: Replace inline script preview block with drawer state**

Change the current bottom script preview section so the preview is not rendered inline in the main page flow.

Instead:

- `预览脚本` triggers script generation
- successful generation opens a right-side drawer

- [ ] **Step 2: Implement the drawer container**

The drawer must:

- open from the right
- overlay the page content
- show a close button
- display generated script in a scrollable code block

- [ ] **Step 3: Keep generation error handling intact**

If script generation fails:

- drawer should not open with stale content
- existing page error handling should still show the failure

## Task 5: Verify The Redesign

**Files:**
- Modify: `RpaClaw/frontend/src/pages/rpa/ConfigurePage.vue`

- [ ] **Step 1: Run repository type check and capture current status**

Run:

```bash
npm run type-check
```

Working directory:

```bash
RpaClaw/frontend
```

Expected:

- command may still fail because of pre-existing unrelated repo-wide frontend issues
- there must be no newly introduced ConfigurePage-specific type errors

- [ ] **Step 2: Manual desktop verification**

Open the configure page and verify:

- the page uses a two-column desktop layout
- the right parameter rail stays visible while scrolling
- collapsed steps are substantially shorter than before
- only one step expands at a time
- expanded details use Chinese labels such as `主定位器` and `校验结果`
- locator promotion still works
- script preview opens in a drawer instead of pushing content downward

- [ ] **Step 3: Manual mobile-width verification**

Verify responsive behavior:

- the page collapses to a single column
- parameter card moves below the step list
- expanded step content does not overflow horizontally
- drawer remains usable on smaller widths

- [ ] **Step 4: Check for regressions in main flows**

Confirm these flows still work:

- editing skill name/description
- enabling/disabling parameters
- editing parameter names and values
- selecting credentials for sensitive params
- previewing generated script
- navigating to `/rpa/test`

- [ ] **Step 5: Commit**

Stage only the configure-page redesign files and commit with a focused message, for example:

```bash
git add RpaClaw/frontend/src/pages/rpa/ConfigurePage.vue docs/superpowers/specs/2026-04-09-rpa-configure-page-density-design.md docs/superpowers/plans/2026-04-09-rpa-configure-page-density-implementation.md
git commit -m "feat: compact rpa configure page layout"
```
