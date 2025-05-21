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
    BINDINGS = [
        Binding("enter", "toggle_expand_or_select", "Toggle Expand/Select", show=False, priority=True),
        # Space will still trigger 'select_cursor' from base Tree bindings,
        # which in turn emits NodeSelected, handled by on_directory_tree_node_selected.
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

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        for path_obj in paths:
            if not self._is_path_ignored(path_obj): yield path_obj

    def _is_path_ignored(self, path_obj: Path) -> bool:
        abs_path_obj = path_obj.resolve() if not path_obj.is_absolute() else path_obj
        try:
            if not abs_path_obj.is_relative_to(self.project_root) and abs_path_obj != self.project_root: return True
        except ValueError: return True

        current_ignore_patterns = DEFAULT_IGNORES + self.additional_ignored_patterns
        for pattern_str in current_ignore_patterns:
            if pattern_str.endswith('/'):
                dir_name_to_ignore = pattern_str.rstrip('/')
                if dir_name_to_ignore in abs_path_obj.parts or \
                   (abs_path_obj.is_dir() and abs_path_obj.name == dir_name_to_ignore):
                    return True
            elif fnmatch.fnmatch(abs_path_obj.name, pattern_str):
                return True
        
        path_to_check_str = str(abs_path_obj)
        dirs_to_check_for_gitignore = [self.project_root]
        if abs_path_obj.parent != self.project_root and abs_path_obj.parent.is_relative_to(self.project_root):
            current_dir = abs_path_obj.parent
            while current_dir != self.project_root and current_dir != current_dir.parent :
                dirs_to_check_for_gitignore.append(current_dir)
                if not current_dir.is_relative_to(self.project_root): break
                current_dir = current_dir.parent
        
        for gitignore_dir in reversed(dirs_to_check_for_gitignore):
            matcher = self._gitignore_matchers.get(gitignore_dir)
            if matcher is None:
                gf_path = gitignore_dir / ".gitignore"
                if gf_path.is_file():
                    try: matcher = gitignore_parser.parse_gitignore(str(gf_path), base_dir=str(gitignore_dir))
                    except Exception as e: self.app.log(f"Warning: Parse {gf_path}: {e}"); matcher = lambda p: False
                else: matcher = lambda p: False
                self._gitignore_matchers[gitignore_dir] = matcher
            if callable(matcher) and matcher(path_to_check_str): return True
        return False

    def render_node(self, node: TreeNode[DirEntry]) -> str:
        rich_label = super().render_node(node)
        if node.data is None: return rich_label
        node_fs_path = Path(node.data.path)
        is_selected = node_fs_path in self.selected_paths
        prefix = "[green]✓[/] " if is_selected else "[dim]][ ] [/]"
        from rich.text import Text
        final_label = Text.from_markup(prefix); final_label.append(rich_label)
        return final_label

    def _toggle_single_node_selection(self, node_fs_path: Path):
        if node_fs_path in self.selected_paths:
            self.selected_paths.discard(node_fs_path)
        else:
            if self._is_path_ignored(node_fs_path):
                self.app.bell(); return
            self.selected_paths.add(node_fs_path)
        self.refresh()
        self.post_message(self.SelectionChanged(self.selected_paths.copy(), self.project_root))

    def _toggle_node_and_children_selection(self, node_fs_path: Path):
        if node_fs_path.is_file() and self._is_path_ignored(node_fs_path) and not (node_fs_path in self.selected_paths):
            self.app.bell(); return
        new_select_state = not (node_fs_path in self.selected_paths)
        self._apply_selection_recursive(node_fs_path, new_select_state)
        self.refresh()
        self.post_message(self.SelectionChanged(self.selected_paths.copy(), self.project_root))

    def _apply_selection_recursive(self, node_fs_path: Path, select_state: bool):
        if select_state:
            if not self._is_path_ignored(node_fs_path): self.selected_paths.add(node_fs_path)
        else: self.selected_paths.discard(node_fs_path)
        
        if node_fs_path.is_dir():
            try:
                for child_item_path in node_fs_path.iterdir():
                    self._apply_selection_recursive(child_item_path, select_state)
            except OSError as e: self.app.log(f"OS Error applying recursive selection for {node_fs_path}: {e}")

    def action_toggle_expand_or_select(self) -> None:
        """
        Called when 'enter' is pressed.
        - Expands/collapses directories.
        - Toggles selection for files.
        """
        if self.cursor_node and self.cursor_node.data:
            node_fs_path = Path(self.cursor_node.data.path)
            if node_fs_path.is_dir():
                self.action_toggle_node() # Built-in Tree action to expand/collapse
            elif node_fs_path.is_file():
                self._toggle_single_node_selection(node_fs_path)
            # else: (e.g. broken symlink, or other non-file/non-dir type if any) do nothing
        
    def on_directory_tree_node_selected(self, event: DirectoryTree.NodeSelected) -> None: # Click or Space
        event.stop()
        if event.node.data is None: return
        self._toggle_single_node_selection(Path(event.node.data.path))
        
    async def on_key(self, event: events.Key) -> None:
        if self.cursor_node is None or self.cursor_node.data is None :
            event.prevent_default(); return 
        
        if event.key == "j": self.action_cursor_down(); event.prevent_default()
        elif event.key == "k": self.action_cursor_up(); event.prevent_default()
        elif event.key in ("h", "l"): event.prevent_default()
        else: return

    def get_selected_final_files(self) -> List[Path]:
        collected_files: Set[Path] = set()
        current_selection_snapshot = list(self.selected_paths)
        for path_obj in current_selection_snapshot:
            if self._is_path_ignored(path_obj): continue
            if path_obj.is_dir():
                for item in path_obj.rglob("*"):
                    if item.is_file():
                        if not self._is_path_ignored(item) and \
                           not is_binary_heuristic(item) and \
                           get_file_size_mb(item) <= MAX_FILE_SIZE_MB:
                            collected_files.add(item)
            elif path_obj.is_file():
                 if not self._is_path_ignored(path_obj) and \
                    not is_binary_heuristic(path_obj) and \
                    get_file_size_mb(path_obj) <= MAX_FILE_SIZE_MB:
                    collected_files.add(path_obj)
        
        relative_collected_files = set()
        if self.project_root:
            for abs_file_path in sorted(list(collected_files)):
                try:
                    if abs_file_path.is_relative_to(self.project_root):
                         relative_collected_files.add(abs_file_path.relative_to(self.project_root))
                except ValueError: self.app.log(f"ValueError making {abs_file_path} relative to {self.project_root}")
        return sorted(list(relative_collected_files))

class PathInputScreen(ModalScreen[Optional[Path]]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    def __init__(self, recent_folders: List[Path]):
        super().__init__(); self.recent_folders = recent_folders
    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Select or Enter Project Path:", classes="dialog_title")
            if self.recent_folders:
                yield Label("Recent folders (press number or use Up/Down then Enter):")
                with ScrollableContainer(id="recent_list"):
                    for i, folder in enumerate(self.recent_folders):
                        yield Button(f"{i+1}. {str(folder)}", id=f"recent_{i}", classes="recent_folder_button")
            yield Input(placeholder="Type path or number for recent (then Enter)", id="path_input")
            with Horizontal(classes="dialog_buttons"):
                yield Button("OK", variant="primary", id="ok_button")
                yield Button("Cancel", variant="error", id="cancel_button")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        path_str = event.value.strip()
        if path_str.isdigit():
            try:
                index = int(path_str) - 1
                if 0 <= index < len(self.recent_folders):
                    self.dismiss(self.recent_folders[index].resolve()); return
            except ValueError: pass
        
        if path_str:
            path = Path(path_str).resolve()
            if path.is_dir(): self.dismiss(path)
            else:
                self.query_one(Input).placeholder = "Invalid path/number. Try again."
                self.query_one(Input).value = ""; self.app.bell()
        else: self.app.bell()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok_button":
            await self.on_input_submitted(Input.Submitted(self.query_one(Input), self.query_one(Input).value))
        elif event.button.id == "cancel_button": self.dismiss(None)
        elif event.button.id and event.button.id.startswith("recent_"):
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
    #dialog { align: center middle; width: 80%; max-width: 70; height: auto; max-height: 80%; border: thick $primary; background: $surface; padding: 1; }
    .dialog_title { width: 100%; text-align: center; margin-bottom: 1; }
    #recent_list { max-height: 10; overflow-y: auto; border: round $primary-lighten-2; margin-bottom:1; }
    #recent_list Button.recent_folder_button { width: 100%; margin-bottom: 1;}
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
        Binding("f5", "open_folder", "Open Folder", show=True),
        Binding("f6", "generate_packed_file", "Generate File", show=True),
        Binding("a", "select_all_tree", "Select All (Project)"),
        Binding("d", "deselect_all_tree", "Deselect All (Project)"),
        Binding("ctrl+a", "select_in_focused_folder", "Sel Content (Focused Dir)"),
        Binding("ctrl+d", "deselect_in_focused_folder", "Desel Content (Focused Dir)"),
    ]
    current_project_path: reactive[Optional[Path]] = reactive(None)
    status_message = reactive("Ready. F5 to Open.")

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
                    tree_instance.post_message(CheckableDirectoryTree.SelectionChanged(set(), tree_instance.project_root))
            else: self.query_one("#selected_files_md", Markdown).update("### Selected Files\n\n_None selected_")
        except Exception as e: self.log(f"Error in on_mount sidebar clearing: {e}")


    def watch_current_project_path(self, old_path: Optional[Path], new_path: Optional[Path]) -> None:
        try: tree_panel = self.query_one("#tree_panel"); tree_panel.remove_children()
        except NoMatches: self.log("Error: #tree_panel not found during watch"); return
        try: self.query_one("#selected_files_md", Markdown).update("### Selected Files\n\n_None selected_")
        except NoMatches: pass

        if new_path and new_path.is_dir():
            tree = CheckableDirectoryTree(str(new_path), id="dir_tree")
            tree_panel.mount(tree)
            # Ensure the tree is focused after mounting if it's the primary widget
            self.call_after_refresh(tree.focus)

            save_recent_folders(new_path, self.recent_folders); self.recent_folders = load_recent_folders()
            self.sub_title = str(new_path); self.status_message = f"Project: {new_path.name}. Select items."
        else:
            tree_panel.mount(Static("F5 to open a folder or select from recent.", id="tree_placeholder"))
            self.sub_title = "No Project"; self.status_message = "No project. F5 to Open."
            if old_path and not new_path: self.notify("Folder selection cancelled or failed.", severity="warning")

    def watch_status_message(self, new_message: str) -> None:
        try: self.query_one("#status_bar", Static).update(new_message)
        except NoMatches: pass

    async def on_checkable_directory_tree_selection_changed(self, event: CheckableDirectoryTree.SelectionChanged) -> None:
        try:
            md_widget = self.query_one("#selected_files_md", Markdown)
            if not event.selected_paths:
                md_widget.update("### Selected Files\n\n_None selected_")
                return

            tree = self.query_one(CheckableDirectoryTree)
            final_packable_files = tree.get_selected_final_files()

            if not final_packable_files:
                md_widget.update("### Selected Files\n\n_No packable files based on current selection._")
                return

            display_items = [f"- `{str(rel_path)}`" for rel_path in sorted(final_packable_files)]
            title = f"### Selected Files ({len(display_items)})"
            md_content = title + "\n\n" + "\n".join(display_items)
            md_widget.update(md_content)
        except NoMatches: self.log("Error: #selected_files_md or CheckableDirectoryTree widget not found for sidebar update.")
        except Exception as e: self.log(f"Error updating sidebar on selection change: {e}")

    def _is_path_ignored_for_app(self, path_obj: Path, project_root: Path) -> bool:
        abs_path_obj = path_obj.resolve() if not path_obj.is_absolute() else path_obj
        try:
            if not abs_path_obj.is_relative_to(project_root) and abs_path_obj != project_root: return True
        except ValueError: return True
        for pattern_str in DEFAULT_IGNORES:
            if pattern_str.endswith('/'):
                dir_name_to_ignore = pattern_str.rstrip('/')
                if dir_name_to_ignore in abs_path_obj.parts or \
                   (abs_path_obj.is_dir() and abs_path_obj.name == dir_name_to_ignore): return True
            elif fnmatch.fnmatch(abs_path_obj.name, pattern_str): return True
        return False

    async def action_open_folder(self) -> None:
        def set_new_path(path: Optional[Path]):
            if path: self.current_project_path = path
            elif not self.current_project_path: self.notify("No folder selected.", severity="information")
        await self.app.push_screen(PathInputScreen(self.recent_folders), set_new_path)

    def action_select_all_tree(self) -> None:
        try:
            tree = self.query_one(CheckableDirectoryTree)
            tree.selected_paths.clear()
            if tree.project_root:
                 tree._apply_selection_recursive(tree.project_root, select_state=True)
            tree.refresh()
            tree.post_message(CheckableDirectoryTree.SelectionChanged(tree.selected_paths.copy(), tree.project_root))
            final_selected_count = len(tree.selected_paths)
            self.status_message = f"{final_selected_count} items marked. Review tree and sidebar."
        except NoMatches: self.status_message = "No project tree loaded."; self.bell()
        except Exception as e: self.status_message = f"Error selecting all: {e}"; self.log(f"Select All Error: {e}"); self.bell()

    def action_deselect_all_tree(self) -> None:
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
                    self.status_message = "Focused item is not a directory. Use Space to toggle selection."
                    self.bell() 
            else:
                self.status_message = "No item focused in the tree."
                self.bell()
        except NoMatches:
            self.status_message = "No project tree loaded."
            self.bell()

    def action_select_in_focused_folder(self) -> None:
        self._operate_on_focused_folder_contents(select_state=True)

    def action_deselect_in_focused_folder(self) -> None:
        self._operate_on_focused_folder_contents(select_state=False)

    async def action_generate_packed_file(self) -> None:
        if not self.current_project_path:
            self.status_message = "Error: No project folder loaded."; self.bell(); return
        try:
            tree = self.query_one(CheckableDirectoryTree)
            selected_relative_paths = tree.get_selected_final_files()
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
                        save_dir = self.project_path.parent if self.project_path and self.project_path.parent != self.project_path else Path.home()
                        self.dismiss(save_dir / save_name)
                    else: self.app.bell()
                elif event.button.id == "copy_btn":
                    try:
                        import pyperclip 
                        pyperclip.copy(_final_output_str_for_clipboard)
                        self.app.notify("Content copied to clipboard!", severity="information")
                    except ImportError:
                        self.app.notify("Error: pyperclip module not found. Cannot copy to clipboard.", severity="error", timeout=5)
                        self.app.log("pyperclip module not found. Please install it to use copy to clipboard.")
                    except Exception as e:
                        self.app.notify(f"Clipboard error: {e}", severity="error")
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
        else: 
            if _final_output_str_for_clipboard and not self.query_one("#status_bar", Static).renderable == "Content copied to clipboard!":
                 self.status_message = "File generation/action completed or cancelled."


if __name__ == "__main__":
    import sys
    initial_folder = None
    if len(sys.argv) > 1:
        path_arg = Path(sys.argv[1])
        if path_arg.is_dir(): initial_folder = path_arg
        else: print(f"Warning: Provided path '{path_arg}' is not a valid directory. Starting without initial project.")
    app = RepoPackerApp(initial_path=initial_folder); app.run()
