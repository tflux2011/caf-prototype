"""The Runtime Semantic Topology (RST) compiler.

A static-analysis front-end that extracts an RST from Python service source.
See :mod:`caf.rst_compiler.compiler` for the entry point.
"""

from caf.rst_compiler.compiler import compile_rst

__all__ = ["compile_rst"]
