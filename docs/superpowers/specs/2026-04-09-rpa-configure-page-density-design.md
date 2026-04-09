# RPA Configure Page Density Optimization Design

## Summary

The current RPA configure page shows too much per-step diagnostic content inline, which forces users to scroll through long locator blocks before they can finish basic skill configuration.

This redesign keeps the existing Recorder V2 data model and functionality, but changes the page into a denser operator-oriented workspace inspired by `.claude/stepconfig.html`.

The redesign focuses on:

- compact step summaries by default
- on-demand expansion for step diagnostics
- a sticky right-side configuration rail for skill info and parameters
- script preview moved out of the main scroll flow into a drawer
- Chinese labels for operator-facing diagnostic fields

## Goals

- Reduce vertical scrolling on the configure page significantly
- Keep locator diagnostics available without showing them for every step at once
- Make parameters editable without forcing the user to leave the steps context
- Preserve existing Recorder V2 capabilities:
  - locator promotion
  - frame display
  - validation display
  - generated script preview

## Non-Goals

- Changing Recorder V2 backend APIs or step payloads
- Reworking the test page or recorder page layout
- Removing locator diagnostics
- Replacing the existing color system or page-level navigation

## Reference Direction

The target visual direction should follow `.claude/stepconfig.html` in spirit:

- compact top toolbar
- concise information cards
- stronger hierarchy between summary and detail
- fewer always-visible technical blocks
- clear actions at the top of the page

This is a layout and density reference, not a requirement to copy markup literally.

## Page Structure

### Desktop Layout

The page becomes a two-column workspace:

- left main column:
  - page title and actions
  - compact skill info card
  - step list
- right sticky rail:
  - skill info summary or editor
  - parameter configuration card

The right rail should remain visible while the left step list scrolls.

### Mobile Layout

On smaller screens, the page collapses into a single column:

- top toolbar
- skill info
- step list
- parameters

The sticky rail behavior is desktop-only.

## Toolbar

The header should remain compact and action-oriented.

Required actions:

- `预览脚本`
- `开始测试`

Behavior:

- `预览脚本` opens a drawer or side panel instead of inserting the script inline into the page
- `开始测试` preserves current behavior

## Step List Design

### Default State

Each step is rendered as a compact summary card.

Visible in collapsed state:

- step index
- action badge
- main description
- short locator summary
- validation badge
- optional value summary
- optional frame hint if not in main frame

The collapsed card should fit within roughly two lines of content in the common case.

### Expanded State

Only one step should be expanded at a time.

When expanded, the card shows:

- `主定位器`
- `框架层级`
- `校验结果`
- locator candidate list

These labels should be in Chinese where they are operator-facing.

Technical locator payloads themselves may remain in their current code-like form.

### Candidate Density Rules

To reduce page height:

- collapsed state does not render the full locator candidate list
- collapsed state may show a compact summary such as:
  - active locator kind
  - active score
  - number of alternatives
- full locator candidates render only inside the expanded card

Each candidate row in expanded state should remain compact:

- kind
- score
- strict count
- selected marker
- one-line locator summary
- optional reason
- `使用此定位器` action when not selected

## Skill Info And Parameters

### Right Rail

The right rail contains two stacked cards:

1. `技能信息`
2. `可配置参数`

### Skill Info Card

The skill info card remains editable, but should be visually compact:

- skill name input
- skill description input

No large hero block is needed.

### Parameter Card

Parameters should be rendered as dense configuration rows instead of tall cards.

Each row should include:

- enable checkbox
- parameter name
- source label or original field label
- default value input or credential selector
- secret indication when applicable

The parameter card should support longer lists without making the whole page excessively tall. A max-height plus internal scroll is acceptable on desktop.

## Script Preview

Script preview moves from an inline bottom section to a drawer.

Behavior:

- closed by default
- opened by toolbar action
- contains the generated Playwright script
- does not change the height of the main page content when opened

The drawer can slide from the right to align with the configure-page workstation layout.

## Labels And Terminology

Operator-facing field labels in the expanded step details should use Chinese:

- `Primary Locator` -> `主定位器`
- `Frame` -> `框架层级`
- `Validation` -> `校验结果`
- `Locator Candidates` -> `候选定位器`
- `Use This` -> `使用此定位器`
- `Selected` / `Active` -> `当前使用`

Badges such as `ok` and `fallback` may remain as-is initially, but Chinese wording is preferred where it does not cause ambiguity.

## State And Behavior

The redesign should add local UI state for:

- expanded step index
- script drawer open/closed state

Existing behavior that must be preserved:

- loading session data
- parameter extraction
- locator promotion API call
- generate-script API call
- navigation to the test page

## Error Handling

- locator promotion failures continue to surface through the existing page error state
- script preview generation failures continue to use existing error handling
- dense layout must not hide actionable error messages

## Testing Strategy

Verification should focus on:

- page still loads recorded steps and parameters
- collapsed step list shows shorter summaries than before
- only one step expands at a time
- locator promotion still works from the expanded view
- script preview drawer opens and shows generated script
- desktop sticky right rail behaves correctly
- mobile layout still works without overlap

## Implementation Notes

This should remain an in-place redesign of:

- `RpaClaw/frontend/src/pages/rpa/ConfigurePage.vue`

Avoid introducing unnecessary shared components unless the page becomes clearly unmaintainable.
