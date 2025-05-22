#!/usr/bin/env python
"""
RepoPacker TUI: A Textual User Interface for selecting files and directories
from a project, respecting .gitignore rules and other ignore patterns,
and generating a packed representation of the selected contents for an AI prompt.

This tool helps in creating context-rich prompts for AI models by including
relevant parts of a codebase. It provides a file tree for easy selection,
shows which files will be included, and copies the final packed content
to the clipboard.
"""
import asyncio
import os
from pathlib import Path
from typing import Set, List, Optional, Iterable
import fnmatch

from textual.app import App, ComposeResult, Screen
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.reactive import reactive
from textual.widgets import Header, Footer, DirectoryTree, Button, Static, Input, Label, Markdown
from textual.widgets._tree import TreeNode
from textual.widgets._directory_tree import DirEntry
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.css.query import NoMatches
from textual.message import Message
from textual import events
from textual.style import Style
from rich.text import Text

import gitignore_parser
import pyperclip # For clipboard operations
import aiofiles
import aiofiles.os as aios

from abc import ABC, abstractmethod # For AsyncFileReader if using ABC
from typing import Protocol, runtime_checkable # For AsyncFileReader if using Protocol

# --- FileReader Interface (Protocol approach chosen) ---
@runtime_checkable
class AsyncFileReader(Protocol):
    @abstractmethod
    async def read_sample(self, filepath: Path, sample_size: int) -> bytes:
        """Reads a sample of bytes from the beginning of a file."""
        ...

class DefaultAsyncFileReader(AsyncFileReader):
    """Default implementation of AsyncFileReader using aiofiles."""
    async def read_sample(self, filepath: Path, sample_size: int) -> bytes:
        # The original is_binary_heuristic catches (OSError, IOError) and returns True (treat as binary).
        # This reader will let exceptions propagate to be caught by is_binary_heuristic.
        async with aiofiles.open(filepath, 'rb') as f:
            return await f.read(sample_size)

# --- Configuration ---
DEFAULT_IGNORES = [
    ".git/", ".hg/", ".svn/", "__pycache__/", "*.pyc", "*.pyo", "*.pyd",
    ".Python", "build/", "develop-eggs/", "dist/", "downloads/", "eggs/",
    ".eggs/", "lib/", "lib64/", "parts/", "sdist/", "var/", "wheels/",
    "*.egg-info/", ".installed.cfg", "*.egg", "MANIFEST",
    ".env", ".venv", "env/", "venv/", "ENV/", "VENV/", "node_modules/",
    "npm-debug.log", "yarn-error.log", ".vscode/", ".idea/",
    "*.sublime-project", "*.sublime-workspace", ".project", ".classpath",
    ".cproject", ".settings/", ".DS_Store", "Thumbs.db",
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.bmp", "*.tiff", "*.ico",
    "*.mp3", "*.wav", "*.ogg", "*.flac", "*.mp4", "*.avi", "*.mov",
    "*.mkv", "*.webm", "*.zip", "*.tar.gz", "*.rar", "*.7z", "*.iso",
    "*.pdf", "*.doc", "*.docx", "*.ppt", "*.pptx", "*.xls", "*.xlsx",
    "*.o", "*.so", "*.dll", "*.exe", "*.app", "*.jar", "*.war",
    "*.sqlite", "*.db", "*.mdb", "*.accdb", "*.log", "*.lock",
]
MAX_FILE_SIZE_MB = 10
RECENT_FOLDERS_FILE = Path.home() / ".repopacker_recent.txt"
MAX_RECENT_ENTRIES = 10
DEFAULT_BINARY_SAMPLE_SIZE = 1024

# --- Helper Functions ---
async def is_binary_heuristic(filepath: Path, sample_size: int = DEFAULT_BINARY_SAMPLE_SIZE, file_reader: Optional[AsyncFileReader] = None) -> bool:
    """
    Asynchronously checks if a file is likely binary by looking for null bytes.

    Reads a sample from the beginning of the file. If a null byte is found
    within the sample, the file is considered binary. Also handles basic
    path validation and file I/O errors.

    Args:
        filepath (Path): The path to the file to check.
        sample_size (int): The number of bytes to read from the file for the check.

    Returns:
        bool: True if the file is likely binary or an error occurs, False otherwise.
    """
    if not isinstance(filepath, Path):
        # Using print as self.app.log is not available in global functions.
        print("TypeError: filepath must be a Path object in is_binary_heuristic.")
        return True # Treat as binary/unreadable if type is wrong
    
    if not filepath.is_file(): 
        # Using print as self.app.log is not available in global functions.
        print(f"Warning: {filepath} is not a file or doesn't exist in is_binary_heuristic.")
        return True # Treat as binary/unreadable if not a file

    current_reader = file_reader if file_reader else DefaultAsyncFileReader()

    try:
        sample = await current_reader.read_sample(filepath, sample_size)
        return b'\0' in sample
    except (OSError, IOError) as e: 
        # Using print as self.app.log is not available in global functions.
        # This catches errors from the file_reader or other OS/IO issues.
        print(f"Error during binary check for {filepath}: {e}")
        return True

async def get_file_size_mb(filepath: Path) -> float:
    """
    Asynchronously gets the size of a file in megabytes (MB).

    Handles basic path validation and file I/O errors, returning float('inf')
    in case of issues.

    Args:
        filepath (Path): The path to the file.

    Returns:
        float: The file size in MB, or float('inf') if an error occurs.
    """
    if not isinstance(filepath, Path):
        # Using print as self.app.log is not available in global functions.
        print("TypeError: filepath must be a Path object in get_file_size_mb.")
        return float('inf') # Return 'infinite' size if type is wrong
    
    # Consider if an async check for is_file is needed. For now, Path.is_file() is sync.
    if not filepath.is_file():
        # Using print as self.app.log is not available in global functions.
        print(f"Warning: {filepath} is not a file or doesn't exist in get_file_size_mb.")
        return float('inf') # Return 'infinite' size if not a file

    try:
        stat_result = await aios.stat(filepath)
        return stat_result.st_size / (1024 * 1024)
    except OSError as e: # aios.stat can raise OSError
        # Using print as self.app.log is not available in global functions.
        print(f"Error getting size for {filepath}: {e}")
        return float('inf')

async def load_recent_folders() -> List[Path]:
    """
    Asynchronously loads a list of recently used folder paths from a predefined file.

    Reads paths from RECENT_FOLDERS_FILE, filters for valid directories,
    and returns up to MAX_RECENT_ENTRIES. Handles file errors gracefully.

    Returns:
        List[Path]: A list of Path objects for recent folders, or an empty list
                    if the file doesn't exist or an error occurs.
    """
    try:
        async with aiofiles.open(RECENT_FOLDERS_FILE, "r", encoding="utf-8") as f:
            lines = await f.readlines()
        # Synchronous processing of lines is fine here
        return [Path(line.strip()) for line in lines if Path(line.strip()).is_dir()][:MAX_RECENT_ENTRIES]
    except FileNotFoundError:
        # Using print as self.app.log is not available in global functions.
        print(f"Recent folders file not found: {RECENT_FOLDERS_FILE}")
        return []
    except IOError as e:
        # Using print as self.app.log is not available in global functions.
        print(f"Error loading recent folders: {e}")
        return []

async def save_recent_folders(folder_path: Path, current_list: List[Path]):
    """
    Asynchronously saves a new folder path to the list of recent folders.

    Prepends the new path to the current list, removes duplicates,
    and writes up to MAX_RECENT_ENTRIES back to RECENT_FOLDERS_FILE.
    Handles file I/O errors gracefully.

    Args:
        folder_path (Path): The new folder path to add.
        current_list (List[Path]): The current list of recent folder paths.
    """
    new_list = [folder_path] + [p for p in current_list if p != folder_path]
    try:
        async with aiofiles.open(RECENT_FOLDERS_FILE, "w", encoding="utf-8") as f:
            for p in new_list[:MAX_RECENT_ENTRIES]:
                await f.write(str(p) + "\n")
    except IOError as e: # aiofiles.open can also raise IOError or OSError
        # Using print as self.app.log is not available in global functions.
        print(f"Error saving recent folders: {e}")


class CheckableDirectoryTree(DirectoryTree):
    """
    A DirectoryTree that allows for nodes (files/directories) to be individually
    selected or deselected. It respects various ignore mechanisms including
    .gitignore files, default patterns, and user-provided additional patterns.
    It also handles path security to prevent access outside the project root.
    """
    BINDINGS = [
        Binding("enter", "toggle_expand_or_select", "Toggle Expand/Select", show=False, priority=True),
    ]

    class SelectionChanged(Message):
        def __init__(self, selected_paths: Set[Path], project_root: Path) -> None:
            super().__init__()
            self.selected_paths = selected_paths
            self.project_root = project_root

    def __init__(self, path: str, id: Optional[str] = None, ignored_patterns: Optional[List[str]] = None):
        """
        Initialize the CheckableDirectoryTree.

        Args:
            path (str): The root path of the directory tree.
            id (Optional[str], optional): The ID of the widget. Defaults to None.
            ignored_patterns (Optional[List[str]], optional): A list of additional
                fnmatch patterns to ignore. Defaults to None.
        
        Raises:
            ValueError: If path is not a string, is empty, or does not point to a valid directory.
        """
        if not isinstance(path, str):
            raise ValueError("path must be a string.")
        if not path:
            raise ValueError("path cannot be empty.")
        
        super().__init__(path, id=id)
        self.selected_paths: Set[Path] = set()
        self.project_root = Path(path).resolve()
        if not self.project_root.is_dir():
            # Or, if an app instance is reliably available: self.app.log.error(...) and set a failed state.
            raise ValueError(f"Project path {self.project_root} is not a valid directory.")
        self._gitignore_matchers: dict[Path, Optional[callable]] = {}
        self.additional_ignored_patterns = ignored_patterns or []

    def _validate_path_security(self, path_to_check: Path, base_path: Path) -> bool:
        """
        Validates if path_to_check is securely within base_path.
        Resolves both to absolute paths and uses is_relative_to.
        Returns True if safe, False otherwise (e.g., different drive, outside base).
        """
        try:
            path_to_check_resolved = path_to_check.resolve()
            base_path_resolved = base_path.resolve()
            # A path is also considered "relative to" itself.
            return path_to_check_resolved.is_relative_to(base_path_resolved) or path_to_check_resolved == base_path_resolved
        except ValueError:  # Handles cases like paths on different drives for is_relative_to
            return False
        except OSError as e: 
            # This can happen if path_to_check.resolve() fails (e.g. file doesn't exist, an issue on some OS)
            # We can log this, but for security purposes, if resolution fails, consider it insecure.
            log_method = getattr(self.app, "log", print) # Use app.log if available, else print
            log_method(f"Security validation OSError for {path_to_check} or {base_path}: {e}")
            return False

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        """
        Filters an iterable of paths, yielding only those not ignored by any rule.

        This method is typically used by the DirectoryTree's loading mechanism
        to decide which paths to display.

        Args:
            paths (Iterable[Path]): An iterable of Path objects to filter.

        Yields:
            Path: Paths that are not ignored.
        """
        for path_obj in paths:
            if not self._is_path_ignored(path_obj): yield path_obj

    def _is_ignored_by_default_patterns(self, path_to_check: Path) -> bool:
        """Checks if the path is ignored by default patterns."""
        # path_to_check is assumed to be an absolute path
        for pattern_str in DEFAULT_IGNORES:
            if pattern_str.endswith('/'):
                dir_name_to_ignore = pattern_str.rstrip('/')
                # Check if any part of the path is this directory name, or if the path itself is this directory
                if dir_name_to_ignore in path_to_check.parts or \
                   (path_to_check.is_dir() and path_to_check.name == dir_name_to_ignore):
                    return True
            elif fnmatch.fnmatch(path_to_check.name, pattern_str):
                return True
        return False

    def _is_ignored_by_additional_patterns(self, path_to_check: Path) -> bool:
        """Checks if the path is ignored by additional (user-defined) patterns."""
        # path_to_check is assumed to be an absolute path
        for pattern_str in self.additional_ignored_patterns:
            if pattern_str.endswith('/'):
                dir_name_to_ignore = pattern_str.rstrip('/')
                if dir_name_to_ignore in path_to_check.parts or \
                   (path_to_check.is_dir() and path_to_check.name == dir_name_to_ignore):
                    return True
            elif fnmatch.fnmatch(path_to_check.name, pattern_str):
                return True
        return False

    def _is_ignored_by_gitignore(self, path_to_check: Path) -> bool:
        """Checks if the path is ignored by .gitignore files."""
        # path_to_check is assumed to be an absolute path
        path_to_check_str = str(path_to_check)
        
        # Determine directories to check for .gitignore files
        # Start from the parent of path_to_check and go up to project_root
        dirs_to_check_for_gitignore = []
        current_dir = path_to_check.parent
        while current_dir != self.project_root.parent and current_dir != current_dir.parent: # Second condition for safety against root
            if current_dir == self.project_root or self.project_root.is_relative_to(current_dir): # current_dir is project_root or an ancestor
                 dirs_to_check_for_gitignore.append(current_dir)
            if not self._validate_path_security(current_dir, self.project_root) or current_dir == self.project_root:
                break # Stop if outside project root or at project root
            current_dir = current_dir.parent
        if self.project_root not in dirs_to_check_for_gitignore: # Ensure project_root is always checked
            dirs_to_check_for_gitignore.append(self.project_root)

        for gitignore_dir_path in reversed(dirs_to_check_for_gitignore): # Check from root downwards
            matcher = self._gitignore_matchers.get(gitignore_dir_path)
            if matcher is None:
                gitignore_file_path = gitignore_dir_path / ".gitignore"
                if gitignore_file_path.is_file() and self._validate_path_security(gitignore_file_path, self.project_root):
                    try: 
                        matcher = gitignore_parser.parse_gitignore(str(gitignore_file_path), base_dir=str(gitignore_dir_path))
                    except IOError as e: 
                        self.app.log(f"Warning: IOError reading gitignore {gitignore_file_path}: {e}"); matcher = lambda p: False
                    except Exception as e: 
                        self.app.log(f"Warning: Failed to parse gitignore {gitignore_file_path}: {e}"); matcher = lambda p: False
                else: 
                    matcher = lambda p: False # No .gitignore file or not secure to access
                self._gitignore_matchers[gitignore_dir_path] = matcher
            
            if callable(matcher) and matcher(path_to_check_str):
                return True
        return False

    def _is_path_ignored(self, path_arg: Path) -> bool:
        """
        Determines if a given path should be ignored by any of the active ignore mechanisms.

        This method first ensures the path is secure (within the project root).
        Then, it delegates to helper methods to check against:
        1. Default ignore patterns.
        2. Additional (user-defined) ignore patterns.
        3. .gitignore rules.

        Args:
            path_arg (Path): The path to check. Can be relative or absolute.

        Returns:
            bool: True if the path should be ignored, False otherwise.
        """
        # Ensure path_arg is absolute for consistent processing
        # Using 'resolved_path' as it's the definitively processed, absolute version of path_arg
        resolved_path = path_arg.resolve() if not path_arg.is_absolute() else path_arg

        # --- Security Check ---
        # First, ensure the path is even allowed to be considered.
        if not self._validate_path_security(resolved_path, self.project_root):
            # self.app.log(f"Path {resolved_path} is outside project root {self.project_root}. Ignoring for security.")
            return True
        
        # --- Ignore Rule Checks ---
        # The original is_relative_to check that was here previously is now fully covered by _validate_path_security.
        # _validate_path_security ensures resolved_path is either self.project_root or a descendant.

        # Check against built-in default ignore patterns.
        if self._is_ignored_by_default_patterns(resolved_path):
            return True
        
        # Check against user-provided additional ignore patterns.
        if self._is_ignored_by_additional_patterns(resolved_path):
            return True

        # Check against .gitignore rules found in the project.
        if self._is_ignored_by_gitignore(resolved_path):
            return True
            
        # If none of the above ignore conditions are met, the path is not ignored.
        return False

    def _is_node_effectively_selected_file(self, file_path: Path) -> bool:
        """
        Synchronously determines if a file node is considered for packing based on
        selection state and ignore patterns.

        This method is suitable for UI rendering purposes (e.g., the `(pack)` indicator).
        It checks if:
        1. The file is not ignored by any rules (`_is_path_ignored`).
        2. Either the file itself is directly in `self.selected_paths`, or one of its
           parent directories (up to the project root) is in `self.selected_paths`.

        Note: This method *does not* check for binary content or file size.
        The authoritative filtering for packing, including binary and size checks,
        is handled asynchronously by `get_selected_final_files`.

        Args:
            file_path (Path): The absolute path to the file to check.

        Returns:
            bool: True if the file is considered selected for UI purposes, False otherwise.
        """
        if self._is_path_ignored(file_path):  # Synchronous check
            return False
        
        # Check if the file itself or any of its relevant parents are in selected_paths
        if file_path in self.selected_paths:
            return True
        
        current_parent = file_path.parent
        # Traverse upwards to the project root
        while current_parent != self.project_root.parent and \
              (current_parent == self.project_root or current_parent.is_relative_to(self.project_root)):
            if current_parent in self.selected_paths:
                return True # Parent directory is selected
            if current_parent == self.project_root:
                break # Reached project root
            
            prev_parent = current_parent
            current_parent = current_parent.parent
            if current_parent == prev_parent: # Safety break for potential issues at filesystem root
                break
        return False

    def render_label(
        self, node: TreeNode[DirEntry], base_style: Style, style: Style
    ) -> Text:
        """
        Renders the label for a node in the DirectoryTree.

        Adds a checkbox (`✓` or ` `) prefix to indicate selection status.
        Also appends a `(pack)` indicator if the file node is effectively
        selected for packing.

        Args:
            node (TreeNode[DirEntry]): The tree node to render.
            base_style (Style): The base style for the label.
            style (Style): The specific style for this node.

        Returns:
            Text: The rendered label with selection indicators.
        """
        rendered_label_from_super = super().render_label(node, base_style, style)
        if node.data is None: return Text("Error: No data") 
        node_fs_path = Path(node.data.path)
        is_directly_selected = node_fs_path in self.selected_paths
        prefix_text = Text.from_markup("[green]✓[/] " if is_directly_selected else "[dim]][ ] [/]")
        final_renderable = Text("")
        final_renderable.append(prefix_text)
        final_renderable.append(rendered_label_from_super)
        if node_fs_path.is_file() and self._is_node_effectively_selected_file(node_fs_path):
            final_renderable.append_text(Text(" [b #40E0D0](pack)[/b]", no_wrap=True))
        return final_renderable

    def _toggle_single_node_selection(self, node_fs_path: Path):
        """
        Toggles the selection state of a single node (file or directory).

        If the node is already selected, it's deselected. If it's not selected,
        it's added to the selection set, unless it's an ignored path.
        Refreshes the tree and posts a SelectionChanged message.

        Args:
            node_fs_path (Path): The absolute path of the node to toggle.
        """
        if node_fs_path in self.selected_paths:
            self.selected_paths.discard(node_fs_path)
        else:
            if self._is_path_ignored(node_fs_path):
                self.app.bell(); return
            self.selected_paths.add(node_fs_path)
        self.refresh()
        self.post_message(self.SelectionChanged(self.selected_paths.copy(), self.project_root))

    def _toggle_node_and_children_selection(self, node_fs_path: Path):
        """
        Toggles the selection state of a node and all its children recursively.

        If the node is a file and is ignored (and not already selected to be deselected),
        the operation is aborted with a bell. Otherwise, determines the new selection
        state (select if currently deselected, deselect if currently selected) and
        applies it recursively to the node and its children.
        Refreshes the tree and posts a SelectionChanged message.

        Args:
            node_fs_path (Path): The absolute path of the node to toggle along with its children.
        """
        if node_fs_path.is_file() and self._is_path_ignored(node_fs_path) and not (node_fs_path in self.selected_paths):
            self.app.bell(); return # Don't select an ignored file unless it's to deselect it.
        new_select_state = not (node_fs_path in self.selected_paths)
        self._apply_selection_recursive(node_fs_path, new_select_state)
        self.refresh()
        self.post_message(self.SelectionChanged(self.selected_paths.copy(), self.project_root))

    def _apply_selection_recursive(self, node_fs_path: Path, select_state: bool):
        """
        Recursively applies the selection state to a node and its children.

        If `select_state` is True, attempts to add the node and its children to
        the selection set (unless they are ignored).
        If `select_state` is False, removes the node and its children from the
        selection set.

        Args:
            node_fs_path (Path): The absolute path of the node to start applying
                                 the selection state from.
            select_state (bool): True to select, False to deselect.
        """
        if select_state:
            if not self._is_path_ignored(node_fs_path): self.selected_paths.add(node_fs_path)
        else: self.selected_paths.discard(node_fs_path)
        
        if node_fs_path.is_dir():
            try:
                for child_item_path in node_fs_path.iterdir(): # Iterate over children
                    self._apply_selection_recursive(child_item_path, select_state)
            except OSError as e: self.app.log(f"OS Error applying recursive selection for {node_fs_path}: {e}")

    def action_toggle_expand_or_select(self) -> None:
        """
        Action bound to the 'enter' key.
        Toggles expansion for directory nodes or toggles selection for file nodes.
        """
        if self.cursor_node and self.cursor_node.data:
            node_fs_path = Path(self.cursor_node.data.path)
            if node_fs_path.is_dir():
                self.action_toggle_node()
            elif node_fs_path.is_file():
                self._toggle_single_node_selection(node_fs_path)
        
    def on_directory_tree_node_selected(self, event: DirectoryTree.NodeSelected) -> None: # Click or Space
        """
        Event handler for when a node is selected via a click or spacebar.
        Toggles the selection state of the clicked/focused node.
        """
        event.stop()
        if event.node.data is None: return
        self._toggle_single_node_selection(Path(event.node.data.path))
        
    async def on_key(self, event: events.Key) -> None:
        key_handled_by_us = False
        if event.key == "j":
            if self.cursor_node is not None:
                self.action_cursor_down()
            key_handled_by_us = True
        elif event.key == "k":
            if self.cursor_node is not None:
                self.action_cursor_up()
            key_handled_by_us = True
        elif event.key in ("h", "l"):
            key_handled_by_us = True
        
        if key_handled_by_us:
            event.prevent_default()

    async def get_selected_final_files(self) -> List[Path]:
        """
        Asynchronously collects all files that are effectively selected for packing.

        This method iterates through all user-selected paths. For selected files,
        it checks if they are valid for packing (not ignored, not binary, within size limits).
        For selected directories, it recursively finds all valid files within them.
        Paths are returned as relative to the project root.

        Returns:
            List[Path]: A sorted list of Path objects relative to the project root,
                        representing all files to be packed.
        """
        collected_files: Set[Path] = set()
        # Keep track of files already processed to avoid duplicates if a file and its parent are both selected.
        already_processed_for_collection: Set[Path] = set()
        
        # First pass: process directly selected files
        for path_obj in self.selected_paths: # path_obj is absolute here
            if path_obj.is_file() and path_obj not in already_processed_for_collection:
                if not self._validate_path_security(path_obj, self.project_root):
                    self.app.log(f"Security: Path {path_obj} is outside project root {self.project_root} and will be skipped during file collection.")
                    already_processed_for_collection.add(path_obj)
                    continue
                # Await async helper functions
                is_bin = await is_binary_heuristic(path_obj) # Check if binary
                size_mb = await get_file_size_mb(path_obj) # Check file size
                if not self._is_path_ignored(path_obj) and \
                   not is_bin and \
                   size_mb <= MAX_FILE_SIZE_MB:
                    collected_files.add(path_obj) # Add if valid and not ignored
                already_processed_for_collection.add(path_obj)
        
        # Second pass: process files within selected directories
        for path_obj in self.selected_paths: # path_obj is absolute here
            if path_obj.is_dir():
                if not self._validate_path_security(path_obj, self.project_root):
                    self.app.log(f"Security: Directory {path_obj} is outside project root {self.project_root} and its contents will be skipped.")
                    continue
                try:
                    # Recursively glob for all files within the directory
                    for item in path_obj.rglob("*"): # item is also an absolute path
                        if item.is_file() and item not in already_processed_for_collection:
                            if not self._validate_path_security(item, self.project_root):
                                self.app.log(f"Security: Path {item} (from rglob under {path_obj}) is outside project root {self.project_root} and will be skipped.")
                                already_processed_for_collection.add(item)
                                continue
                            # Await async helper functions for each file found
                            is_bin_item = await is_binary_heuristic(item)
                            size_mb_item = await get_file_size_mb(item)
                            if not self._is_path_ignored(item) and \
                               not is_bin_item and \
                               size_mb_item <= MAX_FILE_SIZE_MB:
                                collected_files.add(item) # Add if valid and not ignored
                            already_processed_for_collection.add(item)
                except OSError as e: self.app.log(f"OS Error rglobbing {path_obj}: {e}")
        
        relative_collected_files = set()
        if self.project_root:
            for abs_file_path in sorted(list(collected_files)): 
                # Path security already validated before adding to collected_files.
                # This final validation is a safeguard.
                if not self._validate_path_security(abs_file_path, self.project_root):
                    self.app.log(f"Security: Path {abs_file_path} was in collected_files but failed final validation before relative conversion. Skipping.")
                    continue
                try:
                    relative_collected_files.add(abs_file_path.relative_to(self.project_root))
                except ValueError: 
                    # This might happen if, despite validation, abs_file_path is not under project_root (e.g. symlink complexity not fully handled by resolve/is_relative_to in all cases)
                    self.app.log(f"ValueError making {abs_file_path} relative to {self.project_root}. This might indicate an edge case in path validation.")
        return sorted(list(relative_collected_files))

class PathInputScreen(ModalScreen[Optional[Path]]):
    """
    A modal screen that prompts the user to select or input a project directory path.
    It displays a list of recently used folders for quick selection and an input
    field for manual path entry.
    """
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, recent_folders: List[Path]):
        """
        Initialize the PathInputScreen.

        Args:
            recent_folders (List[Path]): A list of recently used folder paths
                                         to display for quick selection.
        """
        super().__init__()
        self.recent_folders = recent_folders
        self.input_widget: Optional[Input] = None 

    def compose(self) -> ComposeResult:
        """
        Composes the UI for the PathInputScreen.
        
        Includes a title, a scrollable list of recent folders (if any),
        an input field for path entry, and OK/Cancel buttons.
        """
        with Vertical(id="dialog"):
            yield Label("Select or Enter Project Path:", classes="dialog_title")
            if self.recent_folders:
                yield Label("Recent folders (press number or use Up/Down then Enter in input):")
                with ScrollableContainer(id="recent_list"):
                    for i, folder in enumerate(self.recent_folders):
                        yield Button(f"{i+1}. {str(folder)}", id=f"recent_{i}", classes="recent_folder_button")
            self.input_widget = Input(placeholder="Type path or press number for recent (then Enter)", id="path_input")
            yield self.input_widget
            with Horizontal(classes="dialog_buttons"):
                yield Button("OK", variant="primary", id="ok_button")
                yield Button("Cancel", variant="error", id="cancel_button")

    async def on_mount(self) -> None:
        if self.input_widget:
            self.input_widget.focus()

    async def on_key(self, event: events.Key) -> None:
        # self.app.log(f"PathInputScreen on_key: {event.key}, input_focused: {self.input_widget.has_focus if self.input_widget else 'N/A'}") # Debugging
        
        # Check if a digit 1-9 is pressed (for recent folders up to 9)
        # and ensure the main input widget itself is not focused to avoid interference.
        if event.key.isdigit() and '0' not in event.key: # Allow 1-9
            if self.input_widget and not self.input_widget.has_focus:
                try:
                    index = int(event.key) - 1
                    if 0 <= index < len(self.recent_folders) and index < 9: # Limit to 9 recent items by direct key press
                        self.dismiss(self.recent_folders[index].resolve())
                        event.prevent_default() 
                        return
                except ValueError:
                    pass 
        # Allow other keys to propagate for default handling by focused widget or screen bindings
        

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """
        Handles the submission of text from the path input field.

        If the input is a number, it attempts to select a recent folder by index.
        Otherwise, it resolves the input string as a path. If valid and a directory,
        it dismisses the screen with the selected path. If invalid, it shows an error
        placeholder and clears the input.

        Args:
            event (Input.Submitted): The input submission event.
        """
        path_str = event.value.strip()
        if path_str.isdigit(): # Check if input is a number for recent folder selection
            try:
                index = int(path_str) - 1
                if 0 <= index < len(self.recent_folders):
                    self.dismiss(self.recent_folders[index].resolve())
                    return
            except ValueError:
                pass 
        
        if path_str:
            path = Path(path_str).resolve()
            if path.is_dir():
                self.dismiss(path)
            else:
                if self.input_widget:
                    self.input_widget.placeholder = "Invalid path/number. Try again."
                    self.input_widget.value = ""
                self.app.bell()
        else: 
            self.app.bell()


    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok_button":
            if self.input_widget:
                await self.on_input_submitted(Input.Submitted(self.input_widget, self.input_widget.value))
        elif event.button.id == "cancel_button":
            self.dismiss(None)
        elif event.button.id and event.button.id.startswith("recent_"):
            try:
                index = int(event.button.id.split("_")[1])
                if 0 <= index < len(self.recent_folders):
                    self.dismiss(self.recent_folders[index].resolve())
            except (ValueError, IndexError):
                self.app.bell()
                
    def action_cancel(self) -> None:
        """
        Action to cancel the path input, dismissing the modal screen with None.
        Typically bound to the 'escape' key.
        """
        self.dismiss(None)


class RepoPackerApp(App[None]):
    """
    The main Textual application class for RepoPacker TUI.
    It orchestrates the UI, including the file tree, sidebar for selected files,
    status bar, and user interactions for selecting a project, files, and
    generating a packed output for AI prompts.
    """
    TITLE = "Repo Packer TUI"; CSS_PATH = None
    CSS = """
    Screen { layout: vertical; overflow-y: auto; }
    Header { height: auto; } Footer { height: auto; }
    #app_body { layout: horizontal; height: 1fr; }
    #tree_panel { width: 2fr; height: 100%; }
    #sidebar_panel { width: 1fr; height: 100%; border-left: wide $primary-lighten-2; padding: 0 1; }
    #sidebar_panel Markdown { width: 100%; height: 100%; overflow-y: auto;}
    CheckableDirectoryTree { border: round $primary; width: 100%; height: 100%; margin: 1 0; }
    #tree_placeholder { padding: 1; color: $text-muted; width: 100%; height: 100%; content-align: center middle; }
    #status_bar { dock: bottom; width: 100%; height: 1; padding: 0 1; background: $primary-background; color: $text; }
    #dialog { align: center middle; width: 80%; max-width: 70; height: auto; max-height: 80%; border: thick $primary; background: $surface; padding: 1; }
    .dialog_title { width: 100%; text-align: center; margin-bottom: 1; }
    #recent_list { max-height: 10; overflow-y: auto; border: round $primary-lighten-2; margin-bottom:1; }
    #recent_list Button.recent_folder_button { width: 100%; margin-bottom: 1;}
    Input { margin-bottom: 1; }
    .dialog_buttons { width: 100%; align-horizontal: right; margin-top:1; }
    .dialog_buttons Button { margin-left: 1; }
    """
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True, priority=True),
        Binding("f5", "open_folder", "Open Folder", show=False), 
        Binding("c", "generate_packed_file", "Copy Prompt", show=True),
        Binding("a", "select_all_tree", "Select All (Project)", show=True),
        Binding("d", "deselect_all_tree", "Deselect All (Project)", show=True),
        Binding("ctrl+a", "select_in_focused_folder", "Sel Content (Dir)", show=True),
        Binding("ctrl+d", "deselect_in_focused_folder", "Desel Content (Dir)", show=True),
        Binding("ctrl+backslash", "command_palette", "Palette", show=False, key_display="Ctrl+\\"),
        Binding("f1", "toggle_dark", "Dark/Light", show=False),
        Binding("question_mark", "app.help", "Help", show=False),
    ]
    current_project_path: reactive[Optional[Path]] = reactive(None)
    status_message = reactive("Ready. F5 to Open, 'c' to Copy Prompt.")

    def __init__(self, initial_path: Optional[Path] = None):
        """
        Initialize the RepoPackerApp.

        Args:
            initial_path (Optional[Path], optional): An optional path to load
                                                     when the app starts. Defaults to None.
        """
        super().__init__()
        self.arg_initial_path = initial_path.resolve() if initial_path and initial_path.exists() else None
        self.recent_folders: List[Path] = [] # Initialize as empty, will be loaded in on_mount

    def compose(self) -> ComposeResult:
        """
        Composes the main UI layout for the application.

        Includes a Header, a main body with a tree panel and a sidebar,
        a status bar, and a Footer.
        """
        yield Header()
        with Horizontal(id="app_body"):
            with Vertical(id="tree_panel"): pass # Tree will be mounted here by watch_current_project_path
            with ScrollableContainer(id="sidebar_panel"):
                yield Markdown("### Selected Files\n\n_None selected_", id="selected_files_md")
        yield Static(self.status_message, id="status_bar")
        yield Footer()

    async def on_mount(self) -> None:
        """
        Coroutine called when the application is mounted.
        Loads recent folders, sets an initial project path if provided via CLI,
        or opens the folder selection dialog. Also initializes the sidebar.
        """
        self.recent_folders = await load_recent_folders() # Load recent folders here
        initial_path_set = False
        if self.arg_initial_path and self.arg_initial_path.is_dir():
            self.current_project_path = self.arg_initial_path
            initial_path_set = True
        else:
            if self.arg_initial_path:
                 self.notify(f"Initial path '{self.arg_initial_path}' not valid.", severity="warning", timeout=4)
        
        if not initial_path_set: await self.action_open_folder()
        
        try:
            if self.query(CheckableDirectoryTree):
                tree_instance_list = self.query(CheckableDirectoryTree)
                if tree_instance_list:
                    tree_instance = tree_instance_list.first()
                    if tree_instance.project_root:
                        tree_instance.post_message(CheckableDirectoryTree.SelectionChanged(set(), tree_instance.project_root))
            else: self.query_one("#selected_files_md", Markdown).update("### Selected Files\n\n_None selected_")
        except Exception as e: self.log(f"Error in on_mount sidebar clearing: {e}")


    def watch_current_project_path(self, old_path: Optional[Path], new_path: Optional[Path]) -> None:
        """
        Reactive watcher method called when `self.current_project_path` changes.

        Clears the existing directory tree and selected files display.
        If `new_path` is a valid directory, it creates and mounts a new
        `CheckableDirectoryTree` for that path, updates the app's sub_title,
        and saves the path to recent folders.
        If `new_path` is None or invalid, it displays a placeholder.

        Args:
            old_path (Optional[Path]): The previous project path.
            new_path (Optional[Path]): The new project path.
        """
        try: tree_panel = self.query_one("#tree_panel"); tree_panel.remove_children()
        except NoMatches: self.log("Error: #tree_panel not found during watch"); return
        try: self.query_one("#selected_files_md", Markdown).update("### Selected Files\n\n_None selected_")
        except NoMatches: pass

        if new_path and new_path.is_dir():
            tree = CheckableDirectoryTree(str(new_path), id="dir_tree")
            tree_panel.mount(tree)
            self.call_after_refresh(tree.focus)

            # Schedule save_recent_folders to run in the background
            asyncio.create_task(save_recent_folders(new_path, self.recent_folders))
            # self.recent_folders is loaded in on_mount. If immediate refresh after save is needed,
            # save_recent_folders could return the new list or an event could be posted.
            # For now, removing synchronous load_recent_folders from here.
            self.sub_title = str(new_path); self.status_message = f"Project: {new_path.name}. Select items. 'c' to Copy Prompt."
        else:
            tree_panel.mount(Static("No project loaded. Open a folder to begin (F5).", id="tree_placeholder"))
            self.sub_title = "No Project"; self.status_message = "No project. Open (F5), Copy Prompt ('c')."
            if old_path and not new_path: self.notify("Folder selection cancelled or failed.", severity="warning")

    def watch_status_message(self, new_message: str) -> None:
        try: self.query_one("#status_bar", Static).update(new_message)
        except NoMatches: pass

    async def on_checkable_directory_tree_selection_changed(self, event: CheckableDirectoryTree.SelectionChanged) -> None:
        """
        Event handler for when the selection in the CheckableDirectoryTree changes.

        Updates the sidebar Markdown widget to display the list of currently
        selected packable files.

        Args:
            event (CheckableDirectoryTree.SelectionChanged): The selection change event,
                                                             containing the set of selected paths.
        """
        try:
            md_widget = self.query_one("#selected_files_md", Markdown)
            tree = self.query_one(CheckableDirectoryTree)
            final_packable_files = await tree.get_selected_final_files() # Await here
            if not final_packable_files:
                md_widget.update("### Selected Files\n\n_No packable files based on current selection._")
                return
            display_items = [f"- `{rel_path.as_posix()}`" for rel_path in sorted(final_packable_files)]
            title = f"### Selected Files ({len(display_items)})"
            md_content = title + "\n\n" + "\n".join(display_items)
            md_widget.update(md_content)
        except NoMatches: self.log("Error: #selected_files_md or CheckableDirectoryTree widget not found for sidebar update.")
        except Exception as e: self.log(f"Error updating sidebar on selection change: {e}")

    async def action_open_folder(self) -> None:
        """
        Action to open the folder selection dialog (PathInputScreen).
        
        The callback `set_new_path` will update `self.current_project_path`
        if a valid path is selected.
        """
        def set_new_path(path: Optional[Path]):
            if path: self.current_project_path = path
            elif not self.current_project_path: self.notify("No folder selected.", severity="information")
        await self.app.push_screen(PathInputScreen(self.recent_folders), set_new_path)

    def action_select_all_tree(self) -> None:
        """
        Action to select all items in the project tree.
        
        It recursively marks the project root and all its children as selected,
        then updates the status message.
        """
        try:
            tree = self.query_one(CheckableDirectoryTree)
            if tree.project_root:
                 tree._apply_selection_recursive(tree.project_root, select_state=True)
            tree.refresh()
            tree.post_message(CheckableDirectoryTree.SelectionChanged(tree.selected_paths.copy(), tree.project_root))
            status_count = sum(1 for p in tree.selected_paths if not tree._is_path_ignored(p))
            self.status_message = f"{status_count} items directly marked. Review tree/sidebar."
        except NoMatches: self.status_message = "No project tree loaded."; self.bell()
        except Exception as e: self.status_message = f"Error selecting all: {e}"; self.log(f"Select All Error: {e}"); self.bell()

    def action_deselect_all_tree(self) -> None:
        """
        Action to deselect all items in the project tree.
        
        It recursively marks the project root and all its children as deselected,
        then updates the status message.
        """
        try:
            tree = self.query_one(CheckableDirectoryTree)
            if tree.project_root:
                 tree._apply_selection_recursive(tree.project_root, select_state=False)
            else: tree.selected_paths.clear()
            tree.refresh()
            tree.post_message(CheckableDirectoryTree.SelectionChanged(tree.selected_paths.copy(), tree.project_root))
            self.status_message = "All selections cleared."
        except NoMatches: self.status_message = "No project tree loaded to deselect from."; self.bell()

    def _operate_on_focused_folder_contents(self, select_state: bool):
        """
        Helper method to select or deselect the contents of the currently focused directory.

        If the focused item in the tree is a directory, this method recursively
        applies the given `select_state` to all its children.

        Args:
            select_state (bool): True to select contents, False to deselect.
        """
        try:
            tree = self.query_one(CheckableDirectoryTree)
            if tree.cursor_node and tree.cursor_node.data:
                node_fs_path = Path(tree.cursor_node.data.path)
                if node_fs_path.is_dir():
                    tree._apply_selection_recursive(node_fs_path, select_state=select_state)
                    tree.refresh()
                    tree.post_message(CheckableDirectoryTree.SelectionChanged(tree.selected_paths.copy(), tree.project_root))
                    action = "Selected" if select_state else "Deselected"
                    self.status_message = f"{action} contents of '{node_fs_path.name}'."
                else:
                    self.status_message = "Focused item is not a directory."
                    self.bell() 
            else:
                self.status_message = "No item focused in the tree."
                self.bell()
        except NoMatches:
            self.status_message = "No project tree loaded."
            self.bell()

    def action_select_in_focused_folder(self) -> None:
        """
        Action to select the contents of the currently focused directory.
        Delegates to `_operate_on_focused_folder_contents` with `select_state=True`.
        """
        self._operate_on_focused_folder_contents(select_state=True)

    def action_deselect_in_focused_folder(self) -> None:
        """
        Action to deselect the contents of the currently focused directory.
        Delegates to `_operate_on_focused_folder_contents` with `select_state=False`.
        """
        self._operate_on_focused_folder_contents(select_state=False)

    async def action_generate_packed_file(self) -> None: # "Copy Prompt"
        """
        Asynchronously generates a packed representation of the selected repository contents
        and copies it to the clipboard.

        The output format includes:
        1.  A `<file_summary>` XML-like block with metadata about the packed content.
        2.  A `<directory_structure>` block listing the relative paths of all included files.
        3.  A `<files>` block containing the actual content of each selected file,
            each wrapped in `<file path="relative/path/to/file">...</file>` tags.

        File reading is done asynchronously. Errors during file processing or clipboard
        operations are logged and notified to the user.
        """
        if not self.current_project_path:
            self.notify("Error: No project folder loaded.", severity="error", timeout=3); self.bell(); return
        try:
            tree = self.query_one(CheckableDirectoryTree)
            selected_relative_paths = await tree.get_selected_final_files() # Await here
        except NoMatches: 
            self.notify("Error: Project tree not found.", severity="error", timeout=3); self.bell(); return
        
        if not selected_relative_paths:
            self.notify("Warning: No files selected or eligible for packing.", severity="warning", timeout=3); self.bell(); return
        
        output_parts = []
        
        # --- Build File Summary Section ---
        output_parts.append("<file_summary>")
        output_parts.append("This section contains a summary of this file.")
        output_parts.append("") 
        output_parts.append("<purpose>")
        output_parts.append("This file contains a packed representation of selected repository contents.")
        output_parts.append("It is designed to be easily consumable by AI systems for analysis, code review,")
        output_parts.append("or other automated processes.")
        output_parts.append("</purpose>")
        output_parts.append("") 
        output_parts.append("<file_format>")
        output_parts.append("The content is organized as follows:")
        output_parts.append("1. This summary section")
        output_parts.append("2. Directory structure of selected files")
        output_parts.append("3. Selected repository files, each consisting of:")
        output_parts.append("  - File path as an attribute (relative to project root)")
        output_parts.append("  - Full contents of the file")
        output_parts.append("</file_format>")
        output_parts.append("") 
        output_parts.append("<usage_guidelines>")
        output_parts.append("- This file should be treated as read-only. Any changes should be made to the")
        output_parts.append("  original repository files, not this packed version.")
        output_parts.append("- When processing this file, use the file path to distinguish")
        output_parts.append("  between different files in the repository.")
        output_parts.append("- Be aware that this file may contain sensitive information. Handle it with")
        output_parts.append("  the same level of security as you would the original repository.")
        output_parts.append("</usage_guidelines>")
        output_parts.append("") 
        output_parts.append("<notes>")
        output_parts.append("- Files are selected based on user interaction and ignore rules.")
        output_parts.append("- Binary files (based on a heuristic) are excluded.")
        output_parts.append("- Files matching patterns in .gitignore (if present) and default ignore patterns (e.g., .git, __pycache__) are typically excluded from selection and packing.")
        output_parts.append(f"- File size limits may apply (currently >{MAX_FILE_SIZE_MB}MB excluded).")
        output_parts.append("</notes>")
        output_parts.append("") 
        output_parts.append("<additional_info>")
        output_parts.append(f"Generated by RepoPacker TUI from project: {self.current_project_path.name}")
        output_parts.append("</additional_info>")
        output_parts.append("</file_summary>")
        output_parts.append("") 
        
        # --- Build Directory Structure Section ---
        output_parts.append("<directory_structure>")
        for rel_path in selected_relative_paths: 
            output_parts.append(rel_path.as_posix()) 
        output_parts.append("</directory_structure>")
        output_parts.append("") 
        
        # --- Build File Contents Section ---
        output_parts.append("<files>")
        output_parts.append("This section contains the contents of the repository's selected files.")
        if selected_relative_paths:
             output_parts.append("") # Add a blank line if there are files to list

        files_processed_count = 0
        self.status_message = "Preparing content for clipboard..."
        await asyncio.sleep(0.01) # Allow UI to update status message

        # Iterate through selected files and append their content
        for i, rel_path in enumerate(selected_relative_paths):
            full_path = self.current_project_path / rel_path
            try:
                # Need to get the tree instance to call _validate_path_security
                tree = self.query_one(CheckableDirectoryTree)
                if not tree._validate_path_security(full_path, self.current_project_path):
                    self.log(f"Security: Skipping file {full_path} as it's outside project boundaries during packing.")
                    continue

                async with aiofiles.open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                    content = await f.read()
                normalized_rel_path = rel_path.as_posix()
                output_parts.append(f'<file path="{normalized_rel_path}">')
                output_parts.append(content)
                output_parts.append("</file>")
                if i < len(selected_relative_paths) - 1:
                    output_parts.append("") 
                files_processed_count += 1
            except (IOError, OSError) as e:
                self.log(f"Error reading file {full_path}: {e}")
                output_parts.append(f'<file path="{rel_path.as_posix()}">{os.linesep}Error reading file: {e}{os.linesep}</file>')
                if i < len(selected_relative_paths) - 1:
                    output_parts.append("")
            except NoMatches: # Handles error if self.query_one(CheckableDirectoryTree) fails
                self.log("Error: Could not find CheckableDirectoryTree instance for security validation during packing.")
                self.notify("Internal error: Tree not found for validation. Packing aborted.", severity="error", timeout=5)
                return # Abort packing if tree is not found
        output_parts.append("</files>")
        final_output_str = "\n".join(output_parts)

        try:
            pyperclip.copy(final_output_str)
            self.notify(f"{files_processed_count} files packed into prompt and copied to clipboard!", severity="information", timeout=4)
            self.status_message = "Prompt copied to clipboard."
        except pyperclip.PyperclipException as e:
            self.log(f"Clipboard copy error: {e}")
            self.notify("Error copying to clipboard. Is pyperclip installed and configured?", severity="error", timeout=5)
            self.status_message = "Clipboard error."
        except Exception as e: # Catch-all for any other unexpected errors during copy
            self.log(f"Unexpected error during prompt generation/copy (e.g. pyperclip internal): {e}")
            self.notify("An unexpected error occurred during copy. See logs.", severity="error", timeout=5)
            self.status_message = "Error during copy."


if __name__ == "__main__":
    import sys
    initial_folder = None
    if len(sys.argv) > 1:
        path_arg = Path(sys.argv[1])
        if path_arg.is_dir(): initial_folder = path_arg
        else: print(f"Warning: Provided path '{path_arg}' is not a valid directory. Starting without initial project.")
    app = RepoPackerApp(initial_path=initial_folder); app.run()
