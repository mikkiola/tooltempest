"""Shape found by the audit after the two incidents: a guard imports its sibling modules from the directory it sits in."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import docs_gate  # noqa: E402,F401
