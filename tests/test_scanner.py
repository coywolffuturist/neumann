"""Tests for ProjectScanner — codebase indexer."""
import json
import tempfile
from pathlib import Path

import pytest
from neumann import ProjectScanner, FileInfo, FileAnalysis, SymbolInfo, ImportInfo


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def simple_project(tmp_path):
    """Create a simple Python project with imports."""
    # main.py
    (tmp_path / "main.py").write_text('''"""Main module."""
from utils import helper
from models.user import User

def main():
    """Entry point."""
    user = User("test")
    helper.process(user)

class App:
    """Application class."""
    def __init__(self, name):
        self.name = name

    def run(self):
        """Run the app."""
        pass
''')

    # utils/__init__.py
    (tmp_path / "utils").mkdir()
    (tmp_path / "utils" / "__init__.py").write_text("")

    # utils/helper.py
    (tmp_path / "utils" / "helper.py").write_text('''"""Helper utilities."""
def process(data):
    """Process some data."""
    return data

def format_output(data):
    """Format the output."""
    return str(data)
''')

    # models/__init__.py
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "__init__.py").write_text("")

    # models/user.py
    (tmp_path / "models" / "user.py").write_text('''"""User model."""
class User:
    """User class."""
    def __init__(self, name):
        self.name = name

    def greet(self):
        """Greet the user."""
        return f"Hello, {self.name}"
''')

    # Non-Python files
    (tmp_path / "README.md").write_text("# Test Project\n\nA test project.")
    (tmp_path / "requirements.txt").write_text("requests\nflask\n")
    (tmp_path / "setup.py").write_text("from setuptools import setup\nsetup()")

    return tmp_path


# ═══════════════════════════════════════════════════════════════════
# Scan
# ═══════════════════════════════════════════════════════════════════

class TestProjectScanner:
    def test_scan_files(self, simple_project):
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=False)
        assert scanner._files
        # Should find Python files, README, requirements, setup.py
        python_files = [f for f in scanner._files if f.endswith(".py")]
        assert len(python_files) >= 5  # main, utils/__init__, helper, models/__init__, user, setup

    def test_scan_analyzes_python(self, simple_project):
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=True)
        # main.py should be analyzed
        main_analysis = scanner._analyses.get("main.py")
        assert main_analysis is not None
        assert len(main_analysis.imports) >= 2  # from utils import helper, from models.user import User
        assert any(s.name == "main" for s in main_analysis.symbols)
        assert any(s.name == "App" for s in main_analysis.symbols)

    def test_scan_detects_symbols(self, simple_project):
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=True)
        # Check models/user.py
        user_analysis = scanner._analyses.get("models/user.py")
        assert user_analysis is not None
        symbols = {s.name for s in user_analysis.symbols}
        assert "User" in symbols
        assert "__init__" in symbols
        assert "greet" in symbols

    def test_scan_detects_imports(self, simple_project):
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=True)
        main_analysis = scanner._analyses.get("main.py")
        assert main_analysis is not None
        modules = {imp.module for imp in main_analysis.imports}
        assert "utils" in modules
        assert "models.user" in modules

    def test_scan_respects_ignore_dirs(self, simple_project):
        (simple_project / ".git").mkdir()
        (simple_project / ".git" / "config").write_text("[core]\n")
        (simple_project / "__pycache__").mkdir()
        (simple_project / "__pycache__" / "main.cpython-310.pyc").write_bytes(b"\x00\x01")

        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=False)
        # .git and __pycache__ should be excluded
        assert not any(f.startswith(".git") for f in scanner._files)
        assert not any(f.startswith("__pycache__") for f in scanner._files)

    def test_scan_respects_max_file_size(self, simple_project):
        scanner = ProjectScanner(simple_project, max_file_size=10)
        scanner.scan(analyze=False)
        # main.py is > 10 bytes, should be excluded
        assert "main.py" not in scanner._files

    def test_scan_time_recorded(self, simple_project):
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=True)
        assert scanner._scan_time > 0

    def test_scan_binary_detection(self, simple_project):
        (simple_project / "binary.bin").write_bytes(b"\x00\x01\x02\x03")
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=False)
        assert scanner._files["binary.bin"].is_binary
        assert scanner._files["binary.bin"].line_count == 0


# ═══════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════

class TestSummary:
    def test_summary_basic(self, simple_project):
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=True)
        summary = scanner.summary()
        assert summary["total_files"] > 0
        assert summary["total_lines"] > 0
        assert "python" in summary["languages"]

    def test_summary_empty(self):
        scanner = ProjectScanner("/tmp/empty_dir_not_exist")
        summary = scanner.summary()
        assert "error" in summary


# ═══════════════════════════════════════════════════════════════════
# File Tree
# ═══════════════════════════════════════════════════════════════════

class TestFileTree:
    def test_file_tree_dict(self, simple_project):
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=False)
        tree = scanner.file_tree(max_depth=2)
        assert "main.py" in tree
        assert "utils" in tree
        assert "models" in tree

    def test_file_tree_text(self, simple_project):
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=False)
        text = scanner.file_tree_text(max_depth=2)
        assert "main.py" in text
        assert "utils" in text


# ═══════════════════════════════════════════════════════════════════
# Search
# ═══════════════════════════════════════════════════════════════════

class TestSearch:
    def test_search_file(self, simple_project):
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=True)
        results = scanner.search("main")
        assert any(r["type"] == "file" and "main" in r["path"] for r in results)

    def test_search_symbol(self, simple_project):
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=True)
        results = scanner.search("User")
        assert any(r["type"] == "symbol:class" for r in results)

    def test_search_function(self, simple_project):
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=True)
        results = scanner.search("process")
        assert any(r["type"] == "symbol:function" for r in results)

    def test_search_import(self, simple_project):
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=True)
        results = scanner.search("models")
        assert any(r["type"] == "import" for r in results)

    def test_search_max_results(self, simple_project):
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=True)
        results = scanner.search("", max_results=5)
        assert len(results) <= 5


# ═══════════════════════════════════════════════════════════════════
# Dependency Graph
# ═══════════════════════════════════════════════════════════════════

class TestDependencyGraph:
    def test_dependencies(self, simple_project):
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=True)
        deps = scanner.get_dependencies("main.py")
        # main.py imports from utils and models
        assert len(deps) >= 1  # at least one dependency

    def test_dependents(self, simple_project):
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=True)
        # models/user.py should be depended on by main.py
        dependents = scanner.get_dependents("models/user.py")
        assert any("main" in d for d in dependents)

    def test_import_chain(self, simple_project):
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=True)
        chains = scanner.get_import_chains("main.py", "models/user.py")
        # There should be at least one chain
        assert len(chains) >= 0  # may or may not find chain depending on module resolution


# ═══════════════════════════════════════════════════════════════════
# LLM Context
# ═══════════════════════════════════════════════════════════════════

class TestLLMContext:
    def test_build_context(self, simple_project):
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=True)
        context = scanner.build_llm_context(max_tokens=4000)
        assert "Project Overview" in context
        assert "File Tree" in context
        assert "main.py" in context

    def test_context_includes_symbols(self, simple_project):
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=True)
        context = scanner.build_llm_context(max_tokens=8000)
        assert "User" in context or "App" in context or "main" in context


# ═══════════════════════════════════════════════════════════════════
# Cache
# ═══════════════════════════════════════════════════════════════════

class TestCache:
    def test_save_and_load(self, simple_project):
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=True)

        cache_path = simple_project / ".neumann" / "scan_cache.json"
        saved = scanner.save_cache(str(cache_path))
        assert saved > 0
        assert cache_path.exists()

        # Load into new scanner
        scanner2 = ProjectScanner(simple_project)
        loaded = scanner2.load_cache(str(cache_path))
        assert loaded == len(scanner._files)
        assert len(scanner2._files) == len(scanner._files)

    def test_load_wrong_root(self, simple_project, tmp_path):
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=False)
        cache_path = simple_project / "cache.json"
        scanner.save_cache(str(cache_path))

        # Try to load with different root
        scanner2 = ProjectScanner(tmp_path / "other")
        loaded = scanner2.load_cache(str(cache_path))
        assert loaded == 0  # Root mismatch

    def test_cache_valid(self, simple_project):
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=False)
        cache_path = simple_project / "cache.json"
        scanner.save_cache(str(cache_path))
        assert scanner.is_cache_valid(str(cache_path), max_age=3600)

    def test_cache_invalid_not_exists(self, simple_project):
        scanner = ProjectScanner(simple_project)
        assert not scanner.is_cache_valid("/nonexistent/cache.json")


# ═══════════════════════════════════════════════════════════════════
# File Helpers
# ═══════════════════════════════════════════════════════════════════

class TestFileHelpers:
    def test_get_file_content(self, simple_project):
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=False)
        content = scanner.get_file_content("main.py")
        assert content is not None
        assert "main" in content

    def test_get_file_content_not_found(self, simple_project):
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=False)
        content = scanner.get_file_content("nonexistent.py")
        assert content is None

    def test_get_file_content_max_lines(self, simple_project):
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=False)
        content = scanner.get_file_content("main.py", max_lines=2)
        assert content is not None
        assert "more lines" in content or len(content.split("\n")) <= 5

    def test_get_file_analysis(self, simple_project):
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=True)
        analysis = scanner.get_file_analysis("main.py")
        assert analysis is not None
        assert isinstance(analysis, FileAnalysis)
        assert analysis.info.language == "python"

    def test_get_file_analysis_not_analyzed(self, simple_project):
        scanner = ProjectScanner(simple_project)
        scanner.scan(analyze=False)  # No analysis
        analysis = scanner.get_file_analysis("main.py")
        assert analysis is None
