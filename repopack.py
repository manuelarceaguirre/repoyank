#!/usr/bin/env python
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

import gitignore_parser

# --- Configuration ---
# ... (remains the same)
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

# --- Helper Functions ---
# ... (remains the same)
def is_binary_heuristic(filepath: Path, sample_size=1024) -> bool:
    try:
        with open(filepath, 'rb') as f: sample = f.read(sample_size)
        return b'\0' in sample
    except Exception: return True

def get_file_size_mb(filepath: Path) -> float:
    try: return filepath.stat().st_size / (1024 * 1024)
    except OSError: return float('inf')

def load_recent_folders() -> List[Path]:
    if not RECENT_FOLDERS_FILE.exists(): return []
    try:
        with open(RECENT_FOLDERS_FILE, "r", encoding="utf-8") as f:
            return [Path(line.strip()) for line in f if Path(line.strip()).is_dir()][:MAX_RECENT_ENTRIES]
    except Exception: return []

def save_recent_folders(folder_path: Path, current_list: List[Path]):
    new_list = [folder_path] + [p for p in current_list if p != folder_path]
    try:
        with open(RECENT_FOLDERS_FILE, "w", encoding="utf-8") as f:
            for p in new_list[:MAX_RECENT_ENTRIES]: f.write(str(p) + "\n")
    except Exception as e: print(f"Error saving recent folders: {e}")


class CheckableDirectoryTree(DirectoryTree):
    class SelectionChanged(Message):
        def __init__(self, selected_paths: Set[Path], project_root: Path) -> None:
            super().__init__()
            self.selected_paths = selected_paths
            self.project_root = project_root

    def __init__(self, path: str, id: Optional[str] = None, ignored_patterns: Optional[List[str]] = None):
        super().__init__(path, id=id)
        self.selected_paths: Set[Path] = set()
        self.project_root = Path(path).resolve()
        self._gitignore_matchers: dict[Path, Optional[callable]] = {}
        self.additional_ignored_patterns = ignored_patterns or []

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        for path_obj in paths:
            if not self._is_path_ignored(path_obj): yield path_obj

    def _is_path_ignored(self, path_obj: Path) -> bool:
        # ... (this method remains the same as the previous correctly working version)
        abs_path_obj = path_obj.resolve() if not path_obj.is_absolute() else path_obj
        try:
            if not abs_path_obj.is_relative_to(self.project_root) and abs_path_obj != self.project_root: return True
        except ValueError: return True
        for pattern_str in DEFAULT_IGNORES + self.additional_ignored_patterns:
            if pattern_str.endswith('/'):
                dir_name_to_ignore = pattern_str.rstrip('/')
                if dir_name_to_ignore in abs_path_obj.parts or \
                   (abs_path_obj.is_dir() and abs_path_obj.name == dir_name_to_ignore): return True
            elif fnmatch.fnmatch(abs_path_obj.name, pattern_str): return True
        path_to_check_str = str(abs_path_obj)
        dirs_to_check_for_gitignore = []
        temp_dir = abs_path_obj.parent
        while temp_dir != temp_dir.parent and (temp_dir.is_relative_to(self.project_root) or temp_dir == self.project_root):
            dirs_to_check_for_gitignore.append(temp_dir)
            if temp_dir == self.project_root: break
            temp_dir = temp_dir.parent
        if self.project_root not in dirs_to_check_for_gitignore and (self.project_root == abs_path_obj.parent or self.project_root == abs_path_obj):
             dirs_to_check_for_gitignore.append(self.project_root)
        for gitignore_dir in dirs_to_check_for_gitignore:
            matcher = self._gitignore_matchers.get(gitignore_dir)
            if matcher is None:
                gf_path = gitignore_dir / ".gitignore"
                if gf_path.is_file():
                    try: matcher = gitignore_parser.parse_gitignore(str(gf_path), base_dir=str(gitignore_dir))
                    except Exception as e: self.app.log(f"Warn: Parse {gf_path}: {e}"); matcher = lambda p: False
                else: matcher = lambda p: False
                self._gitignore_matchers[gitignore_dir] = matcher
            if callable(matcher) and matcher(path_to_check_str): return True
        return False


    def render_node(self, node: TreeNode[DirEntry]) -> str:
        rich_label = super().render_node(node)
        if node.data is None: return rich_label
        is_selected = node.data.path in self.selected_paths
        prefix = "[green]✓[/] " if is_selected else "[dim]][ ] [/]"
        from rich.text import Text
        final_label = Text.from_markup(prefix); final_label.append(rich_label)
        return final_label

    def _toggle_single_node_selection(self, node_fs_path: Path):
        """Toggles selection for a single node only, not its children."""
        if self._is_path_ignored(node_fs_path):
            self.app.bell(); return
        
        if node_fs_path in self.selected_paths:
            self.selected_paths.discard(node_fs_path)
        else:
            self.selected_paths.add(node_fs_path)
        
        self.refresh()
        self.post_message(self.SelectionChanged(self.selected_paths.copy(), self.project_root))

    def _toggle_node_and_children_selection(self, node_fs_path: Path):
        """Toggles selection for a node and all its non-ignored children recursively."""
        if self._is_path_ignored(node_fs_path) and not node_fs_path in self.selected_paths: # Allow de-selecting an ignored parent if it somehow got selected
            self.app.bell(); return

        # Determine the new selection state based on the parent node
        # If parent is currently selected, we deselect it and its children
        # If parent is not selected, we select it and its children
        new_select_state = not (node_fs_path in self.selected_paths)
        
        self._apply_selection_recursive(node_fs_path, new_select_state)
        
        self.refresh()
        self.post_message(self.SelectionChanged(self.selected_paths.copy(), self.project_root))

    def _apply_selection_recursive(self, node_fs_path: Path, select_state: bool):
        """Applies a selection state (True for select, False for deselect) recursively."""
        # Only process if not ignored, OR if we are deselecting (to allow unchecking ignored items)
        if not self._is_path_ignored(node_fs_path) or not select_state:
            if select_state:
                self.selected_paths.add(node_fs_path)
            else:
                self.selected_paths.discard(node_fs_path)

            if node_fs_path.is_dir():
                try:
                    for child_item_path in node_fs_path.iterdir():
                        self._apply_selection_recursive(child_item_path, select_state)
                except OSError as e:
                    self.app.log(f"OS Error applying recursive selection for {node_fs_path}: {e}")


    def on_directory_tree_node_selected(self, event: DirectoryTree.NodeSelected) -> None: # Click or Space
        """Default action: toggle selection of the single node."""
        event.stop()
        if event.node.data is None: return
        self._toggle_single_node_selection(event.node.data.path)
        
    async def on_key(self, event: events.Key) -> None:
        if self.cursor_node is None or self.cursor_node.data is None :
            # event.prevent_default() # Not strictly needed if we return early
            return

        node_fs_path = self.cursor_node.data.path

        if event.key == "j": self.action_cursor_down()
        elif event.key == "k": self.action_cursor_up()
        elif event.key == "l": # Expand dir or no-op for file
            if self.cursor_node.data.is_dir and self.cursor_node.allow_expand and not self.cursor_node.is_expanded:
                self.action_toggle_node()
        elif event.key == "h": # Collapse dir or go to parent
            if self.cursor_node.data.is_dir and self.cursor_node.is_expanded:
                self.action_toggle_node()
            elif self.cursor_node.parent and self.cursor_node.parent.is_tree_root is False:
                self.action_select_parent() # Moves cursor to parent
        elif event.key == "enter":
            if event.shift: # Shift+Enter for recursive selection
                self._toggle_node_and_children_selection(node_fs_path)
            else: # Just Enter for single node selection
                self._toggle_single_node_selection(node_fs_path)
        # Adding 'x' as an alternative for recursive selection, as Shift+Enter can be tricky
        elif event.key == "x":
            self._toggle_node_and_children_selection(node_fs_path)
        else: return # Let other keys pass through
        event.prevent_default()


    def get_selected_final_files(self) -> List[Path]:
        # ... (this method remains the same as the previous correctly working version)
        collected_files: Set[Path] = set()
        # Iterate over a copy, as selected_paths might be modified by _is_path_ignored if an invalid item was added
        current_selection_snapshot = list(self.selected_paths)

        for path_obj in current_selection_snapshot:
            if self._is_path_ignored(path_obj): # Ensure consistency
                # self.selected_paths.discard(path_obj) # Should already be handled by selection logic
                continue 
            
            # If a directory is in selected_paths, it means it was explicitly selected
            # (either individually or as part of a recursive parent selection).
            # We now collect all non-ignored files under it.
            if path_obj.is_dir():
                for item in path_obj.rglob("*"):
                    if not self._is_path_ignored(item) and item.is_file():
                        if not is_binary_heuristic(item) and get_file_size_mb(item) <= MAX_FILE_SIZE_MB:
                            collected_files.add(item)
            elif path_obj.is_file(): # It's an individually selected file
                 if not is_binary_heuristic(path_obj) and get_file_size_mb(path_obj) <= MAX_FILE_SIZE_MB:
                    collected_files.add(path_obj)

        relative_collected_files = set()
        if self.project_root:
            for abs_file_path in sorted(list(collected_files)):
                try:
                    if abs_file_path.is_relative_to(self.project_root) or abs_file_path == self.project_root:
                         relative_collected_files.add(abs_file_path.relative_to(self.project_root))
                except ValueError: self.app.log(f"ValueError making {abs_file_path} relative to {self.project_root}")
        return sorted(list(relative_collected_files))


class PathInputScreen(ModalScreen[Optional[Path]]):
    # ... (remains the same)
    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    def __init__(self, recent_folders: List[Path]):
        super().__init__(); self.recent_folders = recent_folders
    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Select or Enter Project Path:", classes="dialog_title")
            if self.recent_folders:
                yield Label("Recent folders (press number or click):")
                with ScrollableContainer(id="recent_list"):
                    for i, folder in enumerate(self.recent_folders):
                        yield Button(f"{i+1}. {str(folder)}", id=f"recent_{i}", variant="default")
            yield Input(placeholder="Type path or press number for recent...", id="path_input")
            with Horizontal(classes="dialog_buttons"):
                yield Button("OK", variant="primary", id="ok_button")
                yield Button("Cancel", variant="error", id="cancel_button")
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok_button":
            path_str = self.query_one(Input).value.strip()
            if path_str:
                path = Path(path_str).resolve()
                if path.is_dir(): self.dismiss(path)
                else: self.query_one(Input).value = ""; self.query_one(Input).placeholder = "Invalid. Try again."; self.app.bell()
            else: self.app.bell()
        elif event.button.id == "cancel_button": self.dismiss(None)
        elif event.button.id and event.button.id.startswith("recent_"):
            try:
                index = int(event.button.id.split("_")[1])
                if 0 <= index < len(self.recent_folders): self.dismiss(self.recent_folders[index].resolve())
            except (ValueError, IndexError): self.app.bell()
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        path_str = event.value.strip()
        if path_str:
            path = Path(path_str).resolve()
            if path.is_dir(): self.dismiss(path)
            else: self.query_one(Input).value = ""; self.query_one(Input).placeholder = "Invalid. Try again."; self.app.bell()
        else: self.app.bell()
    def action_cancel(self) -> None: self.dismiss(None)

class RepoPackerApp(App[None]):
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
    #recent_list Button { width: 100%; margin-bottom: 1;}
    Input { margin-bottom: 1; }
    .dialog_buttons { width: 100%; align-horizontal: right; margin-top:1; }
    .dialog_buttons Button { margin-left: 1; }
    #output_preview_dialog { align: center middle; width: 90%; height: 90%; border: thick $primary; background: $surface; padding: 1; }
    #output_preview_dialog Markdown { margin-bottom: 1; background: $primary-background-darken-1; padding: 1; border: round $accent;}
    #output_preview_dialog Static { margin-bottom: 1;}
    #output_preview_dialog Input { margin-bottom: 1;}
    """
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+o", "open_folder", "Open Folder"),
        Binding("ctrl+s", "generate_packed_file", "Generate File"),
        Binding("a", "select_all_tree", "Select All (project)"), # Clarified global select all
        Binding("d", "deselect_all_tree", "Deselect All (project)"), # Clarified
        Binding("x", "select_current_recursive", "Select Node Recursively (Tree Focus)"), # New binding for 'x'
    ]
    current_project_path: reactive[Optional[Path]] = reactive(None)
    status_message = reactive("Ready. Ctrl+O to Open.")

    def __init__(self, initial_path: Optional[Path] = None):
        super().__init__()
        self.arg_initial_path = initial_path.resolve() if initial_path and initial_path.exists() else None
        self.recent_folders = load_recent_folders()

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="app_body"):
            with Vertical(id="tree_panel"): pass
            with ScrollableContainer(id="sidebar_panel"):
                yield Markdown("### Selected Files\n\n_None selected_", id="selected_files_md")
        yield Static(self.status_message, id="status_bar")
        yield Footer()

    async def on_mount(self) -> None:
        # When app starts, clear selections (if any persisted unexpectedly)
        # and update sidebar for a clean initial state.
        if self.arg_initial_path and self.arg_initial_path.is_dir():
            self.current_project_path = self.arg_initial_path
        else:
            if self.arg_initial_path:
                 self.notify(f"Initial path '{self.arg_initial_path}' not valid.", severity="warning", timeout=4)
            if not self.current_project_path:
                # If no project path is set yet, show the placeholder by triggering watch method
                self.watch_current_project_path(None, None) # Ensure placeholder is shown
                await self.action_open_folder() # Then prompt for folder
        
        # Ensure initial sidebar state is clean if a tree gets loaded immediately
        try:
            tree = self.query_one(CheckableDirectoryTree)
            tree.post_message(CheckableDirectoryTree.SelectionChanged(set(), tree.project_root))
        except NoMatches:
            # If no tree, ensure sidebar shows "None selected"
             self.query_one("#selected_files_md", Markdown).update("### Selected Files\n\n_None selected_")


    def watch_current_project_path(self, old_path: Optional[Path], new_path: Optional[Path]) -> None:
        try: tree_panel = self.query_one("#tree_panel"); tree_panel.remove_children()
        except NoMatches: self.log("Error: #tree_panel not found during watch"); return
        try: self.query_one("#selected_files_md", Markdown).update("### Selected Files\n\n_None selected_")
        except NoMatches: pass

        if new_path and new_path.is_dir():
            tree_panel.mount(CheckableDirectoryTree(str(new_path), id="dir_tree"))
            save_recent_folders(new_path, self.recent_folders); self.recent_folders = load_recent_folders()
            self.sub_title = str(new_path); self.status_message = f"Project: {new_path.name}. Select items."
        else:
            tree_panel.mount(Static("Ctrl+O to open a folder or select from recent.", id="tree_placeholder"))
            self.sub_title = "No Project"; self.status_message = "No project. Ctrl+O to Open."
            if old_path and not new_path: self.notify("Folder selection cancelled or failed.", severity="warning")

    def watch_status_message(self, new_message: str) -> None:
        try: self.query_one("#status_bar", Static).update(new_message)
        except NoMatches: pass

    async def on_checkable_directory_tree_selection_changed(self, event: CheckableDirectoryTree.SelectionChanged) -> None:
        # ... (this method remains the same, make sure it correctly lists files)
        try:
            md_widget = self.query_one("#selected_files_md", Markdown)
            if not event.selected_paths: md_widget.update("### Selected Files\n\n_None selected_")
            else:
                # Display only files in the sidebar for clarity
                # Files are collected based on selected_paths which can include dirs
                # The get_selected_final_files method resolves this to actual files.
                # For the sidebar, we can iterate selected_paths and list files,
                # or show DIRS if a whole directory is selected.

                # Let's list files explicitly if they are in selected_paths,
                # and denote directories if they are in selected_paths.
                display_items = []
                project_root_for_rel = event.project_root
                
                temp_final_files_for_sidebar = set()
                for p_obj in event.selected_paths:
                    if self._is_path_ignored_for_app(p_obj, project_root_for_rel): continue # Check app-level ignore (same as tree's)
                    if p_obj.is_file():
                        if not is_binary_heuristic(p_obj) and get_file_size_mb(p_obj) <= MAX_FILE_SIZE_MB:
                             temp_final_files_for_sidebar.add(p_obj)
                    elif p_obj.is_dir(): # A directory is itself in selected_paths
                        # Add all valid files under it
                        for item in p_obj.rglob("*"):
                            if not self._is_path_ignored_for_app(item, project_root_for_rel) and item.is_file():
                                if not is_binary_heuristic(item) and get_file_size_mb(item) <= MAX_FILE_SIZE_MB:
                                    temp_final_files_for_sidebar.add(item)
                
                for abs_path in sorted(list(temp_final_files_for_sidebar)):
                    try:
                        if abs_path.is_relative_to(project_root_for_rel):
                            display_items.append(f"- `{abs_path.relative_to(project_root_for_rel)}`")
                        else: display_items.append(f"- `{abs_path}` (external)")
                    except ValueError: display_items.append(f"- `{abs_path}` (rel error)")

                title = f"### Selected Files ({len(display_items)})" if display_items else "### Selected Files\n\n_None selected_"
                md_content = title + "\n\n" + "\n".join(display_items if display_items else ["_No individual files match selection criteria for display._"])
                md_widget.update(md_content)
        except NoMatches: self.log("Error: #selected_files_md widget not found.")

    def _is_path_ignored_for_app(self, path_obj: Path, project_root: Path) -> bool:
        # Helper to use the same ignore logic as the tree, but callable from app context
        # This is a simplified version. Ideally, get the tree instance and call its method.
        abs_path_obj = path_obj.resolve() if not path_obj.is_absolute() else path_obj
        try:
            if not abs_path_obj.is_relative_to(project_root) and abs_path_obj != project_root: return True
        except ValueError: return True
        for pattern_str in DEFAULT_IGNORES: # Not including self.additional_ignored_patterns here
            if pattern_str.endswith('/'):
                dir_name_to_ignore = pattern_str.rstrip('/')
                if dir_name_to_ignore in abs_path_obj.parts or \
                   (abs_path_obj.is_dir() and abs_path_obj.name == dir_name_to_ignore): return True
            elif fnmatch.fnmatch(abs_path_obj.name, pattern_str): return True
        # Simplified: does not include .gitignore for app-level checks for sidebar.
        # The tree's get_selected_final_files already handles full gitignore.
        return False


    async def action_open_folder(self) -> None:
        # ... (remains the same)
        def set_new_path(path: Optional[Path]):
            if path: self.current_project_path = path
            elif not self.current_project_path: self.notify("No folder selected.", severity="information")
        await self.app.push_screen(PathInputScreen(self.recent_folders), set_new_path)

    def action_select_all_tree(self) -> None: # Global select all
        # ... (remains the same, but ensure it posts SelectionChanged)
        try:
            tree = self.query_one(CheckableDirectoryTree)
            tree.selected_paths.clear()
            if tree.project_root:
                for path_obj in tree.project_root.rglob("*"):
                    if not tree._is_path_ignored(path_obj): tree.selected_paths.add(path_obj)
            tree.refresh()
            tree.post_message(CheckableDirectoryTree.SelectionChanged(tree.selected_paths.copy(), tree.project_root))
            self.status_message = f"{len(tree.selected_paths)} non-ignored items marked. Review tree."
        except NoMatches: self.status_message = "No project tree loaded."; self.bell()
        except Exception as e: self.status_message = f"Error selecting all: {e}"; self.log(f"Select All Error: {e}"); self.bell()

    def action_deselect_all_tree(self) -> None: # Global deselect all
        # ... (remains the same, but ensure it posts SelectionChanged)
        try:
            tree = self.query_one(CheckableDirectoryTree)
            tree.selected_paths.clear(); tree.refresh()
            tree.post_message(CheckableDirectoryTree.SelectionChanged(tree.selected_paths.copy(), tree.project_root))
            self.status_message = "All selections cleared."
        except NoMatches: self.status_message = "No project tree loaded to deselect from."; self.bell()

    def action_select_current_recursive(self) -> None:
        """Action to be called by 'x' key binding, targets focused tree."""
        try:
            tree = self.query_one(CheckableDirectoryTree)
            if tree.has_focus and tree.cursor_node and tree.cursor_node.data:
                tree._toggle_node_and_children_selection(tree.cursor_node.data.path)
            else:
                self.bell() # No tree focused or no cursor node
        except NoMatches:
            self.bell() # No tree


    async def action_generate_packed_file(self) -> None:
        # ... (remains the same)
        if not self.current_project_path:
            self.status_message = "Error: No project folder loaded."; self.bell(); return
        try:
            tree = self.query_one(CheckableDirectoryTree)
            selected_relative_paths = tree.get_selected_final_files() # Uses tree's logic
        except NoMatches: self.status_message = "Error: Project tree not found."; self.bell(); return
        
        if not selected_relative_paths:
            self.status_message = "Warning: No files selected or eligible for packing."; self.bell(); return
        
        _final_output_str_for_clipboard = ""
        output_parts = []
        output_parts.append(f"This file is a merged representation of selected codebase parts from '{self.current_project_path.name}', combined by RepoPacker.\n")
        output_parts.append("<file_summary>")
        output_parts.append(f"<source_project_path>{str(self.current_project_path)}</source_project_path>")
        output_parts.append("<purpose>This file contains a packed representation of selected repository contents for LLM consumption, analysis, or review.</purpose>")
        output_parts.append("<file_format>\n1. This summary section\n2. Directory structure (selected files only)\n3. Selected repository files, each consisting of:\n  - File path as an attribute (relative to project root)\n  - Full contents of the file\n</file_format>")
        output_parts.append(f"<usage_guidelines>\n- Read-only. Edit original files.\n- Use file paths to distinguish files.\n- Aware of potential sensitive info.\n</usage_guidelines>")
        output_parts.append(f"<notes>\n- Text files only.\n- Ignores: .gitignore, defaults (e.g., .git, node_modules), size limit (>{MAX_FILE_SIZE_MB}MB).\n- Binaries heuristically excluded.\n</notes>")
        output_parts.append("</file_summary>\n")
        output_parts.append("<directory_structure>")
        for rel_path in selected_relative_paths: output_parts.append(str(rel_path).replace('\\', '/'))
        output_parts.append("</directory_structure>\n")
        output_parts.append("<files>")
        output_parts.append("This section contains the contents of the repository's selected files.\n")

        files_processed_count = 0; total_char_count = 0
        for rel_path in selected_relative_paths:
            full_path = self.current_project_path / rel_path
            self.status_message = f"Processing: {rel_path}"; await asyncio.sleep(0)
            try:
                with open(full_path, 'r', encoding='utf-8', errors='replace') as f: content = f.read()
                normalized_rel_path = str(rel_path).replace('\\', '/')
                output_parts.append(f'<file path="{normalized_rel_path}">'); output_parts.append(content); output_parts.append("</file>\n")
                files_processed_count += 1; total_char_count += len(content)
            except Exception as e:
                self.log(f"Error reading file {full_path}: {e}")
                output_parts.append(f'<file path="{str(rel_path).replace("\\", "/")}">\nError reading file: {e}\n</file>\n')
        output_parts.append("</files>")
        _final_output_str_for_clipboard = "\n".join(output_parts); estimated_tokens = total_char_count // 3

        class OutputPreviewScreen(ModalScreen[Optional[Path]]):
            # ... (remains the same)
            def __init__(self, default_filename: str, project_path: Path, num_files: int, est_tokens: int):
                super().__init__()
                self.default_filename = default_filename; self.project_path = project_path
                self.num_files = num_files; self.est_tokens = est_tokens
            def compose(self) -> ComposeResult:
                with Vertical(id="output_preview_dialog"):
                    yield Label("Pack Complete & Save", classes="dialog_title")
                    yield Markdown(f"- **Project:** `{self.project_path.name}`\n- **Files Packed:** {self.num_files}\n- **Est. Tokens:** ~{self.est_tokens:,} (chars/3)")
                    yield Static("Save as (in project's parent dir or Home):")
                    yield Input(self.default_filename, id="save_path_input")
                    with Horizontal(classes="dialog_buttons"):
                        yield Button("Save", variant="success", id="save_btn")
                        yield Button("Copy to Clipboard", id="copy_btn")
                        yield Button("Cancel", variant="error", id="cancel_btn")
            async def on_button_pressed(self, event: Button.Pressed) -> None:
                if event.button.id == "save_btn":
                    save_name = self.query_one("#save_path_input", Input).value
                    if save_name:
                        save_dir = self.project_path.parent if self.project_path and self.project_path.parent else Path.home()
                        self.dismiss(save_dir / save_name)
                    else: self.app.bell()
                elif event.button.id == "copy_btn":
                    try:
                        import pyperclip
                        pyperclip.copy(_final_output_str_for_clipboard)
                        self.app.notify("Content copied to clipboard!", severity="information")
                    except Exception as e:
                        self.app.notify(f"Clipboard error: {e}. (pyperclip installed?)", severity="error")
                        self.app.log(f"Clipboard error: {e}")
                    self.dismiss(None)
                elif event.button.id == "cancel_btn":
                    self.dismiss(None)

        default_save_name = f"packed_{self.current_project_path.name}_{files_processed_count}files.txt"
        save_path_result = await self.app.push_screen_wait(
            OutputPreviewScreen(default_save_name, self.current_project_path, files_processed_count, estimated_tokens)
        )

        if save_path_result:
            try:
                with open(save_path_result, "w", encoding="utf-8") as f: f.write(_final_output_str_for_clipboard)
                self.status_message = f"Success! {files_processed_count} files packed to {save_path_result}"
                self.notify(self.status_message, severity="information", timeout=5)
            except Exception as e:
                self.status_message = f"Error saving file: {e}"
                self.notify(self.status_message, severity="error", timeout=5); self.log(e); self.bell()
        else: self.status_message = "File generation cancelled or action completed."


if __name__ == "__main__":
    import sys
    initial_folder = None
    if len(sys.argv) > 1:
        path_arg = Path(sys.argv[1])
        if path_arg.is_dir(): initial_folder = path_arg
    app = RepoPackerApp(initial_path=initial_folder); app.run()
