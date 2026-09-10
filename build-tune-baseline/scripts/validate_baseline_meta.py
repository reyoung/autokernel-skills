#!/usr/bin/env python3
"""Validate baseline script interfaces, metrics and protected-file declarations.

Run from the target project, or pass --project-root to locate the metadata.
The validator neither executes evaluation commands nor inspects their files.
Dependencies are listed in the adjacent requirements.txt.
"""

import argparse
import json
import math
from pathlib import Path, PurePosixPath
import sys

try:
    from jsonschema import Draft202012Validator
except ImportError:
    raise SystemExit(
        "Missing dependency: install scripts/requirements.txt from this skill "
        "with python3 -m pip install -r <path-to-requirements.txt>"
    )


SCHEMA_PATH = Path(__file__).with_name("baseline_meta.schema.json")


def load_json(path):
    """Reject duplicate keys and non-finite numbers instead of accepting ambiguity."""
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key!r}")
            result[key] = value
        return result

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(f"non-finite JSON number: {value}")
        return result

    def reject_constant(value):
        raise ValueError(f"invalid JSON constant: {value}")

    with path.open(encoding="utf-8") as stream:
        return json.load(
            stream,
            object_pairs_hook=unique_object,
            parse_float=finite_float,
            parse_constant=reject_constant,
        )


def pointer(parts):
    return "/" + "/".join(
        str(part).replace("~", "~0").replace("/", "~1") for part in parts
    )


def validate_metadata(metadata):
    """Return structural and interface-description errors without project access."""
    schema = load_json(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    errors = [
        f"{pointer(error.absolute_path)}: {error.message}"
        for error in Draft202012Validator(schema).iter_errors(metadata)
    ]
    if errors:
        return sorted(errors)

    def relative_path(value, location):
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or not path.name:
            errors.append(
                f"{location}: use a project-relative file path without '..'"
            )
            return None
        return path

    protected = set()
    for index, record in enumerate(metadata["protected_files"]):
        location = f"/protected_files/{index}/path"
        path = relative_path(record["path"], location)
        if path is not None:
            if path in protected:
                errors.append(f"{location}: duplicate protected file")
            protected.add(path)

    evaluation = metadata["evaluation"]
    scripts = {}
    for mode in ("verify", "benchmark"):
        location = f"/evaluation/{mode}/script"
        path = relative_path(evaluation[mode]["script"], location)
        if path is not None and path not in protected:
            errors.append(f"{location}: script must be listed in protected_files")
        scripts[mode] = path
    if scripts["verify"] is not None and scripts["verify"] == scripts["benchmark"]:
        if evaluation["verify"]["command"] == evaluation["benchmark"]["command"]:
            errors.append(
                "/evaluation: shared script requires distinct verify and benchmark commands"
            )
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "metadata", nargs="?", type=Path,
        default=Path(".autokernel/baseline_meta.json"),
        help="metadata path, relative to --project-root unless absolute",
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    root = args.project_root.resolve()
    metadata_path = args.metadata if args.metadata.is_absolute() else root / args.metadata
    try:
        errors = validate_metadata(load_json(metadata_path))
    except (OSError, ValueError) as error:
        print(f"Invalid baseline metadata: {error}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"Baseline metadata is valid: {metadata_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
