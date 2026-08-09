from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]

try:
    __version__ = version("noteropdf")
except PackageNotFoundError:
    __version__ = "0+unknown"
