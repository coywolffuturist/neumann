"""Project Scanner — codebase indexer for autonomous agents.

Scans a project directory and builds a structured index of:
- File tree with types, sizes, and modification times
- Imports and dependencies per file
- Symbols (functions, classes, methods) per file
- Cross-file relationships (who imports whom)
- Project metadata (language distribution, framework detection)

This gives the agent "eyes" on the entire codebase — not just one file.

Usage:
    from neumann.scanner import ProjectScanner

    scanner = ProjectScanner("/home/user/project")
    scanner.scan()

    # Get project overview
    summary = scanner.summary()
    print(summary["languages"])
    print(summary["file_count"])
    print(summary["dependencies"])

    # Get context for LLM
    context = scanner.build_llm_context()
    # → Structured text the agent can inject into LLM prompts

    # Find files related to a topic
    matches = scanner.search("authentication")
    print(matches)

    # Save/cache for fast re-scans
    scanner.save_cache("/home/user/project/.neumann/scan_cache.json")
    scanner.load_cache("/home/user/project/.neumann/scan_cache.json")
"""
from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


# ═══════════════════════════════════════════════════════════════════
# Data Types
# ═══════════════════════════════════════════════════════════════════

@dataclass
class FileInfo:
    """Metadata about a single file."""
    path: str
    size: int
    modified: float
    language: str = ""
    line_count: int = 0
    is_binary: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ImportInfo:
    """An import statement found in a file."""
    module: str  # e.g. "os.path", "neumann.tools"
    names: list[str] = field(default_factory=list)  # e.g. ["join", "basename"]
    is_from: bool = False  # True for "from x import y"
    line: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SymbolInfo:
    """A function, class, or method defined in a file."""
    name: str
    kind: str  # "function" | "class" | "method"
    line: int
    end_line: int = 0
    args: list[str] = field(default_factory=list)
    docstring: str = ""
    decorators: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FileAnalysis:
    """Complete analysis of a single file."""
    info: FileInfo
    imports: list[ImportInfo] = field(default_factory=list)
    symbols: list[SymbolInfo] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)  # resolved file paths
    dependents: list[str] = field(default_factory=list)  # files that import this

    def to_dict(self) -> dict[str, Any]:
        return {
            "info": self.info.to_dict(),
            "imports": [i.to_dict() for i in self.imports],
            "symbols": [s.to_dict() for s in self.symbols],
            "dependencies": self.dependencies,
            "dependents": self.dependents,
        }


# ═══════════════════════════════════════════════════════════════════
# Language Detection
# ═══════════════════════════════════════════════════════════════════

_EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".r": "r",
    ".R": "r",
    ".sql": "sql",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".md": "markdown",
    ".txt": "text",
    ".html": "html",
    ".css": "css",
    ".scss": "css",
    ".xml": "xml",
    ".dockerfile": "docker",
    "Dockerfile": "docker",
    "Makefile": "make",
    ".lua": "lua",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hs": "haskell",
    ".zig": "zig",
    ".nim": "nim",
    ".dart": "dart",
    ".tf": "terraform",
    ".proto": "protobuf",
}

_CODE_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs",
                    ".java", ".c", ".cpp", ".rb", ".php", ".swift",
                    ".kt", ".scala", ".ex", ".exs"}

# ═══════════════════════════════════════════════════════════════════
# Ignore Patterns
# ═══════════════════════════════════════════════════════════════════

_DEFAULT_IGNORE_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv",
    "venv", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".next", ".nuxt", ".svelte-kit", "dist", "build", "target",
    "vendor", ".eggs", "*.egg-info", ".cache", ".hypothesis",
    ".terraform", ".serverless", "coverage", ".scannerwork",
}

_DEFAULT_IGNORE_FILES = {
    "*.pyc", "*.pyo", "*.pyd", "*.so", "*.dylib", "*.dll", "*.exe",
    "*.class", "*.o", "*.obj", "*.a", "*.lib", "*.la", "*.lo",
    "*.log", "*.lock", "*.pid", "*.seed", "*.elc", "*.swp", "*.swo",
    ".DS_Store", "Thumbs.db", "*.min.js", "*.min.css",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "poetry.lock", "Pipfile.lock", "Gemfile.lock",
    "composer.lock", "Cargo.lock", "go.sum",
}


# ═══════════════════════════════════════════════════════════════════
# Project Scanner
# ═══════════════════════════════════════════════════════════════════

class ProjectScanner:
    """Scans a project directory and builds a structured index."""

    def __init__(
        self,
        root_path: str | Path,
        ignore_dirs: set[str] | None = None,
        ignore_files: set[str] | None = None,
        max_file_size: int = 1_048_576,  # 1MB
    ) -> None:
        self.root = Path(root_path).resolve()
        self.ignore_dirs = ignore_dirs or set(_DEFAULT_IGNORE_DIRS)
        self.ignore_files = ignore_files or set(_DEFAULT_IGNORE_FILES)
        self.max_file_size = max_file_size

        # Scan results
        self._files: dict[str, FileInfo] = {}
        self._analyses: dict[str, FileAnalysis] = {}
        self._dep_graph: dict[str, set[str]] = {}  # file -> set of files it depends on
        self._reverse_graph: dict[str, set[str]] = {}  # file -> set of files that depend on it
        self._scan_time: float = 0

    # ── main scan ──────────────────────────────────────────────────

    def scan(self, analyze: bool = True) -> "ProjectScanner":
        """Scan the project directory.
        
        Args:
            analyze: If True, also parse imports, symbols, and build
                     dependency graph. If False, only file tree is scanned.
        """
        start = time.perf_counter()
        self._files.clear()
        self._analyses.clear()
        self._dep_graph.clear()
        self._reverse_graph.clear()

        # Phase 1: Walk file tree
        for dirpath, dirnames, filenames in os.walk(self.root):
            # Filter ignored directories
            dirnames[:] = [
                d for d in dirnames
                if d not in self.ignore_dirs and not any(
                    fnmatch.fnmatch(d, pat) for pat in self.ignore_dirs
                )
            ]
            # Sort for deterministic order
            dirnames.sort()

            for fname in sorted(filenames):
                if any(fnmatch.fnmatch(fname, pat) for pat in self.ignore_files):
                    continue

                fpath = Path(dirpath) / fname
                rel_path = str(fpath.relative_to(self.root))

                try:
                    stat = fpath.stat()
                    if stat.st_size > self.max_file_size:
                        continue

                    info = FileInfo(
                        path=rel_path,
                        size=stat.st_size,
                        modified=stat.st_mtime,
                        language=self._detect_language(fname, fpath),
                        line_count=self._count_lines(fpath) if not self._is_binary(fpath) else 0,
                        is_binary=self._is_binary(fpath),
                    )
                    self._files[rel_path] = info
                    self._dep_graph[rel_path] = set()
                    self._reverse_graph[rel_path] = set()

                except (OSError, PermissionError):
                    continue

        # Phase 2: Analyze code files
        if analyze:
            self._analyze_files()
            self._build_dependency_graph()

        self._scan_time = time.perf_counter() - start
        return self

    # ── summary ────────────────────────────────────────────────────

    def summary(self) -> dict[str, Any]:
        """Get a high-level summary of the project."""
        if not self._files:
            return {"error": "No files scanned. Call scan() first."}

        lang_counts: dict[str, int] = {}
        lang_lines: dict[str, int] = {}
        lang_bytes: dict[str, int] = {}
        for f in self._files.values():
            lang = f.language or "other"
            lang_counts[lang] = lang_counts.get(lang, 0) + 1
            lang_lines[lang] = lang_lines.get(lang, 0) + f.line_count
            lang_bytes[lang] = lang_bytes.get(lang, 0) + f.size

        total_files = len(self._files)
        total_lines = sum(f.line_count for f in self._files.values())
        total_bytes = sum(f.size for f in self._files.values())

        # Top dependencies
        dep_counts: dict[str, int] = {}
        for analysis in self._analyses.values():
            for imp in analysis.imports:
                mod = imp.module.split(".")[0]  # top-level module
                dep_counts[mod] = dep_counts.get(mod, 0) + 1
        top_deps = sorted(dep_counts.items(), key=lambda x: -x[1])[:20]

        return {
            "root": str(self.root),
            "scan_time_seconds": round(self._scan_time, 3),
            "total_files": total_files,
            "total_lines": total_lines,
            "total_bytes": total_bytes,
            "total_code_files": sum(1 for f in self._files.values() if f.language in _CODE_EXTENSIONS),
            "languages": {
                lang: {
                    "files": count,
                    "lines": lang_lines.get(lang, 0),
                    "bytes": lang_bytes.get(lang, 0),
                }
                for lang, count in sorted(lang_counts.items(), key=lambda x: -x[1])
            },
            "top_dependencies": [{"module": m, "imports": c} for m, c in top_deps],
            "total_symbols": sum(len(a.symbols) for a in self._analyses.values()),
            "total_imports": sum(len(a.imports) for a in self._analyses.values()),
        }

    # ── file tree ──────────────────────────────────────────────────

    def file_tree(self, max_depth: int = 5) -> dict[str, Any]:
        """Get the project file tree as a nested dict."""
        tree: dict[str, Any] = {}

        for rel_path in sorted(self._files.keys()):
            parts = rel_path.split(os.sep)
            if len(parts) > max_depth + 1:
                continue
            node = tree
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = {
                "size": self._files[rel_path].size,
                "language": self._files[rel_path].language,
                "lines": self._files[rel_path].line_count,
            }

        return tree

    def file_tree_text(self, max_depth: int = 5, prefix: str = "") -> str:
        """Get the file tree as formatted text (like `tree` command)."""
        lines: list[str] = []
        self._build_tree_text(self.file_tree(max_depth), lines, prefix)
        return "\n".join(lines)

    def _build_tree_text(self, tree: dict, lines: list[str], prefix: str) -> None:
        items = sorted(tree.items())
        for i, (name, content) in enumerate(items):
            is_last = i == len(items) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{name}")
            if isinstance(content, dict) and "size" not in content:
                extension = "    " if is_last else "│   "
                self._build_tree_text(content, lines, prefix + extension)

    # ── search ─────────────────────────────────────────────────────

    def search(self, query: str, max_results: int = 50) -> list[dict[str, Any]]:
        """Search for files and symbols matching a query string."""
        query_lower = query.lower()
        results: list[dict[str, Any]] = []

        # Search filenames
        for path, info in self._files.items():
            if query_lower in path.lower():
                results.append({
                    "type": "file",
                    "path": path,
                    "language": info.language,
                    "lines": info.line_count,
                    "score": self._score_filename_match(path, query_lower),
                })

        # Search symbols
        for path, analysis in self._analyses.items():
            for sym in analysis.symbols:
                if query_lower in sym.name.lower():
                    results.append({
                        "type": f"symbol:{sym.kind}",
                        "path": path,
                        "name": sym.name,
                        "line": sym.line,
                        "docstring": sym.docstring[:200],
                        "score": self._score_symbol_match(sym, query_lower),
                    })

        # Search imports
        for path, analysis in self._analyses.items():
            for imp in analysis.imports:
                if query_lower in imp.module.lower():
                    results.append({
                        "type": "import",
                        "path": path,
                        "module": imp.module,
                        "names": imp.names,
                        "score": 0.5,
                    })

        # Sort by score, limit
        results.sort(key=lambda x: -x.get("score", 0))
        return results[:max_results]

    @staticmethod
    def _score_filename_match(path: str, query: str) -> float:
        """Score a filename match (exact = 1.0, partial = 0.3-0.7)."""
        basename = path.rsplit(os.sep, 1)[-1].lower()
        if basename == query:
            return 1.0
        if basename.startswith(query):
            return 0.8
        if query in basename:
            return 0.5
        if query in path.lower():
            return 0.3
        return 0.0

    @staticmethod
    def _score_symbol_match(sym: SymbolInfo, query: str) -> float:
        """Score a symbol match."""
        name = sym.name.lower()
        if name == query:
            return 1.0
        if name.startswith(query):
            return 0.8
        if query in name:
            return 0.6
        if sym.docstring and query in sym.docstring.lower():
            return 0.4
        return 0.2

    # ── dependency graph ───────────────────────────────────────────

    def get_dependencies(self, file_path: str) -> list[str]:
        """Get files that the given file depends on."""
        return sorted(self._dep_graph.get(file_path, set()))

    def get_dependents(self, file_path: str) -> list[str]:
        """Get files that depend on the given file."""
        return sorted(self._reverse_graph.get(file_path, set()))

    def get_import_chains(self, from_file: str, to_file: str) -> list[list[str]]:
        """Find import chains between two files (BFS, max depth 5)."""
        if from_file == to_file:
            return [[from_file]]

        visited = {from_file}
        queue: list[list[str]] = [[from_file]]

        while queue:
            path = queue.pop(0)
            current = path[-1]

            for dep in self._dep_graph.get(current, set()):
                if dep in visited:
                    continue
                new_path = path + [dep]
                if dep == to_file:
                    return [new_path]
                if len(new_path) < 6:
                    visited.add(dep)
                    queue.append(new_path)

        return []

    # ── LLM context builder ────────────────────────────────────────

    def build_llm_context(self, max_tokens: int = 8000) -> str:
        """Build a structured context string for LLM injection.
        
        Includes:
        1. Project overview (summary)
        2. File tree
        3. Top-level imports per file
        4. Public symbols (functions, classes) per file
        5. Dependency overview
        """
        parts: list[str] = []
        remaining = max_tokens

        # 1. Summary
        summary = self.summary()
        summary_text = json.dumps({
            "total_files": summary["total_files"],
            "total_lines": summary["total_lines"],
            "languages": {lang: data["files"] for lang, data in summary.get("languages", {}).items()},
            "top_dependencies": summary.get("top_dependencies", [])[:10],
        }, indent=2)
        parts.append(f"## Project Overview\n```json\n{summary_text}\n```\n")
        remaining -= len(summary_text) // 3  # rough token estimate

        # 2. File tree
        tree_text = self.file_tree_text(max_depth=4)
        parts.append(f"## File Tree\n```\n{tree_text}\n```\n")
        remaining -= len(tree_text) // 3

        # 3. File analyses (top files by size, until token budget)
        sorted_files = sorted(
            self._analyses.items(),
            key=lambda x: x[1].info.size,
            reverse=True,
        )

        for path, analysis in sorted_files:
            if remaining <= 0:
                parts.append(f"\n... ({len(sorted_files) - len(parts)} more files not shown)")
                break

            file_context = self._format_file_context(analysis)
            parts.append(file_context)
            remaining -= len(file_context) // 3

        # 4. Dependency overview
        if self._dep_graph:
            dep_lines = []
            for path, deps in sorted(self._dep_graph.items()):
                if deps:
                    dep_names = [os.path.basename(d) for d in sorted(deps)[:5]]
                    dep_lines.append(f"- `{path}` → {', '.join(f'`{d}`' for d in dep_names)}")
            if dep_lines:
                parts.append(f"\n## Dependencies\n" + "\n".join(dep_lines[:30]) + "\n")

        return "\n".join(parts)

    def _format_file_context(self, analysis: FileAnalysis) -> str:
        """Format a single file's analysis for LLM context."""
        lines = [f"\n### `{analysis.info.path}`"]
        lines.append(f"- Language: {analysis.info.language}")
        lines.append(f"- Lines: {analysis.info.line_count}")

        if analysis.imports:
            import_strs = []
            for imp in analysis.imports[:10]:
                if imp.is_from:
                    names = ", ".join(imp.names[:5])
                    import_strs.append(f"from {imp.module} import {names}")
                else:
                    import_strs.append(f"import {imp.module}")
            lines.append(f"- Imports: {', '.join(import_strs[:5])}")

        if analysis.symbols:
            sym_lines = []
            for sym in analysis.symbols[:20]:
                if sym.kind == "class":
                    sym_lines.append(f"  - class {sym.name}")
                elif sym.kind == "function":
                    args = ", ".join(sym.args[:3])
                    sym_lines.append(f"  - def {sym.name}({args})")
                elif sym.kind == "method":
                    args = ", ".join(a for a in sym.args[:3] if a != "self")
                    sym_lines.append(f"  - {sym.name}({args})")
            lines.append(f"- Symbols:")
            lines.extend(sym_lines[:15])

        return "\n".join(lines)

    # ── cache ──────────────────────────────────────────────────────

    def save_cache(self, path: str | Path) -> int:
        """Save scan results to a cache file."""
        data = {
            "root": str(self.root),
            "scan_time": self._scan_time,
            "files": {k: v.to_dict() for k, v in self._files.items()},
            "analyses": {k: v.to_dict() for k, v in self._analyses.items()},
            "dep_graph": {k: list(v) for k, v in self._dep_graph.items()},
            "reverse_graph": {k: list(v) for k, v in self._reverse_graph.items()},
        }
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2))
        return len(data["files"])

    def load_cache(self, path: str | Path) -> int:
        """Load scan results from a cache file."""
        p = Path(path)
        if not p.exists():
            return 0

        data = json.loads(p.read_text())

        # Verify root matches
        if data.get("root") != str(self.root):
            return 0

        self._scan_time = data.get("scan_time", 0)
        self._files = {
            k: FileInfo(**v) for k, v in data.get("files", {}).items()
        }
        self._analyses = {}
        for k, v in data.get("analyses", {}).items():
            self._analyses[k] = self._dict_to_analysis(v)

        self._dep_graph = {k: set(v) for k, v in data.get("dep_graph", {}).items()}
        self._reverse_graph = {k: set(v) for k, v in data.get("reverse_graph", {}).items()}

        return len(self._files)

    def is_cache_valid(self, path: str | Path, max_age: float = 3600) -> bool:
        """Check if cache file exists and is not too old."""
        p = Path(path)
        if not p.exists():
            return False
        return (time.time() - p.stat().st_mtime) < max_age

    # ── file helpers ───────────────────────────────────────────────

    def get_file_content(self, file_path: str, max_lines: int = 500) -> str | None:
        """Get the content of a file in the project."""
        full_path = self.root / file_path
        if not full_path.exists() or not full_path.is_file():
            return None
        try:
            lines = full_path.read_text(errors="replace").splitlines()
            content = "\n".join(lines[:max_lines])
            if len(lines) > max_lines:
                content += f"\n\n... ({len(lines) - max_lines} more lines)"
            return content
        except (OSError, PermissionError):
            return None

    def get_file_analysis(self, file_path: str) -> FileAnalysis | None:
        """Get the analysis of a specific file."""
        return self._analyses.get(file_path)

    # ── private: analysis ──────────────────────────────────────────

    def _analyze_files(self) -> None:
        """Parse Python files for imports and symbols."""
        for rel_path, info in self._files.items():
            if info.language != "python" or info.is_binary:
                continue

            full_path = self.root / rel_path
            try:
                content = full_path.read_text(errors="replace")
                tree = ast.parse(content, filename=rel_path)
            except (SyntaxError, OSError, UnicodeDecodeError):
                continue

            analysis = FileAnalysis(info=info)
            self._analyses[rel_path] = analysis

            for node in ast.walk(tree):
                # Imports
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        analysis.imports.append(ImportInfo(
                            module=alias.name,
                            line=node.lineno,
                        ))
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        analysis.imports.append(ImportInfo(
                            module=node.module,
                            names=[a.name for a in node.names],
                            is_from=True,
                            line=node.lineno,
                        ))

                # Symbols
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    analysis.symbols.append(SymbolInfo(
                        name=node.name,
                        kind="function",
                        line=node.lineno,
                        end_line=node.end_lineno or node.lineno,
                        args=[a.arg for a in node.args.args],
                        docstring=ast.get_docstring(node) or "",
                        decorators=[self._format_decorator(d) for d in node.decorator_list],
                    ))
                elif isinstance(node, ast.ClassDef):
                    analysis.symbols.append(SymbolInfo(
                        name=node.name,
                        kind="class",
                        line=node.lineno,
                        end_line=node.end_lineno or node.lineno,
                        docstring=ast.get_docstring(node) or "",
                        decorators=[self._format_decorator(d) for d in node.decorator_list],
                    ))
                    # Methods
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            analysis.symbols.append(SymbolInfo(
                                name=item.name,
                                kind="method",
                                line=item.lineno,
                                end_line=item.end_lineno or item.lineno,
                                args=[a.arg for a in item.args.args],
                                docstring=ast.get_docstring(item) or "",
                                decorators=[self._format_decorator(d) for d in item.decorator_list],
                            ))

    @staticmethod
    def _format_decorator(node: ast.expr) -> str:
        """Format a decorator node to a string."""
        if isinstance(node, ast.Name):
            return f"@{node.id}"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            return f"@{node.func.id}(...)"
        return "@..."

    def _build_dependency_graph(self) -> None:
        """Build cross-file dependency graph."""
        # Build a map of module name -> file paths
        module_to_files: dict[str, set[str]] = {}
        for rel_path in self._files:
            # Convert file path to module name
            mod = rel_path.replace(os.sep, ".").replace("/", ".")
            if mod.endswith(".py"):
                mod = mod[:-3]
            # Also add package variants
            parts = mod.split(".")
            for i in range(len(parts)):
                module_to_files[".".join(parts[:i+1])] = module_to_files.get(".".join(parts[:i+1]), set())
                module_to_files[".".join(parts[:i+1])].add(rel_path)

        # Build edges
        for rel_path, analysis in self._analyses.items():
            for imp in analysis.imports:
                # Find files that match this import
                module = imp.module
                for candidate in module_to_files.get(module, set()):
                    if candidate != rel_path:
                        self._dep_graph.setdefault(rel_path, set()).add(candidate)
                        self._reverse_graph.setdefault(candidate, set()).add(rel_path)
                        analysis.dependencies.append(candidate)

        # Populate dependents on analyses
        for rel_path, analysis in self._analyses.items():
            analysis.dependents = sorted(self._reverse_graph.get(rel_path, set()))

    # ── private: helpers ───────────────────────────────────────────

    @staticmethod
    def _detect_language(filename: str, filepath: Path) -> str:
        """Detect the programming language of a file."""
        if filename in _EXTENSION_MAP:
            return _EXTENSION_MAP[filename]
        ext = filepath.suffix.lower()
        return _EXTENSION_MAP.get(ext, "")

    @staticmethod
    def _count_lines(filepath: Path) -> int:
        """Count lines in a file."""
        try:
            with open(filepath, errors="replace") as f:
                return sum(1 for _ in f)
        except (OSError, PermissionError):
            return 0

    @staticmethod
    def _is_binary(filepath: Path) -> bool:
        """Check if a file is binary."""
        try:
            with open(filepath, "rb") as f:
                chunk = f.read(8192)
            return b"\x00" in chunk
        except (OSError, PermissionError):
            return True

    def _dict_to_analysis(self, data: dict[str, Any]) -> FileAnalysis:
        """Convert a dict back to FileAnalysis."""
        info_data = data.get("info", {})
        info = FileInfo(**info_data) if isinstance(info_data, dict) else info_data
        analysis = FileAnalysis(info=info)

        for imp_data in data.get("imports", []):
            if isinstance(imp_data, dict):
                analysis.imports.append(ImportInfo(**imp_data))

        for sym_data in data.get("symbols", []):
            if isinstance(sym_data, dict):
                analysis.symbols.append(SymbolInfo(**sym_data))

        analysis.dependencies = data.get("dependencies", [])
        analysis.dependents = data.get("dependents", [])
        return analysis
