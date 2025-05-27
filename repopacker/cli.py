# repopacker/cli.py

import sys
from pathlib import Path
# We assume repoyank.py will be moved to repopacker/app.py
# and the RepoPackerApp class is defined there.
from .app import RepoPackerApp 

def main() -> None:
    initial_folder: Path | None = None
    if len(sys.argv) > 1:
        path_arg_str = sys.argv[1]
        # Basic check for help flags, can be expanded later with argparse
        if path_arg_str in ("-h", "--help"):
            # For now, just print a simple help message.
            # A more sophisticated CLI argument parser like argparse or typer
            # could be added later if more CLI options are needed.
            print("Usage: repopacker [optional_path_to_project]")
            print("\nInteractive TUI for selecting and packing repository files into an AI-friendly prompt.")
            sys.exit(0)

        path_arg = Path(path_arg_str)
        if path_arg.is_dir():
            initial_folder = path_arg.resolve()
        else:
            print(f"Warning: Provided path '{path_arg_str}' is not a valid directory. Starting without initial project.")
            # Optionally, exit here or let the app open its folder selection dialog
            # For now, we'll let the app handle it by starting without a path.

    app = RepoPackerApp(initial_path=initial_folder)
    app.run()

if __name__ == "__main__":
    # This allows running the cli directly for testing, though the entry point
    # via pyproject.toml will call main().
    main()
