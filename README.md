# RepoPacker

[![PyPI version](https://img.shields.io/pypi/v/repopacker.svg?style=flat-square)](https://pypi.org/project/repopacker/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](./LICENSE)
[![CI Status](https://img.shields.io/github/actions/workflow/status/username/repopacker/ci.yml?branch=main&style=flat-square)](https://github.com/username/repopacker/actions/workflows/ci.yml)
[![Python Versions](https://img.shields.io/pypi/pyversions/repopacker.svg?style=flat-square)](https://pypi.org/project/repopacker/)

Interactive TUI for selecting and packing repository files into an AI-friendly prompt.

RepoPacker provides a terminal-based user interface to navigate your project, select specific files
and directories, and then generate a consolidated text block. This block includes the content of the
selected files, formatted for easy input into large language models (LLMs) or for archival purposes.
It respects `.gitignore` rules and offers additional filtering capabilities.

## ✨ Features (Planned/Included)

*   Interactive file tree navigation.
*   Selection of multiple files and directories.
*   Respects `.gitignore` files and custom ignore patterns.
*   Filters out binary files and overly large files.
*   Copies the packed output to the system clipboard.
*   Recent project history.
*   Customizable output format (currently XML-like).
*   Keyboard-driven interface.

## 🖼️ Screenshot / GIF

![RepoPacker TUI Screenshot](https://via.placeholder.com/800x500.png?text=RepoPacker+TUI+Screenshot+Placeholder)
*(Animated GIF or actual screenshot of the TUI will be placed here.)*

## 🚀 Quick Start

### Installation

You can install `repopacker` using pipx (recommended for CLI tools) or pip:

**Using pipx:**
```bash
pipx install repopacker
```

**Using pip:**
```bash
pip install repopacker
```

### Basic Usage

To start RepoLlama, simply run the command:
```bash
repopacker
```
This will open the TUI in the current directory.

You can also specify a path to a project:
```bash
repopacker /path/to/your/project
```

Navigate the tree, select files/folders, and press 'c' to copy the packed content to your
clipboard.

## ⌨️ Detailed Usage & Keyboard Shortcuts

RepoPacker is designed to be primarily keyboard-driven for efficiency.

### Global Application Shortcuts

| Key             | Action                      | Description                                       |
|-----------------|-----------------------------|---------------------------------------------------|
| `Ctrl+Q`        | Quit                        | Exit the application.                             |
| `F5`            | Open Folder                 | Open the folder selection dialog.                 |
| `c`             | Copy Prompt                 | Generate and copy the packed file content.        |
| `a`             | Select All (Project)        | Mark all files/folders in the project for packing.|
| `d`             | Deselect All (Project)      | Clear all selections in the project.              |
| `Ctrl+A`        | Select Content (Dir)        | Mark all items within the currently focused folder. |
| `Ctrl+D`        | Deselect Content (Dir)      | Clear selections within the focused folder.       |
| `Ctrl+\`       | Command Palette             | (Future feature) Open command palette.            |
| `F1`            | Toggle Dark/Light           | Switch between dark and light mode.               |
| `?`             | Help                        | (Future feature) Show help screen.                |

### File Tree Navigation & Selection

| Key             | Action                      | Description                                       |
|-----------------|-----------------------------|---------------------------------------------------|
| `Up` / `k`      | Cursor Up                   | Move the cursor up in the tree.                   |
| `Down` / `j`    | Cursor Down                 | Move the cursor down in the tree.                 |
| `Enter`         | Toggle Expand/Select        | Expand/collapse a directory or select/deselect a file. |
| `Space`         | Toggle Select (Item)        | Select/deselect the item under the cursor.        |
<!-- `Shift+Up/Down` | Extend Selection            | (Future feature) Extend selection up or down. -->
<!-- `Ctrl+Space`    | Toggle Select (Recursive)   | (Future feature) Select/deselect a folder and its contents. -->

The sidebar on the right will show a list of files that will be included in the packed output based
on your current selections.

## 🤝 Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](./CONTRIBUTING.md) for guidelines on how to
contribute, including setting up your development environment, our branching model, and coding
standards.

## 🗺️ Roadmap / TODO

*   [ ] Add support for more fine-grained include/exclude patterns (beyond `.gitignore`).
*   [ ] Implement configuration file for default ignores, max file size, etc.
    (`~/.config/repopacker/config.toml`).
*   [ ] Add options for different output formats (e.g., plain text, JSON).
*   [ ] Implement a "Copy as Markdown" feature.
*   [ ] Enhance TUI with more visual cues and better mouse support (where appropriate).
*   [ ] Develop a comprehensive test suite.
*   [ ] Add a command palette for quick actions.
*   [ ] Create a proper help screen within the application.
*   [ ] Package for conda-forge.
*   [ ] Improve error handling and reporting in the TUI.

## 📜 License

This project is licensed under the MIT License - see the [LICENSE](./LICENSE) file for details.

Copyright (c) 2024 Manuel Arce.

## 📝 Citation

If you use RepoPacker in your work or research, please consider citing it (details TBD once a
stable release/DOI is available).
