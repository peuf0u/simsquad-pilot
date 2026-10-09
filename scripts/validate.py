#!/usr/bin/env python3
"""Validate a pilot artifact against its schema (stdlib only).

Usage: validate.py {feature-context|result|report} <file.json>

Exit 0 when valid; exit 1 with one error per stderr line otherwise.

Structural validation uses the JSON-Schema subset the contract needs
(type, required, properties, additionalProperties, items, enum, pattern,
minLength, minItems, minimum, maximum, $ref incl. sibling-file refs).
Semantic checks a schema can't express:
  result  — bug findings carry severity; bug/blocked/question findings
            carry non-empty evidence; every evidence/screenshot path
            exists relative to the result.json directory.
  report  — summary counts match the device payloads.
"""

import json
import os
import re
import sys

SCHEMA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "schemas")
KINDS = {
    "feature-context": "feature-context.schema.json",
    "result": "result.schema.json",
    "report": "report.schema.json",
}

_schema_cache = {}


def load_schema(filename):
    if filename not in _schema_cache:
        with open(os.path.join(SCHEMA_DIR, filename), encoding="utf-8") as f:
            _schema_cache[filename] = json.load(f)
    return _schema_cache[filename]


def resolve_ref(ref, current_file):
    """Resolve '#/...' within the current schema or 'file.json#/...' in a sibling."""
    file_part, _, pointer = ref.partition("#")
    schema_file = file_part or current_file
    node = load_schema(schema_file)
    for token in pointer.strip("/").split("/"):
        if token:
            node = node[token]
    return node, schema_file


TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
}


def check(value, schema, path, schema_file, errors):
    if "$ref" in schema:
        target, target_file = resolve_ref(schema["$ref"], schema_file)
        check(value, target, path, target_file, errors)
        return

    if "enum" in schema:
        if value not in schema["enum"]:
            errors.append(f"{path}: {value!r} not one of {schema['enum']}")
        return

    expected = schema.get("type")
    if expected:
        py_type = TYPES[expected]
        # bool is an int subclass in Python; don't let true pass as integer.
        if isinstance(value, bool) and expected in ("integer", "number"):
            errors.append(f"{path}: expected {expected}, got boolean")
            return
        if not isinstance(value, py_type):
            errors.append(f"{path}: expected {expected}, got {type(value).__name__}")
            return

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: does not match pattern {schema['pattern']}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum {schema['maximum']}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than minItems {schema['minItems']}")
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(value):
                check(item, item_schema, f"{path}[{i}]", schema_file, errors)

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required key {key!r}")
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, sub in value.items():
            sub_path = f"{path}.{key}"
            if key in props:
                check(sub, props[key], sub_path, schema_file, errors)
            elif isinstance(extra, dict):
                check(sub, extra, sub_path, schema_file, errors)
            elif extra is False:
                errors.append(f"{sub_path}: unexpected key")


def iter_findings(exploration):
    return exploration.get("findings", [])


def check_result_semantics(doc, base_dir, errors):
    def must_exist(rel, path):
        if not os.path.exists(os.path.join(base_dir, rel)):
            errors.append(f"{path}: file does not exist: {rel}")

    for i, chk in enumerate(doc.get("smoke", {}).get("checks", [])):
        if chk.get("screenshot"):
            must_exist(chk["screenshot"], f"$.smoke.checks[{i}].screenshot")

    exploration = doc.get("exploration", {})
    for i, action in enumerate(exploration.get("actions", [])):
        if action.get("screenshot"):
            must_exist(action["screenshot"], f"$.exploration.actions[{i}].screenshot")

    for i, finding in enumerate(iter_findings(exploration)):
        fpath = f"$.exploration.findings[{i}]"
        ftype = finding.get("type")
        if ftype == "bug" and "severity" not in finding:
            errors.append(f"{fpath}: bug finding requires severity")
        if ftype in ("bug", "blocked", "question"):
            evidence = finding.get("evidence") or []
            if not evidence:
                errors.append(f"{fpath}: {ftype} finding requires non-empty evidence")
            for j, rel in enumerate(evidence):
                must_exist(rel, f"{fpath}.evidence[{j}]")


def check_report_semantics(doc, errors):
    devices = doc.get("devices", [])
    summary = doc.get("summary", {})

    def count_findings(ftype):
        return sum(
            1
            for d in devices
            for f in iter_findings(d.get("exploration", {}))
            if f.get("type") == ftype
        )

    expectations = {
        "devices_total": len(devices),
        "smoke_passed": sum(1 for d in devices if d.get("smoke", {}).get("status") == "passed"),
        "smoke_failed": sum(1 for d in devices if d.get("smoke", {}).get("status") == "failed"),
        "bugs": count_findings("bug"),
        "blocked": count_findings("blocked"),
        "questions": count_findings("question"),
    }
    for key, expected in expectations.items():
        if summary.get(key) != expected:
            errors.append(f"$.summary.{key}: is {summary.get(key)}, devices say {expected}")


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in KINDS:
        sys.exit(__doc__.strip().splitlines()[2].strip())
    kind, target = sys.argv[1], sys.argv[2]

    try:
        with open(target, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"validate.py: cannot read {target}: {exc}")

    schema_file = KINDS[kind]
    errors = []
    check(doc, load_schema(schema_file), "$", schema_file, errors)

    # Semantic passes only run on structurally sound documents.
    if not errors:
        if kind == "result":
            check_result_semantics(doc, os.path.dirname(os.path.abspath(target)), errors)
        elif kind == "report":
            check_report_semantics(doc, errors)

    if errors:
        for e in errors:
            print(f"{target}: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"{target}: valid {kind}")


if __name__ == "__main__":
    main()
