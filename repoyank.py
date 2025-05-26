#!/usr/bin/env python
import asyncio
import os
from pathlib import Path
from typing import Set, List, Optional, Iterable, Dict, Tuple
import fnmatch

# NEW: Import platformdirs
import platformdirs

from textual.app import App, ComposeResult, Screen
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.reactive import reactive
from textual.widgets import Header, Footer, DirectoryTree, Button, Static, Input, Label, Markdown
from textual.widgets._tree import TreeNode, Tree
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

# --- Configuration ---
APP_NAME = "RepoPackerTUI"
APP_AUTHOR = "RepoPackerUser" # You can change this

# Use platformdirs to get a user data directory
# This will create a path like:
# Linux: ~/.local/share/RepoPackerTUI/
# macOS: ~/Library/Application Support/RepoPackerTUI/
# Windows: C:\Users\<User>\AppData\Local\RepoPackerUser\RepoPackerTUI\
APP_DATA_DIR = Path(platformdirs.user_data_dir(appname=APP_NAME, appauthor=APP_AUTHOR))

# Ensure the application data directory exists
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)

# Update the path for your recent folders file to be inside APP_DATA_DIR
RECENT_FOLDERS_FILE = APP_DATA_DIR / "repopacker_recent.txt"
# If you had other config/data files, they'd go here too:
# LOG_FILE = APP_DATA_DIR / "app.log"
# SETTINGS_FILE = APP_DATA_DIR / "settings.json"


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
MAX_RECENT_ENTRIES = 10

# --- Helper Functions ---
# No changes needed in the functions themselves, they use the RECENT_FOLDERS_FILE variable.
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
    except Exception as e:
        # It's good to log this if the app instance is available,
        # otherwise print for now.
        print(f"Error saving recent folders to {RECENT_FOLDERS_FILE}: {e}")


class CheckableDirectoryTree(DirectoryTree):
    BINDINGS = [
        Binding("enter", "toggle_expand_or_select", "Expand Dir (Enter)", show=False, priority=True),
        Binding("space", "space_pressed_on_item", "Select File / Expand Dir (Space)", show=False, priority=True),
        Binding("s", "toggle_selection_recursive_cursor", "Select Item Recursively (S)", show=False, priority=True),
    ]

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
        self._ignored_paths_cache: Dict[Path, bool] = {}
        self._binary_heuristic_cache: Dict[Path, bool] = {}
        self._file_size_cache: Dict[Path, float] = {}
        self._precompiled_default_ignores: List[Tuple[str, bool, str]] = []
        for pattern_str in DEFAULT_IGNORES + self.additional_ignored_patterns:
            is_dir_pattern = pattern_str.endswith('/')
            normalized_name_pattern = pattern_str.rstrip('/') if is_dir_pattern else pattern_str
            self._precompiled_default_ignores.append(
                (normalized_name_pattern, is_dir_pattern, pattern_str)
            )

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        for path_obj in paths:
            if not self._is_path_ignored(path_obj): 
                yield path_obj

    def _is_path_ignored(self, path_obj: Path) -> bool:
        abs_path_obj = path_obj.resolve() if not path_obj.is_absolute() else path_obj.resolve()
        if abs_path_obj in self._ignored_paths_cache:
            return self._ignored_paths_cache[abs_path_obj]
        try:
            if not abs_path_obj.is_relative_to(self.project_root) and abs_path_obj != self.project_root:
                self._ignored_paths_cache[abs_path_obj] = True; return True
        except ValueError: 
            self._ignored_paths_cache[abs_path_obj] = True; return True
        for compiled_name_pattern, is_dir_pattern, original_fnmatch_pattern in self._precompiled_default_ignores:
            if is_dir_pattern:
                if (abs_path_obj.is_dir() and abs_path_obj.name == compiled_name_pattern) or \
                   compiled_name_pattern in abs_path_obj.parts:
                    self._ignored_paths_cache[abs_path_obj] = True; return True
            elif fnmatch.fnmatch(abs_path_obj.name, original_fnmatch_pattern):
                self._ignored_paths_cache[abs_path_obj] = True; return True
        path_to_check_str = str(abs_path_obj)
        dirs_to_check_for_gitignore = [self.project_root]
        try: 
            if abs_path_obj.parent != self.project_root and \
               abs_path_obj.parent.is_relative_to(self.project_root):
                current_dir = abs_path_obj.parent
                while current_dir != self.project_root and current_dir != current_dir.parent :
                    dirs_to_check_for_gitignore.append(current_dir)
                    if not current_dir.is_relative_to(self.project_root): break 
                    current_dir = current_dir.parent
        except ValueError: pass 
        for gitignore_dir in reversed(dirs_to_check_for_gitignore): 
            matcher = self._gitignore_matchers.get(gitignore_dir)
            if matcher is None:
                gf_path = gitignore_dir / ".gitignore"
                if gf_path.is_file():
                    try: matcher = gitignore_parser.parse_gitignore(str(gf_path), base_dir=str(gitignore_dir.resolve()))
                    except Exception as e: self.app.log(f"Warning: Parse {gf_path}: {e}"); matcher = lambda p: False
                else: matcher = lambda p: False
                self._gitignore_matchers[gitignore_dir] = matcher
            if callable(matcher) and matcher(path_to_check_str):
                self._ignored_paths_cache[abs_path_obj] = True; return True
        self._ignored_paths_cache[abs_path_obj] = False
        return False

    def _is_node_effectively_selected_file(self, file_path: Path) -> bool:
        if self._is_path_ignored(file_path): return False 
        is_bin = self._binary_heuristic_cache.get(file_path)
        if is_bin is None: is_bin = is_binary_heuristic(file_path); self._binary_heuristic_cache[file_path] = is_bin
        if is_bin: return False
        size_mb = self._file_size_cache.get(file_path)
        if size_mb is None: size_mb = get_file_size_mb(file_path); self._file_size_cache[file_path] = size_mb
        if size_mb > MAX_FILE_SIZE_MB: return False
        if file_path in self.selected_paths: return True
        current_parent = file_path.parent
        while current_parent != self.project_root.parent and current_parent != current_parent.parent : 
            try: is_relevant_parent = (current_parent == self.project_root or current_parent.is_relative_to(self.project_root))
            except ValueError: is_relevant_parent = False
            if not is_relevant_parent: break 
            if current_parent in self.selected_paths: return True
            if current_parent == self.project_root: break 
            current_parent = current_parent.parent
        return False

    def render_label(self, node: TreeNode[DirEntry], base_style: Style, style: Style) -> Text:
        rendered_label_from_super = super().render_label(node, base_style, style)
        if node.data is None: return Text("Error: No data") 
        node_fs_path = node.data.path
        is_directly_selected = node_fs_path in self.selected_paths
        prefix_text = Text.from_markup("[green]✓ [/]" if is_directly_selected else "  ")
        final_renderable = Text("").append(prefix_text).append(rendered_label_from_super)
        if node_fs_path.is_file() and self._is_node_effectively_selected_file(node_fs_path): 
            final_renderable.append_text(Text(" [b #40E0D0](pack)[/b]", no_wrap=True))
        return final_renderable

    def _toggle_single_node_selection(self, node_fs_path: Path):
        self.app.log(f"Toggling single selection: {node_fs_path}")
        if node_fs_path in self.selected_paths:
            self.selected_paths.discard(node_fs_path)
        else:
            if self._is_path_ignored(node_fs_path): 
                self.app.log(f"Path ignored, not selecting: {node_fs_path}"); self.app.bell(); return
            self.selected_paths.add(node_fs_path)
        self.refresh()
        self.post_message(self.SelectionChanged(self.selected_paths.copy(), self.project_root))

    def _toggle_node_and_children_selection(self, node_fs_path: Path):
        self.app.log(f"Toggling node and children selection: {node_fs_path}")
        is_currently_selected = node_fs_path in self.selected_paths
        new_select_state = not is_currently_selected
        self._apply_selection_recursive(node_fs_path, new_select_state)
        self.refresh()
        self.post_message(self.SelectionChanged(self.selected_paths.copy(), self.project_root))

    def _apply_selection_recursive(self, node_fs_path: Path, select_state: bool):
        if select_state:
            if not self._is_path_ignored(node_fs_path): 
                self.selected_paths.add(node_fs_path)
        else:
            self.selected_paths.discard(node_fs_path)
        if node_fs_path.is_dir():
            try:
                for child_item_path in self.filter_paths(node_fs_path.iterdir()): 
                    self._apply_selection_recursive(child_item_path, select_state)
            except OSError as e: self.app.log(f"OS Error applying recursive selection for {node_fs_path}: {e}")

    def action_toggle_expand_or_select(self) -> None: # Bound to Enter
        if self.cursor_node and self.cursor_node.data:
            node_fs_path = self.cursor_node.data.path
            if node_fs_path.is_dir():
                self.app.log(f"Enter on dir: {node_fs_path}, calling action_toggle_node. Current expanded: {self.cursor_node._expanded}")
                self.action_toggle_node() 
                self.app.log(f"After action_toggle_node (Enter). New expanded: {self.cursor_node._expanded}")
    
    def action_space_pressed_on_item(self) -> None: # Bound to Space
        if self.cursor_node and self.cursor_node.data:
            node_fs_path = self.cursor_node.data.path
            self.app.log(f"action_space_pressed_on_item on: {node_fs_path}")
            if node_fs_path.is_file():
                self.app.log(f"Space on file: {node_fs_path}, toggling single selection.")
                self._toggle_single_node_selection(node_fs_path)
            elif node_fs_path.is_dir():
                self.app.log(f"Space on dir: {node_fs_path}, calling action_toggle_node. Current expanded: {self.cursor_node._expanded}")
                self.action_toggle_node() 
                self.app.log(f"After action_toggle_node (Space). New expanded: {self.cursor_node._expanded}")
        else:
            self.app.log("action_space_pressed_on_item: No cursor node or data.")

    def on_tree_node_selected(self, event: Tree.NodeSelected[DirEntry]) -> None: # For Clicks
        event.stop() 
        if event.node is None or event.node.data is None:
            self.app.log("Tree.NodeSelected (likely click) event with no node or no data.")
            return
        node_fs_path = event.node.data.path
        self.app.log(f"Click (Tree.NodeSelected event) on: {node_fs_path}")
        if node_fs_path.is_file():
            self.app.log(f"Click on file: {node_fs_path}, toggling single selection.")
            self._toggle_single_node_selection(node_fs_path)
        elif node_fs_path.is_dir():
            self.app.log(f"Click on dir: {node_fs_path}. Expansion may be handled by default DirectoryTree click behavior. Current expanded state (before potential default toggle): {event.node._expanded}")
    
    def action_toggle_selection_recursive_cursor(self) -> None: # Bound to 's'
        self.app.bell() 
        self.app.log("--- 'S' KEY ACTION CALLED (toggle_selection_recursive_cursor) ---") 
        if self.cursor_node and self.cursor_node.data:
            node_fs_path = self.cursor_node.data.path
            self.app.log(f"'s' key on: {node_fs_path}")
            if node_fs_path.is_file():
                self.app.log(f"'s' key on file: {node_fs_path}, toggling single selection.")
                self._toggle_single_node_selection(node_fs_path)
            elif node_fs_path.is_dir():
                self.app.log(f"'s' key on dir: {node_fs_path}, toggling recursive selection.")
                self._toggle_node_and_children_selection(node_fs_path)
        else:
            self.app.log("'s' key pressed but no cursor node or data.")
            
    async def on_key(self, event: events.Key) -> None:
        key_handled_by_us = False
        if event.key == "j":
            if self.cursor_node is not None: self.action_cursor_down(); key_handled_by_us = True
        elif event.key == "k":
            if self.cursor_node is not None: self.action_cursor_up(); key_handled_by_us = True
        if key_handled_by_us: event.prevent_default()

    def get_selected_final_files(self) -> List[Path]:
        collected_files: Set[Path] = set(); already_processed_for_collection: Set[Path] = set() 
        for path_obj_abs in self.selected_paths: 
            if path_obj_abs.is_file() and path_obj_abs not in already_processed_for_collection:
                if not self._is_path_ignored(path_obj_abs):
                    is_bin = self._binary_heuristic_cache.get(path_obj_abs)
                    if is_bin is None: is_bin = is_binary_heuristic(path_obj_abs); self._binary_heuristic_cache[path_obj_abs] = is_bin
                    size_mb = self._file_size_cache.get(path_obj_abs)
                    if size_mb is None: size_mb = get_file_size_mb(path_obj_abs); self._file_size_cache[path_obj_abs] = size_mb
                    if not is_bin and size_mb <= MAX_FILE_SIZE_MB: collected_files.add(path_obj_abs)
                already_processed_for_collection.add(path_obj_abs)
        for selected_path_abs in self.selected_paths:
            if selected_path_abs.is_dir():
                if not self._is_path_ignored(selected_path_abs): 
                    try:
                        for item_abs in selected_path_abs.rglob("*"): 
                            if item_abs.is_file() and item_abs not in already_processed_for_collection:
                                if not self._is_path_ignored(item_abs): 
                                    is_bin = self._binary_heuristic_cache.get(item_abs)
                                    if is_bin is None: is_bin = is_binary_heuristic(item_abs); self._binary_heuristic_cache[item_abs] = is_bin
                                    size_mb = self._file_size_cache.get(item_abs)
                                    if size_mb is None: size_mb = get_file_size_mb(item_abs); self._file_size_cache[item_abs] = size_mb
                                    if not is_bin and size_mb <= MAX_FILE_SIZE_MB: collected_files.add(item_abs)
                                already_processed_for_collection.add(item_abs)
                    except OSError as e: self.app.log(f"OS Error rglobbing {selected_path_abs}: {e}")
                already_processed_for_collection.add(selected_path_abs) 
        relative_collected_files = set()
        if self.project_root:
            for abs_file_path in sorted(list(collected_files)):
                try:
                    if abs_file_path.is_relative_to(self.project_root):
                         relative_collected_files.add(abs_file_path.relative_to(self.project_root))
                except ValueError: self.app.log(f"ValueError making {abs_file_path} relative to {self.project_root}")
        return sorted(list(relative_collected_files))


class PathInputScreen(ModalScreen[Optional[Path]]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("up", "cursor_up", "Cursor Up", show=False),
        Binding("down", "cursor_down", "Cursor Down", show=False),
    ]
    DEFAULT_CSS = """
    PathInputScreen { align: center top; padding-top: 2; background: $panel-lighten-1; }
    #path_input_dialog_content { width: 80%; max-width: 60; height: auto; border: tall $primary-lighten-2; padding: 1 2; background: $surface; }
    #path_input_widget { margin-top: 1; margin-bottom: 1; }
    #recent_folders_scroller { max-height: 10; border: round $primary-lighten-2; padding: 0 1; margin-top: 1; }
    .recent_folder_item { width: 100%; height: 1; padding: 0 1; border: none; background: transparent; text-align: left; color: $text; margin-bottom: 0; }
    .recent_folder_item:hover { background: $primary-background-lighten-2; }
    .recent_folder_item:focus { background: $primary; }
    """
    def __init__(self, recent_folders: List[Path]):
        super().__init__()
        self.recent_folders = recent_folders
        self.input_widget: Optional[Input] = None
        self.recent_folder_widgets: List[Button] = []
        self._current_focus_idx = -1 
    def compose(self) -> ComposeResult:
        self.recent_folder_widgets.clear()
        with Vertical(id="path_input_dialog_content"):
            yield Label("Enter Project Path or Select Recent (↓/↑, Enter):")
            self.input_widget = Input(placeholder="Type path or press number (1-9)", id="path_input_widget" )
            yield self.input_widget
            if self.recent_folders:
                yield Label("Recent Folders:") 
                with ScrollableContainer(id="recent_folders_scroller"): 
                    for i, folder in enumerate(self.recent_folders):
                        button = Button(f"{i+1}. {str(folder)}", id=f"recent_{i}", classes="recent_folder_item")
                        self.recent_folder_widgets.append(button); yield button
    async def on_mount(self) -> None:
        if self.input_widget: self.input_widget.focus(); self._current_focus_idx = -1
    def _focus_widget_by_idx(self):
        if self._current_focus_idx == -1:
            if self.input_widget: self.input_widget.focus()
        elif 0 <= self._current_focus_idx < len(self.recent_folder_widgets):
            self.recent_folder_widgets[self._current_focus_idx].focus()
    def action_cursor_up(self) -> None:
        if not self.recent_folder_widgets and self.input_widget: self.input_widget.focus(); self._current_focus_idx = -1; return
        if self._current_focus_idx == -1: 
            if self.recent_folder_widgets: self._current_focus_idx = len(self.recent_folder_widgets) - 1
        elif self._current_focus_idx == 0: self._current_focus_idx = -1
        else: self._current_focus_idx -= 1
        self._focus_widget_by_idx()
        if self._current_focus_idx != -1:
            try: self.query_one("#recent_folders_scroller", ScrollableContainer).scroll_to_widget(self.recent_folder_widgets[self._current_focus_idx], animate=False, top=True)
            except NoMatches: pass 
    def action_cursor_down(self) -> None:
        if not self.recent_folder_widgets and self.input_widget: self.input_widget.focus(); self._current_focus_idx = -1; return
        if self._current_focus_idx == -1: 
            if self.recent_folder_widgets: self._current_focus_idx = 0
        elif self._current_focus_idx == len(self.recent_folder_widgets) - 1: self._current_focus_idx = -1
        else: self._current_focus_idx += 1
        self._focus_widget_by_idx()
        if self._current_focus_idx != -1:
            try: self.query_one("#recent_folders_scroller", ScrollableContainer).scroll_to_widget(self.recent_folder_widgets[self._current_focus_idx], animate=False, top=False)
            except NoMatches: pass 
    async def on_key(self, event: events.Key) -> None:
        is_input_focused = self.input_widget and self.input_widget.has_focus
        is_input_empty = self.input_widget and not self.input_widget.value
        if event.key.isdigit() and '0' not in event.key:
            if (is_input_focused and is_input_empty) or not is_input_focused:
                try:
                    index = int(event.key) - 1
                    if 0 <= index < len(self.recent_folders) and index < 9:
                        self.dismiss(self.recent_folders[index].resolve()); event.prevent_default(); return
                except ValueError: pass
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        path_str = event.value.strip()
        if path_str.isdigit(): 
            try:
                index = int(path_str) - 1
                if 0 <= index < len(self.recent_folders): self.dismiss(self.recent_folders[index].resolve()); return
            except ValueError: pass 
        if path_str:
            path = Path(path_str).resolve()
            if path.is_dir(): self.dismiss(path)
            else:
                if self.input_widget: self.app.bell(); self.input_widget.value = "" 
        else: self.app.bell()
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id and event.button.id.startswith("recent_"):
            try:
                index = int(event.button.id.split("_")[1])
                if 0 <= index < len(self.recent_folders): self.dismiss(self.recent_folders[index].resolve())
            except (ValueError, IndexError): self.app.bell()
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
        super().__init__()
        self.arg_initial_path = initial_path.resolve() if initial_path and initial_path.exists() else None
        self.recent_folders = load_recent_folders()
        self.log(f"Application data directory: {APP_DATA_DIR}")
        self.log(f"Recent folders file path: {RECENT_FOLDERS_FILE}")

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="app_body"):
            with Vertical(id="tree_panel"): pass
            with ScrollableContainer(id="sidebar_panel"):
                yield Markdown("### Selected Files\n\n_None selected_", id="selected_files_md")
        yield Static(self.status_message, id="status_bar"); yield Footer()
    async def on_mount(self) -> None:
        initial_path_set = False
        if self.arg_initial_path and self.arg_initial_path.is_dir():
            self.current_project_path = self.arg_initial_path; initial_path_set = True
        elif self.arg_initial_path:
            self.notify(f"Initial path '{self.arg_initial_path}' not valid.", severity="warning", timeout=4)
        if not initial_path_set: await self.action_open_folder()
        try:
            if self.query(CheckableDirectoryTree): 
                tree_list = self.query(CheckableDirectoryTree) 
                if tree_list and tree_list.first().project_root: 
                    tree_list.first().post_message(CheckableDirectoryTree.SelectionChanged(set(), tree_list.first().project_root)) 
            else: self.query_one("#selected_files_md", Markdown).update("### Selected Files\n\n_None selected_")
        except Exception as e: self.log(f"Error in on_mount sidebar clearing: {e}")
    def watch_current_project_path(self, old_path: Optional[Path], new_path: Optional[Path]) -> None:
        try: self.query_one("#tree_panel").remove_children()
        except NoMatches: self.log("Error: #tree_panel not found during watch"); return
        try: self.query_one("#selected_files_md", Markdown).update("### Selected Files\n\n_None selected_")
        except NoMatches: pass
        if new_path and new_path.is_dir():
            tree = CheckableDirectoryTree(str(new_path), id="dir_tree")
            self.query_one("#tree_panel").mount(tree); self.call_after_refresh(tree.focus)
            save_recent_folders(new_path, self.recent_folders); self.recent_folders = load_recent_folders()
            self.sub_title = str(new_path); self.status_message = f"Project: {new_path.name}. Select items. 'c' to Copy."
        else:
            self.query_one("#tree_panel").mount(Static("No project loaded. Open a folder (F5).", id="tree_placeholder"))
            self.sub_title = "No Project"; self.status_message = "No project. Open (F5), Copy ('c')."
            if old_path and not new_path: self.notify("Folder selection cancelled or failed.", severity="warning")
    def watch_status_message(self, new_message: str) -> None:
        try: self.query_one("#status_bar", Static).update(new_message)
        except NoMatches: pass
    async def on_checkable_directory_tree_selection_changed(self, event: CheckableDirectoryTree.SelectionChanged) -> None:
        try:
            md_widget = self.query_one("#selected_files_md", Markdown)
            tree = self.query_one(CheckableDirectoryTree) 
            final_files = tree.get_selected_final_files() 
            if not final_files: md_widget.update("### Selected Files\n\n_No packable files based on current selection._"); return
            display_items = [f"- `{str(rel_path)}`" for rel_path in sorted(final_files)]
            md_widget.update(f"### Selected Files ({len(display_items)})\n\n" + "\n".join(display_items))
        except NoMatches: self.log("Error: Widget not found for sidebar update.")
        except Exception as e: self.log(f"Error updating sidebar: {e}")
    async def action_open_folder(self) -> None:
        def set_new_path(path: Optional[Path]):
            if path: self.current_project_path = path
            elif not self.current_project_path: self.notify("No folder selected.", severity="information")
        await self.app.push_screen(PathInputScreen(self.recent_folders), set_new_path)
    def action_select_all_tree(self) -> None:
        try:
            tree = self.query_one(CheckableDirectoryTree)
            if tree.project_root: tree._apply_selection_recursive(tree.project_root, select_state=True) 
            tree.refresh(); tree.post_message(CheckableDirectoryTree.SelectionChanged(tree.selected_paths.copy(), tree.project_root))
            self.status_message = f"{sum(1 for p in tree.selected_paths if not tree._is_path_ignored(p))} items marked."
        except NoMatches: self.status_message = "No project tree loaded."; self.bell()
        except Exception as e: self.status_message = f"Error selecting all: {e}"; self.log(f"Select All Error: {e}"); self.bell()
    def action_deselect_all_tree(self) -> None:
        try:
            tree = self.query_one(CheckableDirectoryTree)
            if tree.project_root: tree._apply_selection_recursive(tree.project_root, select_state=False)
            else: tree.selected_paths.clear() 
            tree.refresh(); tree.post_message(CheckableDirectoryTree.SelectionChanged(tree.selected_paths.copy(), tree.project_root))
            self.status_message = "All selections cleared."
        except NoMatches: self.status_message = "No project tree loaded."; self.bell()
    def _operate_on_focused_folder_contents(self, select_state: bool):
        try:
            tree = self.query_one(CheckableDirectoryTree)
            if tree.cursor_node and tree.cursor_node.data:
                node_path = tree.cursor_node.data.path
                if node_path.is_dir(): 
                    for child_item in tree.filter_paths(node_path.iterdir()): # Operate on children
                        tree._apply_selection_recursive(child_item, select_state)
                    tree.refresh(); tree.post_message(CheckableDirectoryTree.SelectionChanged(tree.selected_paths.copy(), tree.project_root))
                    self.status_message = f"{'Selected' if select_state else 'Deselected'} contents of '{node_path.name}'."
                else: self.status_message = "Focused item is not a directory."; self.bell() 
            else: self.status_message = "No item focused."; self.bell()
        except NoMatches: self.status_message = "No project tree loaded."; self.bell()
    def action_select_in_focused_folder(self) -> None:
        self._operate_on_focused_folder_contents(select_state=True)
    def action_deselect_in_focused_folder(self) -> None:
        self._operate_on_focused_folder_contents(select_state=False)
    async def action_generate_packed_file(self) -> None: 
        if not self.current_project_path: self.notify("Error: No project folder loaded.", severity="error", timeout=3); self.bell(); return
        try: tree = self.query_one(CheckableDirectoryTree); selected_paths = tree.get_selected_final_files() 
        except NoMatches: self.notify("Error: Project tree not found.", severity="error", timeout=3); self.bell(); return
        if not selected_paths: self.notify("Warning: No files selected/eligible.", severity="warning", timeout=3); self.bell(); return
        output_parts = ["<file_summary>", "This section contains a summary of this file.", "", "<purpose>", "This file contains a packed representation of selected repository contents.", "It is designed to be easily consumable by AI systems for analysis, code review,","or other automated processes.","</purpose>","","<file_format>", "The content is organized as follows:","1. This summary section","2. Directory structure of selected files","3. Selected repository files, each consisting of:","  - File path as an attribute (relative to project root)","  - Full contents of the file","</file_format>","","<usage_guidelines>","- This file should be treated as read-only. Any changes should be made to the","  original repository files, not this packed version.","- When processing this file, use the file path to distinguish","  between different files in the repository.","- Be aware that this file may contain sensitive information. Handle it with","  the same level of security as you would the original repository.","</usage_guidelines>","","<notes>","- Files are selected based on user interaction and ignore rules.","- Binary files (based on a heuristic) are excluded.","- Files matching patterns in .gitignore (if present) and default ignore patterns (e.g., .git, __pycache__) are typically excluded from selection and packing.",f"- File size limits may apply (currently >{MAX_FILE_SIZE_MB}MB excluded).","</notes>","","<additional_info>",f"Generated by RepoPacker TUI from project: {self.current_project_path.name}","</additional_info>","</file_summary>","","<directory_structure>"]
        for rel_path in selected_paths: output_parts.append(str(rel_path).replace('\\', '/')) 
        output_parts.extend(["</directory_structure>", "", "<files>", "This section contains the contents of the repository's selected files."])
        if selected_paths: output_parts.append("")
        files_processed = 0; self.status_message = "Preparing content..."; await asyncio.sleep(0.01) 
        for i, rel_path in enumerate(selected_paths):
            full_path = self.current_project_path / rel_path
            try:
                with open(full_path, 'r', encoding='utf-8', errors='replace') as f: content = f.read()
                output_parts.extend([f'<file path="{str(rel_path).replace("\\", "/")}">', content, '</file>'])
                if i < len(selected_paths) -1: output_parts.append("")
                files_processed += 1
            except Exception as e:
                self.log(f"Error reading {full_path}: {e}")
                output_parts.extend([f'<file path="{str(rel_path).replace("\\", "/")}">{os.linesep}Error reading file: {e}{os.linesep}</file>'])
                if i < len(selected_paths) -1: output_parts.append("")
        output_parts.append("</files>")
        final_output = "\n".join(output_parts)
        try: pyperclip.copy(final_output); self.notify(f"{files_processed} files packed & copied!", severity="information", timeout=4); self.status_message = "Prompt copied."
        except pyperclip.PyperclipException as e: self.log(f"Clipboard error: {e}"); self.notify("Clipboard error.", severity="error", timeout=5); self.status_message = "Clipboard error."
        except Exception as e: self.log(f"Pack error: {e}"); self.notify("Unexpected error.", severity="error", timeout=5); self.status_message = "Error packing."


if __name__ == "__main__":
    import sys
    initial_folder = None
    if len(sys.argv) > 1:
        path_arg = Path(sys.argv[1])
        if path_arg.is_dir(): initial_folder = path_arg
        else: print(f"Warning: Path '{path_arg}' not valid directory. Starting without project.")
    app = RepoPackerApp(initial_path=initial_folder); app.run()
