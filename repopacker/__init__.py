# repopacker/__init__.py

__version__ = "0.1.0"

# Make main() from cli.py available at the package level
# e.g., for `python -m repopacker`
from .cli import main 

# To make the app class available directly (optional, consider if needed for API)
# from .app import RepoPackerApp
