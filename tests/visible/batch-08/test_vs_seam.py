"""Visible specification tests — prohibited constructs, committed credentials and the repository data policy (contract v0.8 header, §2, §3, §10, §12.5, §13.2).

Contract-only: written before any v0.8 implementation commit was supplied,
with no implementation code seen. These are static checks of the committed
tree; they run no implementation code.

Resources: repo — the repository that contains the pytest working directory.
Committed files are listed with `git ls-files` when the repository is a git
checkout, and by walking it (hidden directories skipped) otherwise. Package
source is every `.py` file under `src/`.

The prohibited-construct checks are heuristics over names and calls (Python
`ast`); the contract names the constructs but not their spelling. Each
failure lists file, line and the matched construct. The credential check
never prints the matched text, only file, line and pattern name.

Configuration: none beyond the working directory.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import warnings
from pathlib import Path

import pytest

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

SKIP_DIRS = {"node_modules", "site-packages", "__pycache__"}
MAX_SCAN_BYTES = 8 * 1024 * 1024
DISCOVERY_CALLS = {"entry_points", "iter_entry_points", "load_entry_point", "iter_modules", "walk_packages",
                   "__subclasses__"}
DYNAMIC_IMPORTS = {"import_module", "__import__"}
DISCOVERY_MODULES = {"pkg_resources", "pluggy", "stevedore"}
REFERENCE_HARNESS_PACKAGES = {"lm-eval", "lm-evaluation-harness", "lighteval", "opencompass", "evalscope",
                              "crfm-helm", "deepeval", "inspect-ai", "evals", "simple-evals"}
REFERENCE_HARNESS_MODULES = {"lm_eval", "lighteval", "opencompass", "evalscope", "helm", "inspect_ai", "deepeval",
                             "evals"}
ENGINE_NAMES = re.compile(r"(?i)registry|registries|factory|factories|plugin|matrix|sweep|grid_search|experiment")
CREDENTIAL_PATTERNS = {
    "hugging-face-token": re.compile(r"\bhf_[A-Za-z0-9]{30,}"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}"),
    "github-fine-grained-token": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}"),
    "aws-access-key-id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private-key-block": re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----"),
    "sk-style-api-key": re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{32,}"),
    "slack-token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "bearer-header": re.compile(r"(?i)authorization:\s*bearer\s+[A-Za-z0-9._~+/=-]{24,}"),
}
MODEL_SUFFIXES = (".gguf", ".safetensors", ".partial")
RUN_STORE_NAMES = {"items.jsonl", "attempts.log.jsonl", "server.log"}

# ---------------------------------------------------------------------------
# the committed tree
# ---------------------------------------------------------------------------


def _repo_root() -> Path | None:
    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        pyproject = candidate / "pyproject.toml"
        try:
            if pyproject.is_file() and "slm-eval" in pyproject.read_text(encoding="utf-8", errors="replace"):
                return candidate
        except OSError:
            continue
    return None


@pytest.fixture(scope="module")
def root() -> Path:
    found = _repo_root()
    if found is None:
        pytest.skip("needs the repository that contains the pytest working directory")
    return found


def committed_files(root: Path) -> list[Path]:
    if (root / ".git").exists() and shutil.which("git"):
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True).stdout
        return [root / name for name in out.decode("utf-8", "replace").split("\0") if name]
    found = []
    for path in sorted(root.rglob("*")):
        parts = path.relative_to(root).parts
        if path.is_file() and not any(p.startswith(".") or p in SKIP_DIRS for p in parts[:-1]):
            found.append(path)
    return found


def package_sources(root: Path) -> list[tuple[Path, ast.AST]]:
    src = root / "src"
    if not src.is_dir():
        pytest.skip("no src/ directory holding the package")
    trees = []
    for path in sorted(src.rglob("*.py")):
        if SKIP_DIRS & set(path.parts):
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            trees.append((path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))))
    return trees


def _name(node) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _name(node.func)
    return None


def _where(root: Path, path: Path, node) -> str:
    return f"{path.relative_to(root)}:{getattr(node, 'lineno', '?')}"


def _imported_modules(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node, alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node, node.module.split(".")[0]


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def declared_packages(root: Path) -> set[str]:
    names = set()
    lock = root / "poetry.lock"
    if lock.is_file():
        names |= {_normalise(n) for n in re.findall(r'(?m)^name = "([^"]+)"', lock.read_text(encoding="utf-8"))}
    pyproject = root / "pyproject.toml"
    if tomllib is not None and pyproject.is_file():
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        specs = list((data.get("project") or {}).get("dependencies") or [])
        for group in (data.get("dependency-groups") or {}).values():
            specs += [s for s in group if isinstance(s, str)]
        poetry = (data.get("tool") or {}).get("poetry") or {}
        specs += list((poetry.get("dependencies") or {}).keys())
        for group in (poetry.get("group") or {}).values():
            specs += list((group.get("dependencies") or {}).keys())
        for spec in specs:
            match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", spec)
            if match:
                names.add(_normalise(match.group(1)))
    return names


# ---------------------------------------------------------------------------
# tests: prohibited constructs
# ---------------------------------------------------------------------------


def test_vs_sm_01_no_plugin_or_runtime_discovery(root):
    """VS-SM-01 · repo · Prohibited: "dataset or runtime plug-in discovery"; allowed: an adapter "selected
    by that name alone"; brief hard invariant 4.

    Package source makes no discovery call (entry points, module iteration,
    `__subclasses__`), no dynamic import with a non-literal module name, and
    imports no plug-in framework (`pkg_resources`, `pluggy`, `stevedore`);
    `pyproject.toml` declares no entry-point or plug-in groups beyond the
    `slm-eval` script.
    """
    hits = []
    for path, tree in package_sources(root):
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _name(node.func)
                if name in DISCOVERY_CALLS:
                    hits.append(f"{_where(root, path, node)} calls {name}()")
                elif name in DYNAMIC_IMPORTS and not (node.args and isinstance(node.args[0], ast.Constant)
                                                      and isinstance(node.args[0].value, str)):
                    hits.append(f"{_where(root, path, node)} calls {name}() with a computed module name")
        for node, module in _imported_modules(tree):
            if module in DISCOVERY_MODULES:
                hits.append(f"{_where(root, path, node)} imports {module}")
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    for header in re.findall(r"(?m)^\[(project\.entry-points[^\]]*|tool\.poetry\.plugins[^\]]*)\]", pyproject):
        hits.append(f"pyproject.toml declares [{header}]")
    assert not hits, "discovery constructs found:\n" + "\n".join(hits)


def test_vs_sm_02_no_reference_harness_integration(root):
    """VS-SM-02 · repo · Prohibited: "reference-harness integration"; brief hard invariant 4.

    No declared or locked dependency, and no package import, is a known
    evaluation harness (lm-eval, lighteval, OpenCompass, EvalScope, HELM,
    DeepEval, Inspect, OpenAI evals / simple-evals).
    """
    hits = [f"dependency {name}" for name in sorted(declared_packages(root) & REFERENCE_HARNESS_PACKAGES)]
    for path, tree in package_sources(root):
        for node, module in _imported_modules(tree):
            if module in REFERENCE_HARNESS_MODULES:
                hits.append(f"{_where(root, path, node)} imports {module}")
    assert not hits, "reference-harness integration found:\n" + "\n".join(hits)


def test_vs_sm_03_no_registry_factory_hierarchy_or_engine(root):
    """VS-SM-03 · repo · Prohibited: "model registries or generic model-adapter hierarchies", "generalized
    factories", "an experiment or matrix engine"; allowed: "one concrete adapter per model family … This is
    not an adapter hierarchy"; brief hard invariant 4.

    In package source: no class, function or module-level name containing
    registry, factory, plugin, matrix, sweep, grid_search or experiment; no
    `register…` decorator; no abstract adapter base (an `*Adapter*` class
    using ABC, ABCMeta or `@abstractmethod`); and no `*Adapter*` class that
    subclasses another `*Adapter*` class.
    """
    hits = []
    for path, tree in package_sources(root):
        for node in ast.walk(tree):
            names = []
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                names.append(node.name)
                for decorator in node.decorator_list:
                    target = decorator.func if isinstance(decorator, ast.Call) else decorator
                    is_atexit = isinstance(target, ast.Attribute) and _name(target.value) == "atexit"
                    if (_name(decorator) or "").startswith("register") and not is_atexit:
                        hits.append(f"{_where(root, path, node)} {node.name} is decorated with {_name(decorator)}")
            if isinstance(node, ast.Module):
                for statement in node.body:
                    if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                        names += [(t.id, statement) for t in targets if isinstance(t, ast.Name)]
            for entry in names:
                name, where = entry if isinstance(entry, tuple) else (entry, node)
                if ENGINE_NAMES.search(name):
                    hits.append(f"{_where(root, path, where)} defines {name}")
            if isinstance(node, ast.ClassDef) and "adapter" in node.name.lower():
                bases = [_name(b) or "" for b in node.bases]
                metaclasses = [_name(k.value) or "" for k in node.keywords if k.arg == "metaclass"]
                abstract = any(_name(d) == "abstractmethod" for item in node.body
                               if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                               for d in item.decorator_list)
                if {"ABC", "ABCMeta"} & set(bases + metaclasses) or abstract:
                    hits.append(f"{_where(root, path, node)} {node.name} is an abstract adapter base")
                for base in bases:
                    if "adapter" in base.lower():
                        hits.append(f"{_where(root, path, node)} {node.name} subclasses {base}")
    assert not hits, "prohibited constructs found:\n" + "\n".join(sorted(set(hits)))


# ---------------------------------------------------------------------------
# tests: credentials and the data policy
# ---------------------------------------------------------------------------


def test_vs_sm_04_no_credential_in_committed_files(root):
    """VS-SM-04 · repo · §3 "never prints secrets"; §10 the API key is "never written to disk, logs,
    artifacts, or stdout"; §12.5 "No token or credential appears in it"; brief hard invariant 1.

    No committed text file matches a credential pattern (Hugging Face,
    GitHub, AWS, private-key block, `sk-` API key, Slack, Google API key, a
    bearer authorization header). Files over 8 MiB or holding NUL bytes are
    skipped. Hits are reported by file, line and pattern only.
    """
    hits = []
    for path in committed_files(root):
        try:
            if not path.is_file() or path.stat().st_size > MAX_SCAN_BYTES:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data:
            continue
        for number, line in enumerate(data.decode("utf-8", "replace").splitlines(), start=1):
            for label, pattern in CREDENTIAL_PATTERNS.items():
                if pattern.search(line):
                    hits.append(f"{path.relative_to(root)}:{number} matches {label}")
    assert not hits, "possible credentials in committed files:\n" + "\n".join(hits)


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def test_vs_sm_05_no_run_store_runner_state_or_model_files_committed(root):
    """VS-SM-05 · repo · §2 data policy: raw generations live only in the run store, "only identifiers,
    hashes, and aggregates are committed", model files live in the cache; §13.2 authorization "kept outside
    the repository"; §13.3 runner state lives under the run store.

    No committed file is a model or partial file (`.gguf`, `.safetensors`,
    `.partial`), a run-store or runner-state file (`items.jsonl`,
    `attempts.log.jsonl`, `server.log`, a runner `status.json`), or an
    authorization (`authorization_version`).
    """
    hits = []
    for path in committed_files(root):
        relative = path.relative_to(root)
        if path.name.endswith(MODEL_SUFFIXES):
            hits.append(f"{relative} is a model or partial file")
        elif path.name in RUN_STORE_NAMES:
            hits.append(f"{relative} is a run-store or runner-state file")
        elif path.suffix == ".json" and path.is_file() and path.stat().st_size <= MAX_SCAN_BYTES:
            doc = _json(path)
            if isinstance(doc, dict) and "authorization_version" in doc:
                hits.append(f"{relative} is an authorization")
            elif isinstance(doc, dict) and {"plan_sha256", "authorization_sha256", "state"} <= set(doc):
                hits.append(f"{relative} is runner state")
    assert not hits, "files outside the §2 repository policy:\n" + "\n".join(hits)
