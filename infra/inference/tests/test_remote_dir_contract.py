"""Contract tests guarding against the quoted-tilde remote-path bug.

`remote_dir()` in `lib.sh` returns `~/ai-analytics-inference` by default. That
text is expanded *locally* by command substitution (`$(remote_dir)`) before
being spliced into a heredoc string that is sent to the remote host over SSH.
If a caller wraps that spliced text in single or double quotes on the remote
side, the leading `~` is treated as a literal character by the remote shell
instead of being tilde-expanded to the remote home directory - while rsync's
own remote-path handling *does* tilde-expand unquoted `~`. That mismatch is
what caused `make inference-pull-evidence` to fail with "No such file or
directory" (Error 23): the capture step created `./~/ai-analytics-inference/...`
locally-relative-looking directory on the remote host, but the pull step's
rsync looked for the tilde-expanded absolute path.

These tests assert that no script under infra/inference/scripts wraps a
`$(remote_dir)`-derived path in quotes before sending it to the remote host,
and that pull-evidence.sh's capture (mkdir) and pull (rsync) steps resolve
the remote evidence directory from the same expression.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "infra" / "inference" / "scripts"

# Variables that are locally assigned from remote_dir()'s (locally expanded,
# tilde-containing) output and later spliced into a remote-executed script.
REMOTE_DIR_DERIVED_VARS = ("REMOTE_BASE", "TARGET", "RDIR")


def _script_text(name: str) -> str:
    path = SCRIPTS / name
    assert path.is_file(), f"expected script missing: {path}"
    return path.read_text(encoding="utf-8")


def test_no_script_wraps_remote_dir_substitution_in_quotes() -> None:
    """`$(remote_dir)` must never be wrapped in ' or " before reaching the remote shell.

    Quoting suppresses tilde expansion on the remote host, so any occurrence
    of `'$(remote_dir)` / `"$(remote_dir)` (or the closing counterpart) is a
    regression of the Error 23 bug.
    """
    offending: list[str] = []
    pattern = re.compile(r"""['"]\$\(remote_dir\)""")
    # A plain local assignment (e.g. `RDIR="$(remote_dir)"`) is safe: it just
    # captures remote_dir()'s literal output for later (unquoted) remote use.
    safe_assignment = re.compile(r'^\s*[A-Za-z_][A-Za-z0-9_]*="\$\(remote_dir\)"\s*$')
    for path in sorted(SCRIPTS.glob("*.sh")):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if safe_assignment.match(line):
                continue
            if pattern.search(line):
                offending.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offending, (
        "Found quoted $(remote_dir) substitutions that will not tilde-expand "
        f"on the remote host: {offending}"
    )


def test_no_script_wraps_remote_dir_derived_variable_in_quotes_for_remote_use() -> None:
    """Variables holding remote_dir()'s literal (unexpanded ~) output must stay
    unquoted wherever they are sent to the remote shell for path use (mkdir,
    ls, etc.) - matching how rsync's own remote-path tilde expansion behaves.
    """
    offending: list[str] = []
    for name in REMOTE_DIR_DERIVED_VARS:
        pattern = re.compile(rf"""['"]\$\{{?{name}\}}?['"/]*['"]""")
        for path in sorted(SCRIPTS.glob("*.sh")):
            text = path.read_text(encoding="utf-8")
            if f'"{name}="$(remote_dir)"' in text.replace(" ", "") or f"{name}=$(remote_dir)" in text.replace(" ", ""):
                pass  # the initial local assignment is fine and expected
            for lineno, line in enumerate(text.splitlines(), start=1):
                # Skip the safe local assignment lines themselves.
                if re.match(rf'^\s*{name}="\$\(remote_dir\)"', line):
                    continue
                if re.search(rf"""mkdir[^\n]*['"]\$\{{?{name}\}}?['"]""", line):
                    offending.append(f"{path.name}:{lineno}: {line.strip()}")
                if re.search(rf"""ls[^\n]*['"]\$\{{?{name}\}}?['"]""", line):
                    offending.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offending, (
        f"Found remote_dir()-derived variables quoted before remote path use: {offending}"
    )


def test_pull_evidence_capture_and_rsync_share_the_same_remote_dir_expression() -> None:
    """The mkdir'd (capture) path and the rsync'd (pull) path must be built
    from the same `$(remote_dir)/evidence/$RUN_ID` expression so they always
    agree, regardless of how INFERENCE_REMOTE_DIR is configured.
    """
    text = _script_text("pull-evidence.sh")

    capture_match = re.search(r"EVID_DIR=(\$\(remote_dir\)/evidence/\$RUN_ID)", text)
    assert capture_match, "pull-evidence.sh must assign EVID_DIR from $(remote_dir)/evidence/$RUN_ID"
    assert not re.search(r'EVID_DIR=["\']', text), (
        "EVID_DIR must not be quoted at assignment time on the remote side; "
        "quoting suppresses tilde expansion (Error 23 regression)"
    )

    rsync_match = re.search(
        r'"\$\(ssh_target\):\$\(remote_dir\)/evidence/\$RUN_ID/"', text
    )
    assert rsync_match, "rsync source must pull from $(remote_dir)/evidence/$RUN_ID/"


def test_pull_evidence_skips_summarizer_when_no_capacity_evidence_present() -> None:
    """A cluster-snapshot-only pull (no capacity run) must not fail the whole
    script just because evidence.py's capacity manifest validation requires
    raw/responses.jsonl (or a fallback) to exist.
    """
    text = _script_text("pull-evidence.sh")
    assert "HAS_CAPACITY_EVIDENCE" in text
    assert "Skipping evidence manifest summarizer" in text
    # The strict inference-run path must still call evidence.py unconditionally
    # from the Makefile after pull-evidence.sh succeeds.
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "infra/inference/experiments/evidence.py --run-id" in makefile
