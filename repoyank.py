#!/usr/bin/env python3
"""
RepoPacker - Enhanced TUI for packing repository files
Now with automatic path detection and full vim keybindings
"""

import asyncio
import os
from pathlib import Path
from typing import Set, List, Optional, Iterable
import fnmatch
from datetime import datetime

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.reactive import reactive
from textual.widgets import Header, Footer, DirectoryTree, Static, Label, Markdown
from textual.widgets._tree import TreeNode
from textual.widgets._directory_tree import DirEntry
from textual.binding import Binding
from textual.message import Message
from textual import events
from textual.style import Style
from rich.text import Text

import gitignore_parser
import pyperclip

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
    "__pycache__/", ".pytest_cache/", ".mypy_cache/", ".tox/",
    ".coverage", ".hypothesis/", "htmlcov/", ".nox/",
]
MAX_FILE_SIZE_MB = 10

# --- Helper Functions ---
def is_binary_heuristic(filepath: Path, sample_size=1024) -> bool:
    try:
        with open(filepath, 'rb') as f:
            sample = f.read(sample_size)
        return b'\0' in sample
    except Exception:
        return True

def get_file_size_mb(filepath: Path) -> float:
    try:
        return filepath.stat().st_size / (1024 * 1024)
    except OSError:
        return float('inf')

def get_current_directory() -> Path:
    """Get the directory from which the script was called"""
    return Path.cwd()


class VimDirectoryTree(DirectoryTree):
    """Directory tree with vim-style navigation and selection"""

    BINDINGS = [
        # Vim navigation
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("h", "collapse_or_parent", "Collapse/Parent", show=False),
        Binding("l", "expand_or_select", "Expand/Select", show=False),
        Binding("g", "go_to_top", "Top", show=False),
        Binding("shift+g", "go_to_bottom", "Bottom", show=False),
        # Selection
        Binding("space", "toggle_select", "Toggle Select", show=False),
        Binding("enter", "toggle_select", "Toggle Select", show=False),
        Binding("x", "toggle_select", "Toggle Select", show=False),
        Binding("shift+x", "toggle_recursive", "Toggle Recursive", show=False),
        # Navigation helpers
        Binding("ctrl+d", "page_down", "Page Down", show=False),
        Binding("ctrl+u", "page_up", "Page Up", show=False),
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
            if not self._is_path_ignored(path_obj):
                yield path_obj

    def _is_path_ignored(self, path_obj: Path) -> bool:
        abs_path_obj = path_obj.resolve() if not path_obj.is_absolute() else path_obj
        try:
            if not abs_path_obj.is_relative_to(self.project_root) and abs_path_obj != self.project_root:
                return True
        except ValueError:
            return True

        current_ignore_patterns = DEFAULT_IGNORES + self.additional_ignored_patterns
        for pattern_str in current_ignore_patterns:
            if pattern_str.endswith('/'):
                dir_name_to_ignore = pattern_str.rstrip('/')
                if dir_name_to_ignore in abs_path_obj.parts or \
                   (abs_path_obj.is_dir() and abs_path_obj.name == dir_name_to_ignore):
                    return True
            elif fnmatch.fnmatch(abs_path_obj.name, pattern_str):
                return True

        # Check gitignore
        path_to_check_str = str(abs_path_obj)
        dirs_to_check = [self.project_root]
        if abs_path_obj.parent != self.project_root and abs_path_obj.parent.is_relative_to(self.project_root):
            current_dir = abs_path_obj.parent
            while current_dir != self.project_root and current_dir != current_dir.parent:
                dirs_to_check.append(current_dir)
                if not current_dir.is_relative_to(self.project_root):
                    break
                current_dir = current_dir.parent

        for gitignore_dir in reversed(dirs_to_check):
            matcher = self._gitignore_matchers.get(gitignore_dir)
            if matcher is None:
                gf_path = gitignore_dir / ".gitignore"
                if gf_path.is_file():
                    try:
                        matcher = gitignore_parser.parse_gitignore(str(gf_path), base_dir=str(gitignore_dir))
                    except Exception:
                        matcher = lambda p: False
                else:
                    matcher = lambda p: False
                self._gitignore_matchers[gitignore_dir] = matcher
            if callable(matcher) and matcher(path_to_check_str):
                return True
        return False

    def _is_file_packable(self, file_path: Path) -> bool:
        """Check if a file should be included in packing"""
        if self._is_path_ignored(file_path):
            return False
        if is_binary_heuristic(file_path) or get_file_size_mb(file_path) > MAX_FILE_SIZE_MB:
            return False
        if file_path in self.selected_paths:
            return True
        # Check if any parent is selected
        current_parent = file_path.parent
        while current_parent != self.project_root.parent:
            if current_parent in self.selected_paths:
                return True
            if current_parent == self.project_root:
                break
            current_parent = current_parent.parent
        return False

    def render_label(self, node: TreeNode[DirEntry], base_style: Style, style: Style) -> Text:
        rendered_label = super().render_label(node, base_style, style)
        if node.data is None:
            return Text("Error: No data")

        node_path = Path(node.data.path)
        is_selected = node_path in self.selected_paths

        # Visual indicators
        if is_selected:
            prefix = Text("● ", style="bold green")  # Filled circle for selected
        else:
            prefix = Text("○ ", style="dim")  # Empty circle for unselected

        final_text = Text("")
        final_text.append(prefix)
        final_text.append(rendered_label)

        # Add indicator if file will be packed
        if node_path.is_file() and self._is_file_packable(node_path):
            final_text.append(Text(" ✓", style="bold cyan"))

        return final_text

    def _toggle_selection(self, path: Path):
        """Toggle selection state of a single item"""
        if path in self.selected_paths:
            self.selected_paths.discard(path)
        else:
            if not self._is_path_ignored(path):
                self.selected_paths.add(path)
            else:
                self.app.bell()
                return
        self.refresh()
        self.post_message(self.SelectionChanged(self.selected_paths.copy(), self.project_root))

    def _toggle_recursive(self, path: Path):
        """Toggle selection recursively for directories"""
        if path.is_file():
            self._toggle_selection(path)
            return

        new_state = path not in self.selected_paths
        self._apply_selection_recursive(path, new_state)
        self.refresh()
        self.post_message(self.SelectionChanged(self.selected_paths.copy(), self.project_root))

    def _apply_selection_recursive(self, path: Path, select: bool):
        """Apply selection state recursively"""
        if select:
            if not self._is_path_ignored(path):
                self.selected_paths.add(path)
        else:
            self.selected_paths.discard(path)

        if path.is_dir():
            try:
                for child in path.iterdir():
                    self._apply_selection_recursive(child, select)
            except OSError:
                pass

    # Vim-style actions
    def action_cursor_down(self):
        """Move cursor down (j)"""
        if self.cursor_node:
            super().action_cursor_down()

    def action_cursor_up(self):
        """Move cursor up (k)"""
        if self.cursor_node:
            super().action_cursor_up()

    def action_collapse_or_parent(self):
        """Collapse node or go to parent (h)"""
        if self.cursor_node:
            if self.cursor_node.is_expanded:
                self.cursor_node.collapse()
            elif self.cursor_node.parent:
                # Move to parent by going up until we reach it
                parent = self.cursor_node.parent
                while self.cursor_node != parent:
                    self.action_cursor_up()
                    if not self.cursor_node:
                        break

    def action_expand_or_select(self):
        """Expand directory or select file (l)"""
        if self.cursor_node and self.cursor_node.data:
            path = Path(self.cursor_node.data.path)
            if path.is_dir():
                if not self.cursor_node.is_expanded:
                    self.cursor_node.expand()
                elif self.cursor_node.children:
                    # Move down to first child
                    self.action_cursor_down()
            else:
                self._toggle_selection(path)

    def action_toggle_select(self):
        """Toggle selection of current item (space/enter/x)"""
        if self.cursor_node and self.cursor_node.data:
            self._toggle_selection(Path(self.cursor_node.data.path))

    def action_toggle_recursive(self):
        """Toggle selection recursively (X)"""
        if self.cursor_node and self.cursor_node.data:
            self._toggle_recursive(Path(self.cursor_node.data.path))

    def action_go_to_top(self):
        """Go to first item (g)"""
        # Move cursor to top by repeatedly going up
        while self.cursor_node and self.cursor_node.parent:
            self.action_cursor_up()
        # Now go to the very first node
        if self.cursor_node:
            while self.cursor_node.previous_sibling:
                self.action_cursor_up()

    def action_go_to_bottom(self):
        """Go to last item (G)"""
        # Move cursor to bottom by repeatedly going down
        for _ in range(1000):  # Large number to ensure we reach the bottom
            try:
                self.action_cursor_down()
            except:
                break

    def action_page_down(self):
        """Page down (Ctrl+d)"""
        for _ in range(10):
            self.action_cursor_down()

    def action_page_up(self):
        """Page up (Ctrl+u)"""
        for _ in range(10):
            self.action_cursor_up()

    def get_selected_files(self) -> List[Path]:
        """Get list of files that will be packed"""
        files: Set[Path] = set()
        processed: Set[Path] = set()

        for path in self.selected_paths:
            if path.is_file() and path not in processed:
                if self._is_file_packable(path):
                    files.add(path)
                processed.add(path)
            elif path.is_dir():
                try:
                    for item in path.rglob("*"):
                        if item.is_file() and item not in processed:
                            if not self._is_path_ignored(item) and \
                               not is_binary_heuristic(item) and \
                               get_file_size_mb(item) <= MAX_FILE_SIZE_MB:
                                files.add(item)
                            processed.add(item)
                except OSError:
                    pass

        # Convert to relative paths
        relative_files = []
        for file in sorted(files):
            try:
                if file.is_relative_to(self.project_root):
                    relative_files.append(file.relative_to(self.project_root))
            except ValueError:
                pass

        return sorted(relative_files)


class RepoPackerApp(App[None]):
    TITLE = "RepoPacker"
    CSS = """
    Screen {
        layout: vertical;
        overflow-y: auto;
    }

    Header {
        height: 3;
        background: $boost;
    }

    Footer {
        height: 1;
    }

    #main_container {
        layout: horizontal;
        height: 1fr;
    }

    #tree_panel {
        width: 60%;
        height: 100%;
        border-right: solid $primary;
    }

    #info_panel {
        width: 40%;
        height: 100%;
        padding: 0 1;
    }

    VimDirectoryTree {
        width: 100%;
        height: 100%;
        padding: 0 1;
    }

    #selected_files {
        width: 100%;
        height: 100%;
        overflow-y: auto;
    }

    #status_bar {
        dock: bottom;
        height: 1;
        background: $panel;
        color: $text;
        padding: 0 1;
    }

    .help_text {
        color: $text-muted;
        margin: 1 0;
    }
    """

    BINDINGS = [
        # Main actions
        Binding("y", "yank_to_clipboard", "Yank/Copy", show=True),
        Binding("Y", "yank_with_preview", "Yank+Preview", show=True),
        Binding("a", "select_all", "Select All", show=True),
        Binding("A", "deselect_all", "Clear All", show=True),
        Binding("r", "refresh_tree", "Refresh", show=True),
        Binding("/", "search", "Search", show=False),
        Binding("?", "show_help", "Help", show=True),
        Binding("q", "quit", "Quit", show=True),
        Binding("ctrl+c", "quit", "Quit", show=False),
    ]

    status_text = reactive("Ready")
    selected_count = reactive(0)

    def __init__(self):
        super().__init__()
        self.current_path = get_current_directory()
        self._tree: Optional[VimDirectoryTree] = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main_container"):
            with Vertical(id="tree_panel"):
                yield VimDirectoryTree(str(self.current_path), id="dir_tree")
            with ScrollableContainer(id="info_panel"):
                yield Markdown(id="selected_files")
        yield Static(self.status_text, id="status_bar")
        yield Footer()

    async def on_mount(self):
        """Initialize the app after mounting"""
        self._tree = self.query_one("#dir_tree", VimDirectoryTree)
        self.sub_title = f"📁 {self.current_path.name}"
        self.update_selected_files_display()
        self._tree.focus()

        # Show quick help
        self.status_text = "Vim keys: hjkl=nav, space/x=select, X=recursive, y=copy, ?=help"

    def watch_status_text(self, new_text: str):
        """Update status bar when status_text changes"""
        try:
            self.query_one("#status_bar", Static).update(new_text)
        except:
            pass

    def watch_selected_count(self, count: int):
        """Update status when selection count changes"""
        if count > 0:
            self.status_text = f"📝 {count} files selected | y=copy, Y=preview"
        else:
            self.status_text = "No files selected | space/x=select, X=recursive"

    async def on_vim_directory_tree_selection_changed(self, event: VimDirectoryTree.SelectionChanged):
        """Handle selection changes"""
        self.update_selected_files_display()

    def update_selected_files_display(self):
        """Update the selected files display"""
        if not self._tree:
            return

        files = self._tree.get_selected_files()
        self.selected_count = len(files)

        md_widget = self.query_one("#selected_files", Markdown)

        if not files:
            content = """# 📋 Selected Files

*No files selected*

---
### Quick Guide:
- `j/k` - Navigate up/down
- `h/l` - Collapse/expand
- `space/x` - Toggle selection
- `X` - Select folder recursively
- `y` - Copy to clipboard
- `?` - Show help
"""
        else:
            file_list = "\n".join([f"- `{f}`" for f in files[:50]])
            if len(files) > 50:
                file_list += f"\n- *... and {len(files) - 50} more*"

            total_size = sum(get_file_size_mb(self.current_path / f) for f in files)

            content = f"""# 📋 Selected Files ({len(files)})

**Total size:** {total_size:.2f} MB

{file_list}

---
Press `y` to copy to clipboard"""

        md_widget.update(content)

    async def action_yank_to_clipboard(self):
        """Copy selected files to clipboard (y)"""
        if not self._tree:
            return

        files = self._tree.get_selected_files()
        if not files:
            self.notify("No files selected!", severity="warning")
            return

        self.status_text = "Preparing content..."
        await asyncio.sleep(0.01)

        # Generate packed content
        output = self._generate_packed_content(files)

        # Copy to clipboard
        try:
            pyperclip.copy(output)
            self.notify(f"✅ Copied {len(files)} files to clipboard!", severity="information")
            self.status_text = f"✅ {len(files)} files copied to clipboard"
        except Exception as e:
            self.notify(f"❌ Clipboard error: {e}", severity="error")
            self.status_text = "Failed to copy to clipboard"

    async def action_yank_with_preview(self):
        """Copy with preview (Y)"""
        await self.action_yank_to_clipboard()

    def _generate_packed_content(self, files: List[Path]) -> str:
        """Generate the packed content for selected files"""
        lines = []

        # Header
        lines.extend([
            "<repository_contents>",
            f"Generated from: {self.current_path}",
            f"Timestamp: {datetime.now().isoformat()}",
            f"Files: {len(files)}",
            "",
            "<file_tree>",
        ])

        # File tree
        for f in files:
            lines.append(str(f).replace('\\', '/'))

        lines.extend(["</file_tree>", "", "<files>"])

        # File contents
        for rel_path in files:
            full_path = self.current_path / rel_path
            try:
                with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()

                lines.extend([
                    f'<file path="{str(rel_path).replace(chr(92), "/")}">',
                    content,
                    "</file>",
                    ""
                ])
            except Exception as e:
                lines.extend([
                    f'<file path="{str(rel_path).replace(chr(92), "/")}" error="{e}">',
                    f"Error reading file: {e}",
                    "</file>",
                    ""
                ])

        lines.append("</files>")
        lines.append("</repository_contents>")

        return "\n".join(lines)

    def action_select_all(self):
        """Select all files (a)"""
        if self._tree:
            self._tree._apply_selection_recursive(self._tree.project_root, True)
            self._tree.refresh()
            self._tree.post_message(
                VimDirectoryTree.SelectionChanged(
                    self._tree.selected_paths.copy(),
                    self._tree.project_root
                )
            )
            self.notify("Selected all files", severity="information")

    def action_deselect_all(self):
        """Clear all selections (A)"""
        if self._tree:
            self._tree.selected_paths.clear()
            self._tree.refresh()
            self._tree.post_message(
                VimDirectoryTree.SelectionChanged(
                    self._tree.selected_paths.copy(),
                    self._tree.project_root
                )
            )
            self.notify("Cleared all selections", severity="information")

    def action_refresh_tree(self):
        """Refresh the directory tree (r)"""
        if self._tree:
            self._tree.reload()
            self.notify("Tree refreshed", severity="information")

    def action_show_help(self):
        """Show help dialog (?)"""
        help_text = """
# RepoPacker Help

## Navigation (Vim-style)
- `j/k` - Move down/up
- `h/l` - Collapse/expand or navigate
- `g/G` - Go to top/bottom
- `Ctrl+d/u` - Page down/up

## Selection
- `space/x/Enter` - Toggle file/folder selection
- `X` - Toggle recursive selection
- `a` - Select all
- `A` - Clear all selections

## Actions
- `y` - Copy selected files to clipboard
- `Y` - Copy with preview
- `r` - Refresh tree
- `?` - Show this help
- `q` - Quit

## Notes
- Binary files are automatically excluded
- Files > 10MB are skipped
- .gitignore patterns are respected
"""
        self.notify(help_text, title="Help", severity="information", timeout=10)


if __name__ == "__main__":
    app = RepoPackerApp()
    app.run()
