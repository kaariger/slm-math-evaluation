"""Holdout — runtime security, runs, resume and run evidence (contract v0.3 §3, §5.1, §6, §10).

REQUIRES the real model artifact and the pinned llama.cpp runtime
(SLM_EVAL_HOLDOUT_RUNTIME=1), a seed cache holding the pinned dataset and
the model under models/ (SLM_EVAL_HOLDOUT_SEED_CACHE; §2 places model files
in the dataset cache), the committed protocol and manifest, and the `ps` and
`lsof` tools. Every test uses its own temporary SLM_EVAL_CACHE,
SLM_EVAL_RUNS and working directory; runs use the smoke tier.

Commands that start the runtime are launched in their own session and
tracked with their whole process tree, so a failing test never leaks a
server. SIGINT is sent to the CLI process only, never to its process group,
so cleanup on interrupt is the harness's own doing.
"""

from __future__ import annotations

import json
import re
import time

import pytest

import _ho_support as hs

pytestmark = [pytest.mark.requires_runtime, pytest.mark.requires_data]

STARTUP_TIMEOUT = 1800
GRACE = 15.0
KEY_ARG = re.compile(r"--api-key(?:=|\s+)(\S+)")
KEY_ENV = re.compile(r"\bLLAMA_API_KEY=(\S+)")


def _inputs():
    return hs.protocol_path(), hs.manifest_path()


def _prepared(sandbox):
    protocol, manifest = _inputs()
    hs.populate_cache(sandbox)
    hs.verify_before_run(sandbox, protocol)
    return protocol, manifest


def _run_args(protocol, manifest, k=1, seed_base=None):
    args = ["run", "--protocol", protocol, "--manifest", manifest, "--tier", "smoke", "--k", k]
    if seed_base is not None:
        args += ["--seed-base", seed_base]
    return args


def _smoke_ids(manifest):
    return list(hs.read_json(manifest)["tiers"]["smoke"])


def _run_id_from(proc, sandbox):
    """§3: `run` prints the run ID; any other stdout line is a written path, logs go to stderr."""
    lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    ids = [line for line in lines if "/" not in line and (sandbox.runs / line).is_dir()]
    assert len(ids) == 1, "run must print its run ID on stdout exactly once\n" + hs.describe(proc)
    return ids[0]


def _start_server(sandbox, protocol, manifest):
    sp = sandbox.spawn(*_run_args(protocol, manifest))
    sockets = sp.wait_for_listener(STARTUP_TIMEOUT)
    if not sockets:
        sp.close()
        pytest.fail("no listening runtime observed for the run\n" + hs.describe(sp.completed()), pytrace=False)
    return sp, sockets


def _assert_cleaned_up(sp, ports):
    hs.wait_until(lambda: not sp.survivors() and not any(hs.port_listening(p) for p in ports), GRACE)
    survivors = {pid: sp.seen[pid] for pid in sp.survivors()}
    still = [p for p in ports if hs.port_listening(p)]
    assert not survivors, f"runtime processes survived the CLI: {survivors}"
    assert not still, f"ports still listening after the CLI exited: {still}"


def _probe(sp, ports, budget=180.0):
    pending = {
        (port, method, path, variant)
        for port in ports
        for method, paths in (("GET", hs.PROBE_GET), ("POST", hs.PROBE_POST))
        for path in paths
        for variant in hs.PROBE_HEADERS
    }
    results = {}
    deadline = time.time() + budget
    while pending and time.time() < deadline and sp.proc.poll() is None:
        for combo in sorted(pending):
            port, method, path, variant = combo
            headers = dict(hs.PROBE_HEADERS[variant])
            body = None
            if method == "POST":
                headers["Content-Type"] = "application/json"
                body = hs.PROBE_BODY
            status = hs.http_status(f"http://127.0.0.1:{port}{path}", method, headers, body)
            if status is None or status == 503:
                continue
            results[combo] = status
            pending.discard(combo)
        if pending:
            time.sleep(1.0)
    return results, pending


def test_ho_rt_01_verify_artifact_passes(sandbox):
    """HO-RT-01 · §3 verify-artifact: REQUIRES the real model artifact. With
    the committed protocol it exits 0 and writes
    $SLM_EVAL_RUNS/_verification/<protocol_sha256>/artifact-verification.json,
    which records the protocol's model SHA-256."""
    protocol, _ = _inputs()
    hs.populate_cache(sandbox)
    proc = sandbox.cli("verify-artifact", "--protocol", protocol, timeout=3600)
    assert proc.returncode == hs.EXIT_OK, hs.describe(proc)
    record = sandbox.verification_record(hs.sha256_file(protocol), "artifact-verification.json")
    assert record.is_file(), f"{record} not written\n" + hs.describe(proc)
    _, _, data = hs.committed_protocol()
    model_sha = hs.dig(data, "model", "artifact", "sha256")
    assert model_sha in hs.text_of(hs.read_json(record)), "artifact-verification.json does not record the model sha256"


def test_ho_rt_02_verify_artifact_hash_mismatch_exits_2(sandbox):
    """HO-RT-02 · §3 verify-artifact "exit 2 on mismatch": REQUIRES the real
    model artifact. A copy of the committed protocol whose
    model.artifact.sha256 is changed (nothing else) exits 2."""
    protocol, _ = _inputs()
    hs.populate_cache(sandbox)
    _, raw, data = hs.committed_protocol()
    model_sha = hs.dig(data, "model", "artifact", "sha256")
    text = raw.decode("utf-8")
    assert text.count(model_sha) == 1, "model sha256 does not occur exactly once in the protocol text"
    tampered = sandbox.work / protocol.name
    tampered.write_text(text.replace(model_sha, "0" * 64), encoding="utf-8")
    proc = sandbox.cli("verify-artifact", "--protocol", tampered, timeout=3600)
    assert proc.returncode == hs.EXIT_INTEGRITY, hs.describe(proc)


def test_ho_rt_03_verify_runtime_passes_and_records_checks(sandbox):
    """HO-RT-03 · §3 verify-runtime + §10: REQUIRES the real runtime. It exits
    0 and writes $SLM_EVAL_RUNS/_verification/<protocol_sha256>/
    runtime-verification.json covering each listed check: key enforcement,
    loopback-only bind, ephemeral port, cleanup, raw-capture fidelity and
    parameter equivalence."""
    protocol, _ = _inputs()
    hs.populate_cache(sandbox)
    proc = sandbox.finish("verify-runtime", "--protocol", protocol, timeout=3600)
    assert proc.returncode == hs.EXIT_OK, hs.describe(proc)
    record = sandbox.verification_record(hs.sha256_file(protocol), "runtime-verification.json")
    assert record.is_file(), f"{record} not written\n" + hs.describe(proc)
    text = hs.text_of(hs.read_json(record)).lower()
    groups = {
        "key enforcement": ("key", "auth"),
        "loopback-only bind": ("loopback", "127.0.0.1", "bind"),
        "ephemeral port": ("ephemeral", "port"),
        "cleanup": ("cleanup", "clean_up", "terminat", "shutdown"),
        "raw-capture fidelity": ("raw", "fidelity", "capture"),
        "parameter equivalence": ("param", "equivalen"),
    }
    absent = [name for name, needles in groups.items() if not any(n in text for n in needles)]
    assert not absent, f"runtime-verification.json does not cover: {absent}"


def test_ho_rt_04_api_key_required_on_every_endpoint(sandbox):
    """HO-RT-04 · §10 "required on every endpoint": REQUIRES the real runtime.
    While a run's server is up, requests with no key, a wrong bearer key or a
    wrong X-Api-Key to every known llama.cpp endpoint (including /, /health
    and /models) are answered 401/403 (404 accepted for absent routes);
    nothing is served without the key."""
    protocol, manifest = _prepared(sandbox)
    sp, sockets = _start_server(sandbox, protocol, manifest)
    with sp:
        ports = sorted({port for _, _, port in sockets})
        results, pending = _probe(sp, ports)
        hs.interrupt_and_wait(sp)
    violations = {f"{m} :{p}{path} [{v}]": s for (p, m, path, v), s in results.items() if s not in (401, 403, 404)}
    assert not violations, f"endpoints answered without a valid key: {violations}"
    assert any(s in (401, 403) for s in results.values()), (
        f"no endpoint answered 401/403; {len(pending)} probes never got a definitive answer"
    )


def test_ho_rt_05_loopback_only_on_ephemeral_port(sandbox):
    """HO-RT-05 · §10 "bound to 127.0.0.1 only, on an ephemeral port":
    REQUIRES the real runtime. Every listening TCP socket of the run's process
    tree is on 127.0.0.1 and its port lies in the OS ephemeral range."""
    protocol, manifest = _prepared(sandbox)
    sp, _ = _start_server(sandbox, protocol, manifest)
    with sp:
        deadline = time.time() + 10
        while time.time() < deadline and sp.proc.poll() is None:
            sp.observe_listeners()
            time.sleep(0.5)
        hs.interrupt_and_wait(sp)
    low, high = hs.ephemeral_port_range()
    not_loopback = {f"{pid}:{port}": host for (pid, port), host in sp.listeners.items() if host != "127.0.0.1"}
    not_ephemeral = [port for (_, port) in sp.listeners if not low <= port <= high]
    assert not not_loopback, f"sockets not bound to 127.0.0.1 only: {not_loopback}"
    assert not not_ephemeral, f"ports outside the ephemeral range [{low}, {high}]: {not_ephemeral}"


def test_ho_rt_06_api_key_never_written_or_printed(sandbox):
    """HO-RT-06 · §10 "never written to disk, logs, artifacts, or stdout":
    REQUIRES the real runtime. The server receives its key in memory (no
    --api-key-file); the key observed in the server's arguments or
    environment appears in no file of the cache, run store or working
    directory, in no repository file written during the test, and in
    neither stdout nor stderr. Skips if the key value is not observable."""
    protocol, manifest = _prepared(sandbox)
    since = time.time()
    sp, _ = _start_server(sandbox, protocol, manifest)
    with sp:
        hs.wait_for_items(sandbox, sp, 1, 900)
        keys = set()
        for pid in sp.observe():
            keys.update(KEY_ARG.findall(sp.seen.get(pid, "")))
            keys.update(KEY_ENV.findall(hs.environment_of(pid)))
        commands = dict(sp.seen)
        hs.interrupt_and_wait(sp)
    key_files = [pid for pid, cmd in commands.items() if "--api-key-file" in cmd]
    assert not key_files, "the server was given its key through a file (--api-key-file)"
    if not keys:
        pytest.skip("the API key value is not observable from process arguments or environment")
    repo = hs._repo_root_or_none()
    for key in keys:
        needle = key.encode("utf-8")
        hits = hs.files_containing(needle, sandbox.root)
        for path in hs.iter_files(repo) if repo is not None else []:
            try:
                st = path.stat()
                if st.st_mtime >= since - 1 and st.st_size <= hs.LARGE_FILE and needle in path.read_bytes():
                    hits.append(str(path))
            except OSError:
                continue
        assert not hits, f"the API key was written to {len(hits)} file(s): {sorted(hits)[:10]}"


def test_ho_rt_07_server_cleaned_up_on_normal_exit(sandbox):
    """HO-RT-07 · §10 "terminated on normal exit": REQUIRES the real runtime.
    After verify-runtime exits 0, no process it started survives and no port
    it listened on is still listening. Skips if no server was observed."""
    protocol, _ = _inputs()
    hs.populate_cache(sandbox)
    with sandbox.spawn("verify-runtime", "--protocol", protocol) as sp:
        deadline = time.time() + 3600
        while sp.proc.poll() is None and time.time() < deadline:
            sp.observe_listeners()
            time.sleep(0.25)
        code = sp.wait(1.0)
        assert code == hs.EXIT_OK, hs.describe(sp.completed())
        if not sp.listeners:
            pytest.skip("no listening server observed during verify-runtime")
        _assert_cleaned_up(sp, sorted({port for _, port in sp.listeners}))


def test_ho_rt_08_server_cleaned_up_on_interrupt(sandbox):
    """HO-RT-08 · §10 "terminated ... on interrupt": REQUIRES the real
    runtime. SIGINT to the `slm-eval run` process alone makes it exit and
    leaves no surviving runtime process and no listening port."""
    protocol, manifest = _prepared(sandbox)
    sp, sockets = _start_server(sandbox, protocol, manifest)
    with sp:
        hs.interrupt_and_wait(sp)
        _assert_cleaned_up(sp, sorted({port for _, _, port in sockets}))


def test_ho_rt_09_run_evidence_is_complete(sandbox):
    """HO-RT-09 · §3 run, §6.1 run evidence, §8/§9, §10 raw output: REQUIRES
    the real model and runtime. A smoke run with --k 2 --seed-base 7100
    exits 0 and prints its run ID; the run directory holds byte copies of the
    protocol and manifest whose SHA-256 values are protocol_sha256 and
    manifest_sha256; run.json records every §6.1 field with identities equal
    to the protocol's (version, model hash, runtime build, extractor, both
    scorers with pins), k, seeds [7100, 7101], one completed attempt and
    status completed; both verification files are copied in; items.jsonl
    has one complete line per
    smoke item x sample, with consistent hashes, seed = seed_base +
    sample_index, the upstream reference answer, raw output containing the
    think block whenever thinking is reported, and None answers scored
    incorrect."""
    protocol, manifest = _prepared(sandbox)
    _, _, pdata = hs.committed_protocol()
    proc = sandbox.finish(*_run_args(protocol, manifest, k=2, seed_base=7100), timeout=hs.run_timeout())
    assert proc.returncode == hs.EXIT_OK, hs.describe(proc)
    run_id = _run_id_from(proc, sandbox)
    run_dir = sandbox.runs / run_id
    run = hs.read_json(run_dir / "run.json")
    problems = []
    for key in ("run_id", "protocol_version", "protocol_sha256", "manifest_sha256", "tier", "k", "seeds",
                "model_artifact_sha256", "runtime_build", "adapter", "extractor", "scorers", "executor",
                "host", "attempts", "status"):
        if key not in run:
            problems.append(f"run.json lacks {key}")
    expect = {
        "run_id": run_id,
        "protocol_version": pdata.get("protocol_version"),
        "manifest_sha256": hs.sha256_file(manifest),
        "tier": "smoke",
        "k": 2,
        "seeds": [7100, 7101],
        "model_artifact_sha256": hs.dig(pdata, "model", "artifact", "sha256"),
        "runtime_build": hs.dig(pdata, "runtime", "build"),
        "status": "completed",
    }
    problems += [f"run.json {k} = {run.get(k)!r}, expected {v!r}" for k, v in expect.items() if run.get(k) != v]
    if run.get("protocol_sha256") != hs.sha256_file(protocol):
        problems.append("protocol_sha256 is not the SHA-256 of the protocol file")
    for name, source, key in (("protocol.yaml", protocol, "protocol_sha256"),
                              ("manifest.json", manifest, "manifest_sha256")):
        copy_path = run_dir / name
        if not copy_path.is_file() or copy_path.read_bytes() != source.read_bytes():
            problems.append(f"{name} is not a byte copy of {source.name}")
        elif hs.sha256_file(copy_path) != run.get(key):
            problems.append(f"{name} hash differs from run.json {key}")
    adapter = run.get("adapter") or {}
    if not (isinstance(adapter.get("id"), str) and adapter.get("id") and isinstance(adapter.get("version"), str)):
        problems.append(f"adapter {adapter!r} lacks id/version")
    extraction = pdata.get("extraction") or {}
    if run.get("extractor") != {"id": extraction.get("id"), "version": extraction.get("version")}:
        problems.append(f"extractor {run.get('extractor')!r} differs from the protocol's extraction")
    recorded = {(s.get("id"), s.get("pin")) for s in run.get("scorers") or [] if isinstance(s, dict)}
    for role in ("primary", "secondary"):
        entry = hs.dig(pdata, "scorers", role) or {}
        if (entry.get("id"), entry.get("pin")) not in recorded:
            problems.append(f"scorers lack the protocol's {role} scorer {entry!r}")
    if not isinstance(run.get("executor"), str):
        problems.append("executor is not a string")
    host = run.get("host") or {}
    if not (isinstance(host.get("os"), str) and isinstance(host.get("arch"), str) and hs.is_number(host.get("memory_gb"))):
        problems.append(f"host {host!r} lacks os/arch/memory_gb")
    attempts = run.get("attempts") or []
    if len(attempts) != 1 or attempts[0].get("status") != "completed" or attempts[0].get("resumed_from") is not None \
            or not all(k in attempts[0] for k in ("n", "started", "ended")):
        problems.append(f"attempts {attempts!r} is not one completed fresh attempt")
    for name in ("runtime-verification.json", "artifact-verification.json"):
        try:
            hs.read_json(run_dir / name)
        except (OSError, ValueError):
            problems.append(f"{name} is not copied into the run directory as JSON")
    assert not problems, "; ".join(problems)

    smoke = _smoke_ids(manifest)
    records = hs.read_jsonl(run_dir / "items.jsonl")
    pairs = [(r.get("item_id"), r.get("sample_index")) for r in records]
    assert len(records) == 2 * len(smoke), f"{len(records)} lines for {len(smoke)} smoke items x 2"
    assert len(set(pairs)) == len(pairs), "duplicate item x sample lines"
    assert {r.get("item_id") for r in records} == set(smoke), "items.jsonl item set differs from the smoke tier"
    upstream = hs.records_by_id(hs.cached_records(sandbox.cache))
    max_tokens = hs.dig(pdata, "sampling", "max_output_tokens", "value")
    fields = ("item_id", "sample_index", "seed", "prompt_sha256", "raw_output", "raw_output_sha256",
              "thinking_present", "truncated", "finish_reason", "tokens_generated", "reference_answer",
              "reference_sha256", "extraction", "verdicts", "timing_ms")
    bad = []
    for r in records:
        where = f"{r.get('item_id')}#{r.get('sample_index')}"
        missing = [f for f in fields if f not in r]
        if missing:
            bad.append(f"{where}: missing {missing}")
            continue
        raw, ref = r["raw_output"], r["reference_answer"]
        extraction, verdicts = r["extraction"] or {}, r["verdicts"] or {}
        checks = [
            (r["sample_index"] in (0, 1), "sample_index not in 0..1"),
            (r["seed"] == 7100 + r["sample_index"], "seed != seed_base + sample_index"),
            (hs.is_hex64(r["prompt_sha256"]), "prompt_sha256 not hex"),
            (isinstance(raw, str) and r["raw_output_sha256"] == hs.sha256_text(raw), "raw_output_sha256 mismatch"),
            (isinstance(ref, str) and r["reference_sha256"] == hs.sha256_text(ref), "reference_sha256 mismatch"),
            (r["item_id"] not in upstream or upstream[r["item_id"]].get("answer") == ref,
             "reference_answer differs from the upstream answer"),
            (set(extraction) >= {"answer", "rule", "status"}, "extraction lacks answer/rule/status"),
            (extraction.get("status") in ("ok", "fallback", "no_answer", "truncated"), "extraction.status invalid"),
            (isinstance(verdicts.get("primary"), bool) and isinstance(verdicts.get("secondary"), bool),
             "verdicts not booleans"),
            (extraction.get("answer") is not None or not (verdicts.get("primary") or verdicts.get("secondary")),
             "answer None scored correct"),
            (isinstance(r["thinking_present"], bool) and isinstance(r["truncated"], bool),
             "thinking_present/truncated not booleans"),
            ("</think>" not in raw or r["thinking_present"] is True, "think block present but thinking_present false"),
            (not r["thinking_present"] or r["truncated"] or "</think>" in raw,
             "thinking reported but raw output lacks </think> (reasoning parsed away?)"),
            (isinstance(r["finish_reason"], str), "finish_reason not a string"),
            (hs.is_int(r["tokens_generated"]) and 0 <= r["tokens_generated"]
             and (not hs.is_int(max_tokens) or r["tokens_generated"] <= max_tokens), "tokens_generated out of range"),
            (hs.is_number(r["timing_ms"]) and r["timing_ms"] >= 0, "timing_ms invalid"),
        ]
        bad += [f"{where}: {message}" for ok, message in checks if not ok]
    assert not bad, "; ".join(bad[:20])


def test_ho_rt_10_real_run_rescores_exactly_and_reports_aggregates(sandbox):
    """HO-RT-10 · §3 rescore `all_match`, §6.2, §6.4, §7 aggregates only:
    REQUIRES the real model and runtime. Rescoring a completed k=1 smoke run
    exits 0 with a §6.4 rescore.json (all_match true, a matching record per
    item x sample), leaving the run's files untouched; its report (in the run
    directory) carries the primary accuracy and Wilson
    interval, the disagreement and truncation counts computed from
    items.jsonl, and no raw output, problem text or long reference answer."""
    protocol, manifest = _prepared(sandbox)
    proc = sandbox.finish(*_run_args(protocol, manifest), timeout=hs.run_timeout())
    assert proc.returncode == hs.EXIT_OK, hs.describe(proc)
    run_id = _run_id_from(proc, sandbox)
    run_dir = sandbox.runs / run_id
    before = hs.file_bytes(run_dir)
    rescore = sandbox.cli("rescore", "--run", run_id, timeout=3600)
    assert rescore.returncode == hs.EXIT_OK, hs.describe(rescore)
    doc = hs.read_json(run_dir / "rescore.json")
    records = hs.read_jsonl(run_dir / "items.jsonl")
    problems = hs.rescore_problems(doc, run_id, {(r["item_id"], r["sample_index"]) for r in records})
    assert not problems, "rescore.json violates §6.4: " + "; ".join(problems[:10])
    assert doc["all_match"] is True, "rescore of a real run is not all_match"
    after = hs.file_bytes(run_dir)
    changed = [name for name, data in before.items() if after.get(name) != data]
    assert not changed, f"rescore modified original run files {changed}"

    _, report, md = hs.run_report(sandbox, run_id)
    n = len(records)
    primary = sum(1 for r in records if r["verdicts"]["primary"])
    disagreements = sum(1 for r in records if r["verdicts"]["primary"] != r["verdicts"]["secondary"])
    truncations = sum(1 for r in records if r["truncated"])
    assert hs.has_close(hs.numbers(report), primary / n), "primary accuracy not in report.json"
    lo, hi = hs.wilson(primary, n)
    assert hs.has_interval(report, lo, hi), "primary Wilson interval not in report.json"
    for needle, count in (("disagree", disagreements), ("truncat", truncations)):
        values = hs.numbers_under(report, needle)
        assert any(v == count for v in values) or hs.has_close(values, count / n), f"{needle} count {count} not reported"
    blob = json.dumps(report, ensure_ascii=False) + "\n" + md
    raw_leaks = [r["item_id"] for r in records if len(r["raw_output"]) >= 80 and r["raw_output"][:80] in blob]
    ref_leaks = [r["item_id"] for r in records if len(r["reference_answer"]) >= 12 and r["reference_answer"] in blob]
    problem_leaks = hs.leaked_problems([blob], hs.cached_records(sandbox.cache))
    assert not raw_leaks, f"raw output text in the report for {raw_leaks[:5]}"
    assert not ref_leaks, f"reference answers in the report for {ref_leaks[:5]}"
    assert not problem_leaks, f"{len(problem_leaks)} problem texts in the report"


def test_ho_rt_11_resume_generates_only_missing_pairs(sandbox):
    """HO-RT-11 · §6.3 resume and §6.1 attempts: REQUIRES the real model and
    runtime. A smoke run interrupted after its first completed line records
    an interrupted attempt; `run --resume` exits 0, keeps every line
    completed before the interrupt unchanged, adds only the missing pairs
    (no duplicates, one line per smoke item), appends a second, completed
    attempt that records what it resumed from, and ends with status
    completed. Skips if the run finished before the interrupt landed."""
    protocol, manifest = _prepared(sandbox)
    with sandbox.spawn(*_run_args(protocol, manifest)) as sp:
        run_dir = hs.wait_for_items(sandbox, sp, 1, hs.run_timeout())
        assert run_dir is not None, "no completed items.jsonl line observed\n" + hs.describe(sp.completed())
        hs.interrupt_and_wait(sp)
    run = hs.read_json(run_dir / "run.json")
    if run.get("status") == "completed":
        pytest.skip("the run completed before SIGINT was delivered")
    attempts = run.get("attempts") or []
    assert attempts and attempts[-1].get("status") == "interrupted", f"attempts after SIGINT: {attempts!r}"
    done = {(r["item_id"], r["sample_index"]): r for r in hs.complete_jsonl_lines(run_dir / "items.jsonl")}
    proc = sandbox.finish("run", "--resume", run_dir.name, timeout=hs.run_timeout())
    assert proc.returncode == hs.EXIT_OK, hs.describe(proc)
    records = hs.read_jsonl(run_dir / "items.jsonl")
    pairs = [(r["item_id"], r["sample_index"]) for r in records]
    assert len(pairs) == len(set(pairs)), "resume duplicated item x sample lines"
    assert {i for i, _ in pairs} == set(_smoke_ids(manifest)) and len(pairs) == len(_smoke_ids(manifest)), \
        "resumed run does not hold exactly one line per smoke item"
    current = dict(zip(pairs, records))
    regenerated = [pair for pair, record in done.items() if current.get(pair) != record]
    assert not regenerated, f"pairs completed before the interrupt were regenerated or altered: {regenerated[:5]}"
    run = hs.read_json(run_dir / "run.json")
    attempts = run.get("attempts") or []
    assert len(attempts) == 2, f"expected two attempt records, found {attempts!r}"
    assert attempts[0].get("status") == "interrupted" and attempts[1].get("status") == "completed", attempts
    assert attempts[1].get("resumed_from") is not None, "the resumed attempt does not record resumed_from"
    assert run.get("status") == "completed", f"run status {run.get('status')!r} after resume"


@pytest.mark.parametrize(
    "field, replacement",
    [
        ("protocol_sha256", "0" * 64),
        ("manifest_sha256", "0" * 64),
        ("model_artifact_sha256", "0" * 64),
        ("runtime_build", "b0-holdout"),
    ],
    ids=["HO-RT-12a-protocol", "HO-RT-12b-manifest", "HO-RT-12c-model", "HO-RT-12d-runtime"],
)
def test_ho_rt_12_resume_refused_when_identity_differs(sandbox, field, replacement):
    """HO-RT-12a..d · §6.3 "Resume is allowed only when the protocol,
    manifest, model, and runtime build are all identical to the original run.
    Any difference exits 2 and generates nothing" (and §6.1 copy hashes):
    REQUIRES the real model and runtime. After an interrupted smoke run, its
    recorded protocol / manifest / model hash or runtime build is changed;
    `run --resume` exits 2 and items.jsonl is unchanged."""
    protocol, manifest = _prepared(sandbox)
    with sandbox.spawn(*_run_args(protocol, manifest)) as sp:
        run_dir = hs.wait_for_run_dir(sandbox, sp, STARTUP_TIMEOUT)
        assert run_dir is not None, "no run directory appeared\n" + hs.describe(sp.completed())
        sp.wait_for_listener(STARTUP_TIMEOUT)
        hs.interrupt_and_wait(sp)
    run_json = run_dir / "run.json"
    run = hs.read_json(run_json)
    if run.get("status") == "completed":
        pytest.skip("the run completed before SIGINT was delivered")
    assert field in run, f"run.json lacks {field}"
    run[field] = replacement
    hs.write_json(run_json, run)
    items = run_dir / "items.jsonl"
    before = items.read_bytes() if items.exists() else None
    proc = sandbox.finish("run", "--resume", run_dir.name, timeout=STARTUP_TIMEOUT)
    assert proc.returncode == hs.EXIT_INTEGRITY, hs.describe(proc)
    after = items.read_bytes() if items.exists() else None
    assert after == before, "resume generated output although identities differ"


def test_ho_rt_13_used_tier_is_immutable(sandbox):
    """HO-RT-13 · §5.1 "A tier is immutable once it is used" + §3
    apply-verdicts "the result would change a tier that any existing run has
    already used: exits 3", failing without changing anything: REQUIRES the
    real model and runtime and the pinned dataset. Once a run has started on
    a freshly built manifest's smoke tier, verdicts excluding every flagged
    pair either exit 3 leaving the manifest byte-identical, or exit 0
    leaving the smoke tier unchanged. Skips if no pair is flagged."""
    protocol, _ = _inputs()
    hs.populate_cache(sandbox)
    manifest = sandbox.work / "manifest.json"
    build = sandbox.cli("data", "build-manifest", "--out", manifest, timeout=1800)
    assert build.returncode == hs.EXIT_OK, hs.describe(build)
    flagged = hs.find_flagged_file(sandbox.cache)
    assert flagged is not None, "no flagged file in the dataset cache"
    pairs = hs.read_json(flagged).get("pairs") or []
    if not pairs:
        pytest.skip("the pinned dataset produced no flagged pairs")
    hs.verify_before_run(sandbox, protocol)
    with sandbox.spawn(*_run_args(protocol, manifest)) as sp:
        run_dir = hs.wait_for_run_dir(sandbox, sp, STARTUP_TIMEOUT)
        assert run_dir is not None, "no run directory appeared\n" + hs.describe(sp.completed())
        hs.interrupt_and_wait(sp)
    original = manifest.read_bytes()
    exclude_all = {str(p.get("id")): "exclude" for p in pairs}
    verdicts = hs.write_json(sandbox.root / "outside" / "verdicts.json", hs.verdicts_doc(flagged, pairs, exclude_all))
    proc = sandbox.cli("data", "apply-verdicts", "--manifest", manifest, "--verdicts", verdicts, timeout=1800)
    if proc.returncode == hs.EXIT_PRECONDITION:
        assert manifest.read_bytes() == original, "apply-verdicts exited 3 but changed the manifest"
    else:
        assert proc.returncode == hs.EXIT_OK, hs.describe(proc)
        smoke_before = json.loads(original.decode("utf-8"))["tiers"]["smoke"]
        assert hs.read_json(manifest)["tiers"]["smoke"] == smoke_before, "a used smoke tier was changed (expected exit 3)"


def test_ho_rt_14_run_with_mismatched_manifest_source_exits_2(sandbox):
    """HO-RT-14 · §3 run "Exits 2 if the manifest source differs from the
    protocol's dataset.source": REQUIRES the real model and runtime. With
    both verification records present, a copy of the committed manifest
    whose source pins a different file hash than the protocol makes `run`
    exit 2."""
    protocol, manifest = _prepared(sandbox)
    doc = hs.read_json(manifest)
    doc["source"]["files"][0]["sha256"] = "0" * 64
    tampered = hs.write_json(sandbox.work / "manifest.json", doc)
    proc = sandbox.finish(*_run_args(protocol, tampered), timeout=STARTUP_TIMEOUT)
    assert proc.returncode == hs.EXIT_INTEGRITY, hs.describe(proc)
