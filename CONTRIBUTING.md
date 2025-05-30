# Contributing to RepoPacker

First off, thank you for considering contributing to RepoPacker! We welcome any help, from reporting bugs and suggesting features to writing code and improving documentation.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [How Can I Contribute?](#how-can-i-contribute)
  - [Reporting Bugs](#reporting-bugs)
  - [Suggesting Enhancements](#suggesting-enhancements)
  - [Pull Requests](#pull-requests)
- [Development Setup](#development-setup)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [Running Linters and Formatters](#running-linters-and-formatters)
  - [Running Tests](#running-tests)
- [Branching Model](#branching-model)
- [Commit Message Guidelines](#commit-message-guidelines)
- [Code Review Process](#code-review-process)
- [Issue and Pull Request Templates](#issue-and-pull-request-templates)

## Code of Conduct

This project and everyone participating in it is governed by the [RepoPacker Code of Conduct](./CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code. Please report unacceptable behavior to `Manuel Arce at [INSERT EMAIL ADDRESS HERE]`.

## How Can I Contribute?

### Reporting Bugs

If you find a bug, please ensure the bug was not already reported by searching on GitHub under [Issues](https://github.com/username/repopacker/issues).

If you're unable to find an open issue addressing the problem, [open a new one](https://github.com/username/repopacker/issues/new). Be sure to include a **title and clear description**, as much relevant information as possible, and a **code sample or an executable test case** demonstrating the expected behavior that is not occurring.

### Suggesting Enhancements

If you have an idea for an enhancement, please ensure it hasn't already been suggested by searching on GitHub under [Issues](https://github.com/username/repopacker/issues).

If you're unable to find an open issue for your enhancement, [open a new one](https://github.com/username/repopacker/issues/new). Provide a clear description of the enhancement and its potential benefits.

### Pull Requests

1.  Fork the repository and create your branch from `main`.
2.  If you've added code that should be tested, add tests.
3.  If you've changed APIs, update the documentation.
4.  Ensure the test suite passes (`pytest`).
5.  Make sure your code lints and formats correctly (`ruff check .` and `black .`).
6.  Issue that pull request!

## Development Setup

### Prerequisites

*   Python 3.10 or higher.
*   `pip` and `venv` (or your preferred virtual environment tool).
*   Git.

### Installation

1.  Clone your fork of the repository:
    ```bash
    git clone https://github.com/YOUR_USERNAME/repopacker.git # Replace YOUR_USERNAME
    cd repopacker
    ```

2.  Create and activate a virtual environment:
    ```bash
    python -m venv .venv
    source .venv/bin/activate  # On Windows use `.venv\Scripts\activate`
    ```

3.  Install the project in editable mode with development dependencies:
    ```bash
    pip install -e ".[dev]"
    ```

4.  (Optional, but recommended) Install pre-commit hooks:
    ```bash
    pre-commit install
    ```
    This will run linters and formatters automatically before each commit.

### Running Linters and Formatters

We use `ruff` for linting and `black` for code formatting.

*   To check for linting issues with `ruff`:
    ```bash
    ruff check .
    ```
*   To automatically fix linting issues with `ruff` (where possible):
    ```bash
    ruff check . --fix
    ```
*   To format code with `black`:
    ```bash
    black .
    ```
*   To check formatting with `black` (without making changes):
    ```bash
    black . --check
    ```
*   To check types with `mypy`:
    ```bash
    mypy repopacker/
    ```

If you installed pre-commit hooks, these checks will run automatically.

### Running Tests

Tests are written using `pytest`. Run them with:
```bash
pytest
```

## Branching Model

*   **`main`**: This is the primary branch representing the latest stable release. Direct pushes to `main` are discouraged.
*   **Feature Branches**: Create branches from `main` for new features (e.g., `feature/awesome-new-thing`).
*   **Bugfix Branches**: Create branches from `main` for bug fixes (e.g., `fix/annoying-bug`).

Submit Pull Requests (PRs) to merge your feature/bugfix branches into `main`.

## Commit Message Guidelines

We aim to follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/). This makes the commit history more readable and helps automate changelog generation.

A typical commit message looks like:
```
feat: Allow custom output file path
^--^  ^--------------------------^
|     |
|     +-> Summary in present tense.
|
+-------> Type: chore, docs, feat, fix, refactor, style, test.
```

Example:
```
feat: Add keyboard shortcut for toggling sidebar

Adds `Ctrl+B` to show/hide the file selection summary sidebar.
Helps users with smaller screens maximize tree view space.
```

If your commit addresses an issue, reference it in the commit body or footer (e.g., `Fixes #123`).

## Code Review Process

*   Once a PR is submitted, at least one core contributor will review it.
*   Address any feedback or requested changes.
*   Once approved, the PR will be merged into `main`.
*   Be patient, as reviews can take time.

## Issue and Pull Request Templates

We may use GitHub issue and pull request templates in the future to streamline submissions. For now, please try to include as much relevant information as possible when creating issues or PRs.

---

Thank you for your contribution!
