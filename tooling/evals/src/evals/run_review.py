"""Static, self-contained review.html for one run (no network, all text escaped)."""

from __future__ import annotations

import html
from pathlib import Path

from evals.run_compare import changes
from evals.runs import (
    DIMS,
    DIRECTIONS,
    TERMINAL_OK,
    declined_histogram,
    failure_histogram,
    format_histogram,
    load_baseline,
    load_run_reports,
    mcp_reason_histogram,
    merge_failed,
    read_jsonl,
)

CSS = """body{font:14px system-ui,sans-serif;margin:1rem;color:#222}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:4px;vertical-align:top}
.b{padding:1px 6px;border-radius:8px;font-size:12px;background:#eee}
.correct{background:#cfc}.incorrect,.failed,.regressed{background:#fbb}.needs_review{background:#fe9}
.improved{background:#bdf}.unchanged{background:#eee}.lateral{background:#fdb}.missing_evidence{background:#ddd;font-style:italic}.fail{background:#fee}pre{white-space:pre-wrap;margin:0}
.note{color:#900;font-size:12px}.hide{display:none}
button.on{background:#222;color:#fff}#shown{font-weight:600;margin-left:.5rem}"""

JS = """const rows=[...document.querySelectorAll('tbody tr')];let mode='all';
function match(r,m){return m=='all'||(m=='bad'?r.dataset.bad=='1':(r.dataset.dirs||'').split(' ').includes(m))}
const btns=[...document.querySelectorAll('button[data-m]')];
btns.forEach(b=>{b.dataset.l=b.textContent;b.textContent=b.dataset.l+' ('+rows.filter(r=>match(r,b.dataset.m)).length+')'});
function apply(){const q=document.getElementById('q').value.toLowerCase();let n=0;
rows.forEach(r=>{let ok=match(r,mode);
if(ok&&q)ok=r.textContent.toLowerCase().includes(q);r.classList.toggle('hide',!ok);if(ok)n++});
btns.forEach(b=>b.classList.toggle('on',b.dataset.m==mode));
document.getElementById('shown').textContent='Showing '+n+' of '+rows.length+' turns'}
btns.forEach(b=>b.onclick=()=>{mode=b.dataset.m;apply()});
document.getElementById('q').oninput=apply;apply();"""


def _e(s: object) -> str:
    return html.escape(str(s), quote=True)


def _badge(v: str, extra: str = "") -> str:
    return f'<span class="b {_e(v)}">{_e(extra or v)}</span>'


def render_review(run_dir: Path, baseline: Path | None = None) -> str:
    graded, _ = load_run_reports(run_dir)
    results = read_jsonl(run_dir / "results.jsonl")
    new = merge_failed(graded, results)
    ch = changes(new, load_baseline(baseline)) if baseline else {}
    body = []
    for r in sorted(results, key=lambda r: (r["case_id"], r["turn_index"])):
        k = (r["case_id"], r["turn_index"])
        g = new[k]
        bad = r["terminal"] != TERMINAL_OK or any(g[d] != "correct" for d in DIMS)
        dirs = [(d, ch[(k[0], k[1], d)]) for d in DIMS if (k[0], k[1], d) in ch]
        dirset = " ".join(sorted({v for _, v in dirs}))
        term = _e(r["terminal"])
        if r["terminal"] != TERMINAL_OK:
            term = f'<b>{term}</b><div class="note">see app/LiteLLM logs</div>'
        badges = " ".join(_badge(g[d], f"{d}: {g[d]}") for d in DIMS)
        dbadges = " ".join(_badge(v, f"{d} {v}") for d, v in dirs if v != "unchanged")
        body.append(
            f'<tr data-bad="{int(bad)}" data-dirs="{_e(dirset)}"'
            f'{" class=fail" if r["terminal"] != TERMINAL_OK else ""}>'
            f"<td>{_e(k[0])}#{k[1]}</td><td>{_e(r.get('question', ''))}</td>"
            f"<td>{badges} {dbadges}</td>"
            f"<td><pre>{_e(g['actual'])}</pre>expected: {_e(g['expected'])}</td>"
            f"<td>{_e(g['reasons'])}</td><td><pre>{_e(g['answer_text'])}</pre></td>"
            f"<td>{term}</td></tr>"
        )
    reg_btn = (
        " ".join(f'<button data-m="{d}">{d}</button>' for d in DIRECTIONS)
        if baseline
        else ""
    )
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{_e(run_dir.name)}</title><style>{CSS}</style></head><body>"
        f"<h1>{_e(run_dir.name)}</h1>"
        f'<p id="failure-codes">Failure codes: '
        f"{_e(format_histogram(failure_histogram(run_dir, results)))}</p>"
        f'<p id="declined-outcomes">Declined outcomes: '
        f"{_e(format_histogram(declined_histogram(run_dir, results)))}</p>"
        f'<p id="mcp-failure-reasons">MCP failure reasons: '
        f"{_e(format_histogram(mcp_reason_histogram(run_dir, results)))}</p>"
        '<p><button data-m="all">all</button> <button data-m="bad">failed / not correct</button> '
        f'{reg_btn} <input id="q" placeholder="search"> <span id="shown"></span></p>'
        "<table><thead><tr><th>turn</th><th>question</th><th>outcome</th><th>tools (actual)</th>"
        "<th>reasons</th><th>answer</th><th>terminal</th></tr></thead><tbody>"
        + "".join(body)
        + f"</tbody></table><script>{JS}</script></body></html>\n"
    )
