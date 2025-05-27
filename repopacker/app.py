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

    def _is_node_effectively_selected_file(self, file_path: Path) -> bool:
        if self._is_path_ignored(file_path): return False
        if is_binary_heuristic(file_path) or get_file_size_mb(file_path) > MAX_FILE_SIZE_MB: return False
        if file_path in self.selected_paths: return True
        current_parent = file_path.parent
        while current_parent != self.project_root.parent and \
              (current_parent == self.project_root or current_parent.is_relative_to(self.project_root)):
            if current_parent in self.selected_paths: return True
            if current_parent == self.project_root: break
            prev_parent = current_parent
            current_parent = current_parent.parent
            if current_parent == prev_parent : break
        return False

    def render_label(
        self, node: TreeNode[DirEntry], base_style: Style, style: Style
    ) -> Text:
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
        if self.cursor_node and self.cursor_node.data:
            node_fs_path = Path(self.cursor_node.data.path)
            if node_fs_path.is_dir():
                self.action_toggle_node()
            elif node_fs_path.is_file():
                self._toggle_single_node_selection(node_fs_path)
        
    def on_directory_tree_node_selected(self, event: DirectoryTree.NodeSelected) -> None: # Click or Space
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

    def get_selected_final_files(self) -> List[Path]:
        collected_files: Set[Path] = set()
        already_processed_for_collection: Set[Path] = set()
        for path_obj in self.selected_paths:
            if path_obj.is_file() and path_obj not in already_processed_for_collection:
                if not self._is_path_ignored(path_obj) and \
                   not is_binary_heuristic(path_obj) and \
                   get_file_size_mb(path_obj) <= MAX_FILE_SIZE_MB:
                    collected_files.add(path_obj)
                already_processed_for_collection.add(path_obj)
        for path_obj in self.selected_paths:
            if path_obj.is_dir():
                try:
                    for item in path_obj.rglob("*"):
                        if item.is_file() and item not in already_processed_for_collection:
                            if not self._is_path_ignored(item) and \
                               not is_binary_heuristic(item) and \
                               get_file_size_mb(item) <= MAX_FILE_SIZE_MB:
                                collected_files.add(item)
                            already_processed_for_collection.add(item)
                except OSError as e: self.app.log(f"OS Error rglobbing {path_obj}: {e}")
        
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
        super().__init__()
        self.recent_folders = recent_folders
        self.input_widget: Optional[Input] = None 

    def compose(self) -> ComposeResult:
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
        path_str = event.value.strip()
        if path_str.isdigit():
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
        self.dismiss(None)


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
                    if tree_instance.project_root:
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
            self.call_after_refresh(tree.focus)

            save_recent_folders(new_path, self.recent_folders); self.recent_folders = load_recent_folders()
            self.sub_title = str(new_path); self.status_message = f"Project: {new_path.name}. Select items. 'c' to Copy Prompt."
        else:
            tree_panel.mount(Static("No project loaded. Open a folder to begin (F5).", id="tree_placeholder"))
            self.sub_title = "No Project"; self.status_message = "No project. Open (F5), Copy Prompt ('c')."
            if old_path and not new_path: self.notify("Folder selection cancelled or failed.", severity="warning")

    def watch_status_message(self, new_message: str) -> None:
        try: self.query_one("#status_bar", Static).update(new_message)
        except NoMatches: pass

    async def on_checkable_directory_tree_selection_changed(self, event: CheckableDirectoryTree.SelectionChanged) -> None:
        try:
            md_widget = self.query_one("#selected_files_md", Markdown)
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

    async def action_open_folder(self) -> None:
        def set_new_path(path: Optional[Path]):
            if path: self.current_project_path = path
            elif not self.current_project_path: self.notify("No folder selected.", severity="information")
        await self.app.push_screen(PathInputScreen(self.recent_folders), set_new_path)

    def action_select_all_tree(self) -> None:
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
                    self.status_message = "Focused item is not a directory."
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

    async def action_generate_packed_file(self) -> None: # "Copy Prompt"
        if not self.current_project_path:
            self.notify("Error: No project folder loaded.", severity="error", timeout=3); self.bell(); return
        try:
            tree = self.query_one(CheckableDirectoryTree)
            selected_relative_paths = tree.get_selected_final_files()
        except NoMatches: 
            self.notify("Error: Project tree not found.", severity="error", timeout=3); self.bell(); return
        
        if not selected_relative_paths:
            self.notify("Warning: No files selected or eligible for packing.", severity="warning", timeout=3); self.bell(); return
        
        output_parts = []
        
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
        
        output_parts.append("<directory_structure>")
        for rel_path in selected_relative_paths: 
            output_parts.append(str(rel_path).replace('\\', '/')) 
        output_parts.append("</directory_structure>")
        output_parts.append("") 
        
        output_parts.append("<files>")
        output_parts.append("This section contains the contents of the repository's selected files.")
        if selected_relative_paths:
             output_parts.append("")

        files_processed_count = 0
        self.status_message = "Preparing content for clipboard..."
        await asyncio.sleep(0.01)

        for i, rel_path in enumerate(selected_relative_paths):
            full_path = self.current_project_path / rel_path
            try:
                with open(full_path, 'r', encoding='utf-8', errors='replace') as f: content = f.read()
                normalized_rel_path = str(rel_path).replace('\\', '/')
                output_parts.append(f'<file path="{normalized_rel_path}">')
                output_parts.append(content)
                output_parts.append("</file>")
                if i < len(selected_relative_paths) - 1:
                    output_parts.append("") 
                files_processed_count += 1
            except Exception as e:
                self.log(f"Error reading file {full_path}: {e}")
                output_parts.append(f'<file path="{str(rel_path).replace("\\", "/")}">{os.linesep}Error reading file: {e}{os.linesep}</file>')
                if i < len(selected_relative_paths) - 1:
                    output_parts.append("")
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
        except Exception as e:
            self.log(f"Unexpected error during prompt generation/copy: {e}")
            self.notify("An unexpected error occurred. See logs.", severity="error", timeout=5)
            self.status_message = "Error generating prompt."


if __name__ == "__main__":
    import sys
    initial_folder = None
    if len(sys.argv) > 1:
        path_arg = Path(sys.argv[1])
        if path_arg.is_dir(): initial_folder = path_arg
        else: print(f"Warning: Provided path '{path_arg}' is not a valid directory. Starting without initial project.")
    app = RepoPackerApp(initial_path=initial_folder); app.run()
