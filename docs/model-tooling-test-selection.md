# Model-tooling test selection

The selection index in `tests/visible/model-tooling-selection.json` records
maintainer-ruled harness and proof checks. Source test files are copied
verbatim; their SHA-256 values bind this inventory to the reviewed assertions.
It also lists proposed, advisory and retired IDs excluded from required
execution. Copying a mixed-status module does not authorize its other cases.

The index names 84 required harness IDs, which expand to 138 collected cases,
and seven required proof-record IDs, which expand to seven collected cases.
The four resume-identity cases have separate explicit parameter selectors.
Counts are discovery checks, not passing test results or total project
regression counts. Existing legacy regression tests are a separate inventory;
26 retired runtime obligations have required successors in this selection.

Use explicit nodes and a scoped operator setup. Do not use blanket collection
or execution of all visible/holdout directories: historical sources include
retired helpers and cases with different prerequisites and ownership rules.
Keep their history, but do not enable them by omission. Disable plugin autoload,
bytecode and pytest cache and avoid unrelated configuration/conftest discovery.

Collection-only validation must use `--collect-only`, explicit node arguments,
`-c /dev/null`, `--confcutdir=tests/visible`, `--rootdir=.`, and
`-p no:cacheprovider`, with a fresh log destination in assigned scratch.
No fixture setup, test body, process launch, network request or key lookup is
needed for collection. Inspecting 138 plus seven nodes does not prove those
assertions pass. The collection operator supplied with the handoff additionally
refuses process/network operations and writes outside its scratch directory;
that operator is input-only and is not part of the repository.

Actual execution requires the applicable explicit environment, verified input
copies, fresh run stores, process-ownership and teardown controls, safe
diagnostics and authorization. Runtime, fault, network, retained-record and
key-handling paths must keep their separate scopes. A missing prerequisite or
skip remains not run. Never supply a token, relax an assertion or change a
registered file to force a pass. Model conversion and physical proof remain
separate work.
