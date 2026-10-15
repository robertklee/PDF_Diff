"""pdfdiff - offline PDF text comparison."""

__version__ = "0.1.0"

from .compare import compare  # noqa: E402
from .model import CompareOptions, DiffResult  # noqa: E402

__all__ = ["compare", "CompareOptions", "DiffResult", "__version__"]
