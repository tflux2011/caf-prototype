"""CAF prototype package.

This package hosts the Runtime Semantic Topology (RST) compiler and supporting
data models described in the CAF paper. Extraction is purely static: the
compiler parses source into an AST and never imports or executes target code.
"""

__all__ = ["schema", "annotations"]
