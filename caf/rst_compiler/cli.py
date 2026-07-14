"""Command-line entry point for the RST compiler.

Usage::

    caf-rst <repo-root> -o rst.json
    caf-rst --print-schema        # emit the language-agnostic JSON Schema
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from caf.rst_compiler.compiler import compile_rst
from caf.schema import RST


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="caf-rst",
        description="Compile a Runtime Semantic Topology (RST) from service source.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        help="repository root containing service subdirectories",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("rst.json"),
        help="output path for the RST artifact (default: rst.json)",
    )
    parser.add_argument("--version", default="0.1.0", help="RST artifact version")
    parser.add_argument(
        "--generated-by",
        default="caf-ast-compiler-py@0.1",
        help="compiler stamp recorded in the artifact",
    )
    parser.add_argument(
        "--print-schema",
        action="store_true",
        help="print the RST JSON Schema (the contract for all front-ends) and exit",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.print_schema:
        print(json.dumps(RST.model_json_schema(), indent=2))
        return 0

    if args.path is None:
        parser.error("the 'path' argument is required unless --print-schema is used")
    if not args.path.is_dir():
        parser.error(f"{args.path} is not a directory")

    rst = compile_rst(args.path, version=args.version, generated_by=args.generated_by)
    payload = rst.model_dump(by_alias=True, exclude_none=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        f"Wrote {args.output} with {len(rst.nodes)} nodes and {len(rst.edges)} edges.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
