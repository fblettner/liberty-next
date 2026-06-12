"""Liberty Next — connector-driven low-code framework."""
from importlib.metadata import PackageNotFoundError, version as _dist_version

try:
    # The single source of truth is pyproject's ``[project].version`` (the installed dist metadata),
    # so ``__version__`` always matches the running wheel — and the upgrade detector can tell when the
    # app's code/model version actually changed.
    __version__ = _dist_version("liberty-next")
except PackageNotFoundError:  # source checkout without an install — fall back so imports still work
    __version__ = "0.0.0+dev"
