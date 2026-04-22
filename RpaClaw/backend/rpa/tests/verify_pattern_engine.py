#!/usr/bin/env python3
"""Manual verification script for the pattern engine implementation."""

import sys
import os

script_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.join(script_dir, '..', '..')
sys.path.insert(0, backend_dir)

from rpa.pattern_engine import (
    PatternMatch,
    detect_ui_pattern,
    build_semantic_extract_js,
    analyze_dom_for_pattern,
    build_enhanced_extract_candidates,
)
from rpa.extracted_fields import parse_extracted_fields, build_extract_candidates


def test_pattern_detection():
    print("=" * 60)
    print("TEST 1: Pattern Detection")
    print("=" * 60)
    
    test_cases = [
        {
            "name": "AUI Form Field",
            "dom_info": {
                "has_label_for": True,
                "has_form_container": True,
                "framework_classes": ["aui-form-item"],
                "has_table_structure": False,
                "has_dl_structure": False,
            },
            "expected_pattern": "FORM_FIELD_LABEL_VALUE",
            "expected_hint": "aui",
        },
        {
            "name": "Element Plus Form",
            "dom_info": {
                "has_label_for": False,
                "has_form_container": True,
                "framework_classes": ["el-form-item"],
                "has_table_structure": False,
                "has_dl_structure": False,
            },
            "expected_pattern": "FORM_FIELD_LABEL_VALUE",
        },
        {
            "name": "Ant Design Form",
            "dom_info": {
                "has_label_for": True,
                "has_form_container": True,
                "framework_classes": ["ant-form-item"],
                "has_table_structure": False,
                "has_dl_structure": False,
            },
            "expected_pattern": "FORM_FIELD_LABEL_VALUE",
        },
        {
            "name": "Bootstrap Form",
            "dom_info": {
                "has_label_for": True,
                "has_form_container": True,
                "framework_classes": ["form-group", "mb-3"],
                "has_table_structure": False,
                "has_dl_structure": False,
            },
            "expected_pattern": "FORM_FIELD_LABEL_VALUE",
        },
        {
            "name": "Table Structure",
            "dom_info": {
                "has_label_for": False,
                "has_form_container": False,
                "framework_classes": [],
                "has_table_structure": True,
                "has_dl_structure": False,
            },
            "expected_pattern": "TABLE_CELL_PAIR",
        },
        {
            "name": "Generic Fallback",
            "dom_info": {
                "has_label_for": False,
                "has_form_container": False,
                "framework_classes": [],
                "has_table_structure": False,
                "has_dl_structure": False,
            },
            "expected_pattern": "GENERIC_SIBLING",
        },
    ]
    
    all_passed = True
    for tc in test_cases:
        patterns = detect_ui_pattern(tc["dom_info"])
        primary = patterns[0] if patterns else None
        
        passed = primary and primary.name == tc["expected_pattern"]
        status = "✅ PASS" if passed else "❌ FAIL"
        
        print(f"\n{status} - {tc['name']}")
        if primary:
            print(f"   Pattern: {primary.name}")
            print(f"   Confidence: {primary.confidence:.2f}")
            if hasattr(tc, 'get') and tc.get("expected_hint"):
                hint_match = primary.framework_hint == tc.get("expected_hint")
                print(f"   Framework Hint: {primary.framework_hint} {'✓' if hint_match else '✗'}")
        
        all_passed = all_passed and passed
    
    return all_passed


def test_semantic_extraction():
    print("\n" + "=" * 60)
    print("TEST 2: Semantic Extraction JS Generation")
    print("=" * 60)
    
    test_cases = [
        {
            "label": "直接主管",
            "pattern": PatternMatch(
                name="FORM_FIELD_LABEL_VALUE",
                confidence=0.98,
                context={"has_label_for": True},
                framework_hint="aui",
            ),
            "expected_strategy": "label_for_association",
            "min_confidence": 0.95,
        },
        {
            "label": "用户名",
            "pattern": PatternMatch(
                name="FORM_FIELD_LABEL_VALUE",
                confidence=0.85,
                context={"has_label_for": False},
                framework_hint="element-plus",
            ),
            "expected_strategy": "container_search",
            "min_confidence": 0.9,
        },
        {
            "label": "姓名",
            "pattern": PatternMatch(
                name="TABLE_CELL_PAIR",
                confidence=0.88,
                context={},
            ),
            "expected_strategy": "structured_pair",
            "min_confidence": 0.85,
        },
        {
            "label": "状态",
            "pattern": PatternMatch(
                name="GENERIC_SIBLING",
                confidence=0.6,
                context={},
            ),
            "expected_strategy": "generic_sibling",
            "min_confidence": 0.7,
        },
    ]
    
    all_passed = True
    for tc in test_cases:
        candidate = build_semantic_extract_js(tc["label"], tc["pattern"])
        
        strategy_match = candidate.strategy_name == tc["expected_strategy"]
        confidence_ok = candidate.confidence >= tc["min_confidence"]
        has_expression = bool(candidate.expression)
        
        passed = strategy_match and confidence_ok and has_expression
        status = "✅ PASS" if passed else "❌ FAIL"
        
        print(f"\n{status} - Label: '{tc['label']}'")
        print(f"   Strategy: {candidate.strategy_name} {'✓' if strategy_match else '✗'}")
        print(f"   Confidence: {candidate.confidence:.2f} {'✓' if confidence_ok else '✗'}")
        print(f"   Has Expression: {'Yes' if has_expression else 'No'} {'✓' if has_expression else '✗'}")
        print(f"   Description: {candidate.description[:50]}...")
        
        all_passed = all_passed and passed
    
    return all_passed


def test_enhanced_candidates():
    print("\n" + "=" * 60)
    print("TEST 3: Enhanced Candidates Generation")
    print("=" * 60)
    
    test_cases = [
        {
            "name": "AUI with snapshot",
            "label": "直接主管",
            "dom_info": {
                "has_label_for": True,
                "has_form_container": True,
                "framework_classes": ["aui-form-item"],
                "has_table_structure": False,
                "has_dl_structure": False,
            },
            "min_candidates": 3,
        },
        {
            "name": "Element Plus with snapshot",
            "label": "邮箱",
            "dom_info": {
                "has_label_for": False,
                "has_form_container": True,
                "framework_classes": ["el-form-item"],
                "has_table_structure": False,
                "has_dl_structure": False,
            },
            "min_candidates": 2,
        },
        {
            "name": "Without snapshot (backward compat)",
            "label": "备注",
            "dom_info": None,
            "min_candidates": 2,
        },
    ]
    
    all_passed = True
    for tc in test_cases:
        candidates = build_enhanced_extract_candidates(tc["label"], tc["dom_info"])
        
        count_ok = len(candidates) >= tc["min_candidates"]
        has_selected = any(c.get("selected") for c in candidates)
        has_strategy_names = all("strategy_name" in c for c in candidates)
        has_confidence = all("confidence" in c for c in candidates)
        
        passed = count_ok and has_selected and has_strategy_names and has_confidence
        status = "✅ PASS" if passed else "❌ FAIL"
        
        print(f"\n{status} - {tc['name']}")
        print(f"   Candidates: {len(candidates)} (≥{tc['min_candidates']}) {'✓' if count_ok else '✗'}")
        print(f"   Has Selected: {'Yes' if has_selected else 'No'} {'✓' if has_selected else '✗'}")
        print(f"   Strategy Names Present: {'Yes' if has_strategy_names else 'No'}")
        print(f"   Confidence Scores Present: {'Yes' if has_confidence else 'No'}")
        
        if candidates:
            strategies = [c.get("strategy_name") for c in candidates]
            print(f"   Strategies: {strategies}")
        
        all_passed = all_passed and passed
    
    return all_passed


def test_dom_analysis():
    print("\n" + "=" * 60)
    print("TEST 4: DOM Snapshot Analysis")
    print("=" * 60)
    
    test_cases = [
        {
            "name": "AUI Container",
            "snapshot": {
                "tagName": "DIV",
                "classList": ["aui-form-item", "is-text"],
            },
            "check_has_form_container": True,
            "check_framework_in_class": "aui",
        },
        {
            "name": "Element Plus Container",
            "snapshot": {
                "tagName": "DIV",
                "classList": ["el-form-item", "is-required"],
            },
            "check_has_form_container": True,
            "check_framework_in_class": "el-",
        },
        {
            "name": "Label with [for]",
            "snapshot": {
                "tagName": "LABEL",
                "classList": [],
                "htmlFor": "field-12345",
            },
            "check_has_label_for": True,
        },
        {
            "name": "Table Row",
            "snapshot": {
                "tagName": "TR",
                "role": "row",
                "classList": [],
            },
            "check_has_table_structure": True,
        },
    ]
    
    all_passed = True
    for tc in test_cases:
        result = analyze_dom_for_pattern(tc["snapshot"])
        
        checks = []
        if "check_has_form_container" in tc:
            checks.append(("has_form_container", result["has_form_container"] == tc["check_has_form_container"]))
        if "check_has_label_for" in tc:
            checks.append(("has_label_for", result["has_label_for"] == tc["check_has_label_for"]))
        if "check_has_table_structure" in tc:
            checks.append(("has_table_structure", result["has_table_structure"] == tc["check_has_table_structure"]))
        if "check_framework_in_class" in tc:
            checks.append(("framework_in_class", tc["check_framework_in_class"] in result["class_str"]))
        
        all_checks_pass = all(passed for _, passed in checks)
        status = "✅ PASS" if all_checks_pass else "❌ FAIL"
        
        print(f"\n{status} - {tc['name']}")
        for check_name, check_result in checks:
            print(f"   {check_name}: {'✓' if check_result else '✗'}")
        
        all_passed = all_passed and all_checks_pass
    
    return all_passed


def test_integration():
    print("\n" + "=" * 60)
    print("TEST 5: Integration Test (extracted_fields + pattern_engine)")
    print("=" * 60)
    
    output = "zhangwei WX1383818"
    element_snapshot = {
        "tagName": "SPAN",
        "role": "",
        "classList": ["aui-input-display-only__content"],
        "containerTag": "DIV",
        "containerClasses": ["aui-form-item"],
    }
    
    fields = parse_extracted_fields(
        output,
        hint_label="直接主管",
        element_snapshot=element_snapshot,
    )
    
    has_fields = len(fields) > 0
    has_candidates = has_fields and "extract_candidates" in fields[0]
    candidate_count = len(fields[0].get("extract_candidates", [])) if has_candidates else 0
    has_semantic = False
    if has_candidates:
        semantic_strategies = ["label_for_association", "container_search"]
        has_semantic = any(
            c.get("strategy_name") in semantic_strategies
            for c in fields[0]["extract_candidates"]
        )
    
    passed = has_fields and has_candidates and candidate_count >= 2 and has_semantic
    status = "✅ PASS" if passed else "❌ FAIL"
    
    print(f"\n{status} - AUI Integration Test")
    print(f"   Fields Extracted: {len(fields)} {'✓' if has_fields else '✗'}")
    print(f"   Has Candidates: {'Yes' if has_candidates else 'No'} {'✓' if has_candidates else '✗'}")
    print(f"   Candidate Count: {candidate_count} (≥2) {'✓' if candidate_count >= 2 else '✗'}")
    print(f"   Has Semantic Strategies: {'Yes' if has_semantic else 'No'} {'✓' if has_semantic else '✗'}")
    
    return passed


def main():
    print("\n" + "=" * 60)
    print("PATTERN ENGINE VERIFICATION SUITE")
    print("Testing universal UI pattern detection & extraction")
    print("=" * 60)
    
    results = []
    results.append(("Pattern Detection", test_pattern_detection()))
    results.append(("Semantic Extraction JS", test_semantic_extraction()))
    results.append(("Enhanced Candidates", test_enhanced_candidates()))
    results.append(("DOM Analysis", test_dom_analysis()))
    results.append(("Integration", test_integration()))
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    total_tests = len(results)
    passed_tests = sum(1 for _, result in results if result)
    
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {name}")
    
    print(f"\nTotal: {passed_tests}/{total_tests} tests passed")
    
    if passed_tests == total_tests:
        print("\n🎉 All tests passed! Implementation is working correctly.")
        return 0
    else:
        print(f"\n⚠️  {total_tests - passed_tests} test(s) failed. Please review.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
