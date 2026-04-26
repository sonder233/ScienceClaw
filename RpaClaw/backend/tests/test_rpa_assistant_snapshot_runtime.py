from __future__ import annotations

import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


_MODULE_PATH = Path(__file__).resolve().parents[1] / "rpa" / "assistant_snapshot_runtime.py"
_SPEC = spec_from_file_location("assistant_snapshot_runtime_under_test", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_MODULE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

SNAPSHOT_V2_JS = _MODULE.SNAPSHOT_V2_JS


def test_snapshot_v2_js_captures_visible_business_fields():
    assert "span" in SNAPSHOT_V2_JS
    assert "[data-field]" in SNAPSHOT_V2_JS
    assert "[data-label]" in SNAPSHOT_V2_JS
    assert "[data-value]" in SNAPSHOT_V2_JS
    assert "function buildContentLocator(el, role, name, text, placeholder, title)" in SNAPSHOT_V2_JS
    assert "stable data-field locator" in SNAPSHOT_V2_JS
    assert "stable data-label locator" in SNAPSHOT_V2_JS
    assert "stable data-value locator" in SNAPSHOT_V2_JS
    assert "const className = normalizeText(el.className || '', 80);" in SNAPSHOT_V2_JS
    assert "const dataLabel = normalizeText(el.getAttribute('data-label') || '', 80);" in SNAPSHOT_V2_JS
    assert "data_field: normalizeText(el.getAttribute('data-field') || '', 80)" in SNAPSHOT_V2_JS
    assert "data_label: normalizeText(el.getAttribute('data-label') || '', 80)" in SNAPSHOT_V2_JS
    assert "data_value: normalizeText(el.getAttribute('data-value') || '', 80)" in SNAPSHOT_V2_JS
    assert "if (tag === 'label' || /label/i.test(className) || dataLabel)" in SNAPSHOT_V2_JS
    assert "if (dataField || dataValue || /value/i.test(className))" in SNAPSHOT_V2_JS
    assert "function isMeaningfulBusinessContainer(el)" in SNAPSHOT_V2_JS
    assert "data-section" in SNAPSHOT_V2_JS
    assert "data-region" in SNAPSHOT_V2_JS
    assert "detail_section" in SNAPSHOT_V2_JS


def test_snapshot_v2_js_collects_structured_table_and_detail_views():
    assert "table_views: []" in SNAPSHOT_V2_JS
    assert "detail_views: []" in SNAPSHOT_V2_JS
    assert "function collectTableViews()" in SNAPSHOT_V2_JS
    assert "function collectDetailViews()" in SNAPSHOT_V2_JS
    assert "data-colid" in SNAPSHOT_V2_JS
    assert "row_count_observed" in SNAPSHOT_V2_JS
    assert "column_header" in SNAPSHOT_V2_JS
    assert "row_local_actions" in SNAPSHOT_V2_JS
    assert "aui-form-item" in SNAPSHOT_V2_JS
    assert "data_prop" in SNAPSHOT_V2_JS
    assert "value_kind" in SNAPSHOT_V2_JS


def test_snapshot_v2_js_marks_non_row_table_text_as_auxiliary():
    assert "auxiliary_text" in SNAPSHOT_V2_JS
    assert "empty_state" in SNAPSHOT_V2_JS
    assert "tooltip" in SNAPSHOT_V2_JS
    assert "outside_rows" in SNAPSHOT_V2_JS


def test_snapshot_v2_js_assigns_nearby_heading_to_table_view_title():
    assert "function nearestTableTitle(root)" in SNAPSHOT_V2_JS
    assert "previousElementSibling" in SNAPSHOT_V2_JS
    assert "nearest_preceding_heading" in SNAPSHOT_V2_JS
    assert "title_source" in SNAPSHOT_V2_JS


def test_snapshot_v2_js_collects_jalor_grid_as_scoped_table_view():
    assert "function collectJalorGridViews()" in SNAPSHOT_V2_JS
    assert ".jalor-igrid" in SNAPSHOT_V2_JS
    assert ".jalor-igrid-head tbody.igrid-head td" in SNAPSHOT_V2_JS
    assert ".jalor-igrid-body tbody.igrid-data tr.grid-row" in SNAPSHOT_V2_JS
    assert ":scope > .jalor-igrid-body" in SNAPSHOT_V2_JS
    assert "tr.grid-row-group" in SNAPSHOT_V2_JS
    assert "framework_hint: 'jalor-igrid'" in SNAPSHOT_V2_JS
    assert "page.locator('#\" + escapeCssAttributeValue(bodyTableId) + \" tbody.igrid-data tr.grid-row').nth(\" + rowIndex + \")" in SNAPSHOT_V2_JS
    assert "td[field=\"" in SNAPSHOT_V2_JS


def test_snapshot_v2_js_does_not_duplicate_jalor_inner_tables_as_generic_tables():
    assert "collectJalorGridViews()" in SNAPSHOT_V2_JS
    assert "el.closest('.jalor-igrid')" in SNAPSHOT_V2_JS


@pytest.mark.asyncio
async def test_snapshot_v2_js_maps_jalor_header_body_grid_rows():
    playwright = pytest.importorskip("playwright.async_api")
    html = """
    <html>
      <body>
        <div id="taskExportGrid" class="jalor-igrid">
          <div class="jalor-igrid-content">
            <div class="jalor-igrid-head">
              <table id="taskExportGridHeader">
                <tbody class="igrid-head">
                  <tr>
                    <td _col="0" class="grid-seq"><span></span></td>
                    <td _col="1" class="grid-select"><span></span></td>
                    <td _col="2" field="tmpName"><span>File Name</span></td>
                    <td _col="4" field="moduleName"><span>Task Type</span></td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div class="jalor-igrid-body">
              <table id="taskExportGridTable" class="jalor-igrid-tbody">
                <tbody class="igrid-data">
                  <tr class="grid-row-group"><td colspan="4">Created : 2026-04-26</td></tr>
                  <tr class="grid-row" _row="0">
                    <td _col="0" class="grid-seq"><span>1</span></td>
                    <td _col="1" class="grid-select"><span></span></td>
                    <td _col="2" field="tmpName"><a href="/download/a">File_A.xlsx</a></td>
                    <td _col="4" field="moduleName">ExportConfigA</td>
                  </tr>
                  <tr class="grid-row" _row="1">
                    <td _col="0" class="grid-seq"><span>2</span></td>
                    <td _col="1" class="grid-select"><span></span></td>
                    <td _col="2" field="tmpName"><a href="/download/b">File_B.xlsx</a></td>
                    <td _col="4" field="moduleName">ExportConfigA</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </body>
    </html>
    """
    pw = await playwright.async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    try:
        await page.set_content(html)
        snapshot = json.loads(await page.evaluate(SNAPSHOT_V2_JS))
    finally:
        await browser.close()
        await pw.stop()

    assert len(snapshot["table_views"]) == 1
    table = snapshot["table_views"][0]
    assert table["framework_hint"] == "jalor-igrid"
    assert table["row_count_observed"] == 2
    assert any(column["column_id"] == "tmpName" and column["header"] == "File Name" for column in table["columns"])
    assert table["rows"][0]["cells"][2]["column_header"] == "File Name"
    assert table["rows"][0]["cells"][2]["text"] == "File_A.xlsx"
    assert table["rows"][0]["cells"][2]["actions"][0]["locator"]["value"] == 'td[field="tmpName"] a'
    assert table["rows"][0]["locator_hints"][0]["expression"] == (
        "page.locator('#taskExportGridTable tbody.igrid-data tr.grid-row').nth(0)"
    )
