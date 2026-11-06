"""Visible specification tests — verified-copy re-scoring of retained Qwen3 runs, legacy and current protocol forms alike (contract v0.8.1 §2, §3, §4, §6).

Contract-only: written from the approved interface and the maintainer's
readings, with no implementation code seen. This test never writes into an
evidence location, and never prints or writes prompt, problem or output text:
failures name runs, item × sample pairs, counts, adapter ids and file names
only, and the CLI's own stdout and stderr are kept in files that are named,
not shown.

Which runs apply. A retained run applies when it is `completed` and its
hash-bound protocol copy (protocol.yaml, whose SHA-256 must equal run.json
`protocol_sha256`) names the qwen3 family: `model.adapter.id` is `qwen3`, or
the protocol has no `model.adapter`, which §4 assigns to qwen3 (the legacy
form). run.json's own `adapter` record does not decide it: a legacy run may
record its transport there. A run whose run.json `adapter.id` names another
adapter family than its protocol does, or whose protocol copy cannot be
verified or read, fails the test rather than being left out.

Resources (stated in the docstring):
  retained  SLM_EVAL_TEST_RETAINED_RUNS: a directory holding retained
            Qwen3-8B MATH-500 run directories (§6.1). Only ever read.
  work      SLM_EVAL_TEST_WORK_ROOT (optional): where the fresh working
            stores are created; the pytest temporary directory otherwise. It
            must be outside the repository (§2: run stores hold raw
            generations) and its path must contain no whitespace. Working
            copies are left there for inspection, not deleted.
  yaml      PyYAML in the pytest process, to read the protocol copies.

Do not run this test with pytest's --showlocals (-l): local variables hold
record contents.

Configuration: SLM_EVAL_TEST_CMD overrides the CLI command (shell-split). No
SLM_EVAL_TEST_* variable, and no variable whose name contains TOKEN, SECRET,
PASSWORD or API_KEY, is passed to the implementation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import uuid
from pathlib import Path

import pytest

RESCORE_TIMEOUT = 3600
EXIT_OK = 0
ADAPTER_FAMILIES = {"qwen3", "phi4-reasoning", "gemma4"}

# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _command() -> list[str]:
    override = os.environ.get("SLM_EVAL_TEST_CMD", "").strip()
    if override:
        return shlex.split(override)
    found = shutil.which("slm-eval")
    if found is None:
        pytest.fail("`slm-eval` is not on PATH; run inside the project environment or set SLM_EVAL_TEST_CMD",
                    pytrace=False)
    return [found]


_LOCATIONS = ("SLM_EVAL_CACHE", "SLM_EVAL_RUNS", "SLM_EVAL_SCRATCH", "SLM_EVAL_FLOOR_GB", "SLM_EVAL_SCRATCH_FLOOR_GB")


def _passed_on(name: str) -> bool:
    upper = name.upper()
    return (name not in _LOCATIONS and not name.startswith("SLM_EVAL_TEST_")
            and not any(word in upper for word in ("TOKEN", "SECRET", "PASSWORD", "API_KEY")))


def not_run(reason: str):
    pytest.skip(f"NOT RUN: {reason}")


def fail(message: str):
    """Fail without a traceback or assertion introspection, which could show record contents."""
    raise pytest.fail.Exception(message, pytrace=False) from None


class withheld:
    """Turn any unexpected exception into a failure that shows only its type."""

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        if kind is not None and issubclass(kind, Exception):
            raise pytest.fail.Exception(f"{kind.__name__} raised; details withheld (records hold raw generations)",
                                        pytrace=False) from None
        return False


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dig(obj, *keys):
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def location(variable: str, what: str) -> Path:
    configured = os.environ.get(variable, "").strip()
    if not configured:
        not_run(f"needs {what}: set {variable}")
    path = Path(configured).expanduser()
    if not path.is_dir():
        fail(f"{variable} is not a directory")
    return path


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def run_dirs(store: Path) -> list[Path]:
    """Run directories of a §2 run store: children holding run.json, other than `_verification` and `_runner`."""
    return sorted(p for p in store.iterdir() if p.is_dir() and not p.name.startswith("_") and (p / "run.json").is_file())


def items_of(run_dir: Path) -> list:
    """items.jsonl records in order; a line that is not a JSON object is None."""
    records = []
    path = run_dir / "items.jsonl"
    if not path.is_file():
        return records
    with open(path, "rb") as fh:
        for line in fh:
            if line.strip():
                try:
                    record = json.loads(line)
                except ValueError:
                    record = None
                records.append(record if isinstance(record, dict) else None)
    return records


def record_hash_problems(records: list) -> int:
    """§6.1: every record's raw_output_sha256 and reference_sha256 hold."""
    bad = 0
    for record in records:
        if record is None:
            bad += 1
            continue
        bad += sha256_bytes(str(record.get("raw_output", "")).encode("utf-8")) != record.get("raw_output_sha256")
        bad += sha256_bytes(str(record.get("reference_answer", "")).encode("utf-8")) != record.get("reference_sha256")
    return bad


def copy_problems(run_dir: Path, doc: dict) -> list[str]:
    """§6.1: the protocol and manifest copies have the hashes run.json records."""
    problems = []
    for name, key in (("protocol.yaml", "protocol_sha256"), ("manifest.json", "manifest_sha256")):
        if not (run_dir / name).is_file() or sha256_file(run_dir / name) != doc.get(key):
            problems.append(f"{name} copy does not match run.json {key}")
    return problems


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


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# which retained runs apply: the family named by the hash-bound protocol copy
# ---------------------------------------------------------------------------


def _yaml():
    try:
        import yaml  # type: ignore
    except ImportError:
        not_run("PyYAML is needed in the pytest process to read the protocol copies")
    return yaml


def protocol_family(run_dir: Path, doc: dict) -> tuple[str | None, bool, str | None]:
    """(family, legacy form, problem) from the run's protocol copy, verified against run.json first.

    §4: a protocol without `model.adapter` takes adapter qwen3 (legacy form)."""
    path = run_dir / "protocol.yaml"
    if not path.is_file() or sha256_file(path) != doc.get("protocol_sha256"):
        return None, False, ("its protocol.yaml copy is missing or does not match run.json protocol_sha256, so its "
                             "adapter family cannot be established")
    try:
        data = _yaml().safe_load(path.read_bytes().decode("utf-8"))
    except Exception:
        return None, False, "its protocol.yaml copy cannot be parsed, so its adapter family cannot be established"
    if not isinstance(data, dict):
        return None, False, "its protocol.yaml copy is not a mapping"
    model = data.get("model")
    adapter = model.get("adapter") if isinstance(model, dict) else None
    if adapter is None:
        return "qwen3", True, None
    if isinstance(adapter, dict) and isinstance(adapter.get("id"), str):
        return adapter["id"], False, None
    return None, False, "its protocol's model.adapter names no id"


def classify(store: Path) -> tuple[list[Path], set[str], dict[str, list[str]], list[str]]:
    """(applicable runs, names in the legacy form, excluded names by reason, problems)."""
    applicable, legacy, problems = [], set(), []
    excluded: dict[str, list[str]] = {"not completed": [], "another adapter family": []}
    for run_dir in run_dirs(store):
        doc = read_json(run_dir / "run.json")
        if not isinstance(doc, dict):
            problems.append(f"{run_dir.name}: run.json is not a JSON object, so it cannot be classified")
            continue
        if doc.get("status") != "completed":
            excluded["not completed"].append(run_dir.name)
            continue
        family, legacy_form, problem = protocol_family(run_dir, doc)
        if problem:
            problems.append(f"{run_dir.name}: {problem}")
            continue
        recorded = dig(doc, "adapter", "id")
        if recorded in ADAPTER_FAMILIES and recorded != family:
            problems.append(f"{run_dir.name}: run.json adapter {recorded!r} contradicts its protocol's adapter "
                            f"family {family!r}")
            continue
        if family == "qwen3":
            applicable.append(run_dir)
            if legacy_form:
                legacy.add(run_dir.name)
        else:
            excluded["another adapter family"].append(run_dir.name)
    return applicable, legacy, excluded, problems


# ---------------------------------------------------------------------------
# verified copies, then rescore on the copies only
# ---------------------------------------------------------------------------


def inventory(root: Path) -> dict[str, tuple]:
    """Every entry under a run directory: ("dir",) or ("file", size, sha256, mtime_ns)."""
    entries = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        for name in sorted(dirnames):
            path = base / name
            entries[str(path.relative_to(root))] = ("symlinked-dir",) if path.is_symlink() else ("dir",)
        for name in sorted(filenames):
            path = base / name
            stat = path.stat()
            entries[str(path.relative_to(root))] = ("file", stat.st_size, sha256_file(path), stat.st_mtime_ns)
    return entries


def _content(entries: dict[str, tuple]) -> dict[str, tuple]:
    """The same inventory without modification times: what a byte-verified copy must reproduce."""
    return {k: v[:3] for k, v in entries.items()}


def inventory_difference(expected: dict[str, tuple], got: dict[str, tuple]) -> list[str]:
    missing = sorted(set(expected) - set(got))
    extra = sorted(set(got) - set(expected))
    changed = sorted(k for k in set(expected) & set(got) if expected[k] != got[k])
    return ([f"missing {k}" for k in missing[:5]] + [f"unexpected {k}" for k in extra[:5]]
            + [f"differs {k}" for k in changed[:5]])


def copy_verified(source: Path, target: Path, expected: dict[str, tuple]) -> list[str]:
    """Copy a run directory completely into a new location, then verify every byte against the inventory."""
    if target.exists():
        return [f"{target.name}: copy target already exists"]
    if any(v[0] == "symlinked-dir" for v in expected.values()):
        return [f"{source.name}: holds a symbolic link to a directory, which this test does not copy"]
    target.mkdir(parents=True)
    for rel, entry in sorted(expected.items()):
        if entry[0] == "dir":
            (target / rel).mkdir(parents=True, exist_ok=True)
        else:
            (target / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / rel, target / rel)
    difference = inventory_difference(_content(expected), _content(inventory(target)))
    return [f"{target.name}: copy is not byte-identical: " + "; ".join(difference)] if difference else []


def original_problems(run_dir: Path) -> list[str]:
    """Verify an original before anything is copied: §6.1 copies and record hashes."""
    doc = read_json(run_dir / "run.json")
    if not isinstance(doc, dict):
        return ["run.json is not a JSON object"]
    problems = copy_problems(run_dir, doc)
    records = items_of(run_dir)
    if not records:
        problems.append("items.jsonl is missing or empty")
    bad = record_hash_problems(records)
    if bad:
        problems.append(f"{bad} items.jsonl lines are not JSON or fail their record hashes")
    return problems


def work_root(tmp_path: Path, store: Path) -> Path:
    configured = os.environ.get("SLM_EVAL_TEST_WORK_ROOT", "").strip()
    base = Path(configured).expanduser() if configured else tmp_path
    if not base.is_dir():
        fail("SLM_EVAL_TEST_WORK_ROOT is not a directory")
    base = base.resolve()
    if re.search(r"\s", str(base)):
        fail("setup: the working-store path contains whitespace; set SLM_EVAL_TEST_WORK_ROOT to a space-free "
             "directory outside the repository")
    root = base / f"vs-rc-13-{uuid.uuid4().hex[:12]}"
    root.mkdir()
    repo = _repo_root()
    if repo is not None and _inside(root, repo):
        fail("setup: the working store would hold raw generations inside the repository (§2); set "
             "SLM_EVAL_TEST_WORK_ROOT outside it")
    if _inside(root, store) or _inside(store, root):
        fail("setup: the working store and SLM_EVAL_TEST_RETAINED_RUNS overlap")
    return root


class Store:
    """A fresh, empty run store with its own cache, scratch and working directory."""

    def __init__(self, root: Path):
        self.root = root
        self.runs, self.cache, self.scratch, self.cwd, self.logs, self.displaced = (
            root / n for n in ("runs", "cache", "scratch", "cwd", "logs", "displaced"))
        for directory in (self.runs, self.cache, self.scratch, self.cwd, self.logs, self.displaced):
            directory.mkdir(parents=True)
        self.vars = {k: v for k, v in os.environ.items() if _passed_on(k)}
        self.vars.update(SLM_EVAL_CACHE=str(self.cache), SLM_EVAL_RUNS=str(self.runs),
                         SLM_EVAL_SCRATCH=str(self.scratch))

    def displace_prior_rescore(self, run_id: str) -> None:
        """After the copy is verified: move a prior rescore.json aside (it stays in the baseline) so that the
        rescore.json read afterwards can only have been written by this rescore."""
        prior = self.runs / run_id / "rescore.json"
        if prior.is_file():
            (self.displaced / run_id).mkdir()
            shutil.move(str(prior), str(self.displaced / run_id / "rescore.json"))

    def rescore(self, run_id: str) -> tuple[int, Path, Path]:
        """`rescore --run` as an owned process; its output goes to files that are named, never printed."""
        name = uuid.uuid4().hex[:8]
        out_path, err_path = self.logs / f"{name}.out", self.logs / f"{name}.err"
        with open(out_path, "wb") as out, open(err_path, "wb") as err:
            popen = subprocess.Popen(_command() + ["rescore", "--run", run_id], cwd=self.cwd, env=self.vars,
                                     stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True)
            try:
                code = popen.wait(timeout=RESCORE_TIMEOUT)
            except subprocess.TimeoutExpired:
                # The launch handle is not reaped yet, so its process group cannot have been reused.
                os.killpg(popen.pid, signal.SIGKILL)
                popen.wait()
                fail(f"rescore of {run_id} exceeded {RESCORE_TIMEOUT} s; stdout {out_path}, stderr {err_path}")
        return code, out_path, err_path


def stored_verdicts(records: list) -> dict[tuple, tuple]:
    return {(r.get("item_id"), r.get("sample_index")): (dig(r, "verdicts", "primary"), dig(r, "verdicts", "secondary"))
            for r in records if r is not None}


def rescore_consistency(doc, run_id: str, stored: dict[tuple, tuple]) -> tuple[list[str], dict[tuple, dict]]:
    """§6.4: version, run id, one record per stored pair, stored verdicts copied, `match` and `all_match` honest."""
    if not isinstance(doc, dict):
        return ["no readable rescore.json"], {}
    problems = []
    if doc.get("rescore_version") != "1" or doc.get("run_id") != run_id:
        problems.append("rescore_version or run_id is wrong")
    by_pair, duplicates = {}, 0
    for record in doc.get("records") or []:
        if not isinstance(record, dict):
            problems.append("a record is not an object")
            continue
        pair = (record.get("item_id"), record.get("sample_index"))
        duplicates += pair in by_pair
        by_pair[pair] = record
    if duplicates:
        problems.append(f"{duplicates} pairs have more than one record")
    missing, extra = set(stored) - set(by_pair), set(by_pair) - set(stored)
    if missing or extra:
        problems.append(f"{len(missing)} stored pairs have no record; {len(extra)} records name no stored pair")
    dishonest = []
    for pair, record in by_pair.items():
        recomputed = (dig(record, "recomputed", "primary"), dig(record, "recomputed", "secondary"))
        copied = (dig(record, "stored", "primary"), dig(record, "stored", "secondary"))
        if (pair in stored and copied != stored[pair]) or not all(isinstance(v, bool) for v in recomputed) \
                or record.get("match") is not (copied == recomputed):
            dishonest.append(pair)
    if dishonest:
        problems.append(f"{len(dishonest)} records misstate stored, recomputed or match, first "
                        + ", ".join(f"{i}#{s}" for i, s in sorted(dishonest, key=str)[:10]))
    matches = [r.get("match") is True for r in by_pair.values()]
    if doc.get("all_match") is not all(matches):
        problems.append(f"all_match {doc.get('all_match')!r} does not summarize the records")
    return problems, by_pair


def flip_first_line(items_path: Path) -> tuple:
    """Invert both stored verdicts of the first record, leaving every other line byte-identical."""
    lines = items_path.read_bytes().splitlines(keepends=True)
    record = json.loads(lines[0])
    flipped = (not dig(record, "verdicts", "primary"), not dig(record, "verdicts", "secondary"))
    record["verdicts"] = {"primary": flipped[0], "secondary": flipped[1]}
    lines[0] = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    items_path.write_bytes(b"".join(lines))
    return (record.get("item_id"), record.get("sample_index")), flipped


def test_vs_rc_13_retained_qwen3_runs_rescore_on_verified_copies(tmp_path):
    """VS-RC-13 · retained (+ work) · retained Qwen3-8B runs re-score to their stored verdicts; §3 rescore; §4
    compatibility ("a protocol file written under v0.7 stays valid … adapter qwen3 … Re-scoring … unchanged");
    §6.1 copies and record hashes; §6.2 rescore leaves the original untouched; §6.4; §2 run stores hold raw
    generations.

    Proposed replacement for VS-RC-11's intent, which chose runs by run.json `adapter.id` and so left out
    legacy runs that record their transport there. Every completed retained run is classified by its
    hash-bound protocol copy (module docstring): qwen3 in either the legacy or the current form applies;
    another family is left out; an unverifiable or contradictory run fails the test. Then, for every
    applicable run:
      1. the original is verified first (protocol and manifest copies, every record hash) and inventoried;
      2. a complete baseline copy, any prior rescore.json included, is made in a fresh space-free directory
         outside the repository and verified byte for byte;
      3. a working copy is made from the baseline in a fresh run store and verified byte for byte; only then
         is a prior rescore.json moved aside, so the rescore.json read afterwards is new;
      4. `rescore` runs on the working copy only → exit 0; rescore.json has version "1", the run id, one record
         per stored pair copying its stored verdicts, honest `match` values, `all_match` summarizing them, and
         `all_match: true`; no working file that existed before it ran changes.
    A control copy of the smallest run, with one record's stored verdicts inverted, must report that record
    `match: false` with the same recomputed verdicts and `all_match: false`. Originals and the baseline keep
    their exact bytes and modification times throughout. Nothing from a record is printed. With no
    applicable run the test fails.
    """
    store = location("SLM_EVAL_TEST_RETAINED_RUNS", "the retained Qwen3-8B run directories")
    _yaml()
    with withheld():
        runs, legacy, excluded, failures = classify(store)
        if failures:
            fail("retained runs could not all be classified, so nothing was copied or rescored:\n"
                 + "\n".join(failures))
        if not runs:
            fail("no completed qwen3-family run directory found in SLM_EVAL_TEST_RETAINED_RUNS (excluded: "
                 + "; ".join(f"{len(v)} {k}" for k, v in excluded.items()) + ")")
        originals = {run.name: inventory(run) for run in runs}
        failures = [f"{run.name}: original fails verification: {p}" for run in runs for p in original_problems(run)]
        if failures:
            fail("originals were not verified, so nothing was copied or rescored:\n" + "\n".join(failures))

        root = work_root(tmp_path, store)
        baseline = root / "baseline"
        for run in runs:
            failures += copy_verified(run, baseline / run.name, originals[run.name])
        if failures:
            fail("baseline copies are not byte-identical:\n" + "\n".join(failures))
        baseline_inventory = {run.name: inventory(baseline / run.name) for run in runs}

        work = Store(root / "work")
        results: dict[str, dict] = {}
        for run in runs:
            name = run.name
            problems = copy_verified(baseline / name, work.runs / name, originals[name])
            if problems:
                failures += problems
                continue
            work.displace_prior_rescore(name)
            before = _content(inventory(work.runs / name))
            stored = stored_verdicts(items_of(work.runs / name))
            code, out_path, err_path = work.rescore(name)
            doc = read_json(work.runs / name / "rescore.json")
            problems, by_pair = rescore_consistency(doc, name, stored)
            if code != EXIT_OK:
                problems.insert(0, f"rescore exited {code} (stdout {out_path}, stderr {err_path})")
            after = _content(inventory(work.runs / name))
            if any(after.get(k) != v for k, v in before.items()):
                problems.append("rescore changed or removed working files that existed before it ran (§6.2)")
            if not problems and dig(doc, "all_match") is not True:
                mismatched = sorted((p for p, r in by_pair.items() if r.get("match") is not True), key=str)
                problems.append(f"all_match is false: {len(mismatched)} pairs differ, first "
                                + ", ".join(f"{i}#{s}" for i, s in mismatched[:10]))
            failures += [f"{name}: {p}" for p in problems]
            results[name] = {"ok": not problems, "by_pair": by_pair, "lines": len(stored)}

        controllable = [name for name, r in results.items() if r["ok"] and r["lines"]]
        if controllable:
            name = min(controllable, key=lambda n: (results[n]["lines"], n))
            control = Store(root / "control")
            problems = copy_verified(baseline / name, control.runs / name, originals[name])
            if not problems:
                control.displace_prior_rescore(name)
                pair, flipped = flip_first_line(control.runs / name / "items.jsonl")
                stored = stored_verdicts(items_of(control.runs / name))
                code, out_path, err_path = control.rescore(name)
                doc = read_json(control.runs / name / "rescore.json")
                problems, by_pair = rescore_consistency(doc, name, stored)
                main = results[name]["by_pair"].get(pair) or {}
                record = by_pair.get(pair) or {}
                if record.get("recomputed") != main.get("recomputed"):
                    problems.append("the control's recomputed verdicts differ from the main rescore's")
                if record.get("match") is not False or dig(doc, "all_match") is not False:
                    problems.append(f"an inverted stored verdict was not reported as a mismatch "
                                    f"(exit {code}; stdout {out_path}, stderr {err_path})")
            failures += [f"control {name}: {p}" for p in problems]
        elif not failures:
            failures.append("no run was available for the inverted-verdict control")

        for run in runs:
            difference = inventory_difference(originals[run.name], inventory(run))
            if difference:
                failures.append(f"{run.name}: ORIGINAL CHANGED: " + "; ".join(difference))
            difference = inventory_difference(baseline_inventory[run.name], inventory(baseline / run.name))
            if difference:
                failures.append(f"{run.name}: baseline changed: " + "; ".join(difference))
    if failures:
        fail(f"retained runs (working store {root}):\n" + "\n".join(failures))
    print(f"VS-RC-13: rescored {len(runs)} runs on verified copies ({len(legacy)} in the legacy form without "
          f"model.adapter, {len(runs) - len(legacy)} naming adapter qwen3); left out: "
          + "; ".join(f"{len(v)} {k}" for k, v in excluded.items()) + f"; working store {root}")
