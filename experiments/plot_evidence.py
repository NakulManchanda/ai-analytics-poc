#!/usr/bin/env python3
"""Plot submission graphs from retained Prometheus range exports and per-request evidence (no cluster needed).

Usage: uv run --project experiments python experiments/plot_evidence.py [--out DIR]
Inputs are the `metrics/inference/<run id>/prometheus_range/*.json` files from `make inference-pull-range` and the
`requests.jsonl` of the E4 arms. Series are WINDOW-level (15 s steps), not per request.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path("metrics/inference")
E0, E2, E3, E4 = (
    "d123-20261004-e0-2321",
    "d123-20261004-e2-2357",
    "d123-20261005-e3-0040",
    "d123-20261010-e4-0003",
)
LABEL_KEYS = ("worker", "app", "pod", "instance", "reason", "class", "policy", "gpu")


def series(
    run: str, name: str, job: str | None = None, instance_prefix: str | None = None
):
    """[(label, minutes since first sample, values)] for one exported query; NaN points are dropped."""
    data = json.loads((ROOT / run / "prometheus_range" / f"{name}.json").read_text())[
        "data"
    ]["result"]
    out = []
    for s in data:
        metric = s["metric"]
        if job and metric.get("job") != job:
            continue
        if instance_prefix and not metric.get("instance", "").startswith(
            instance_prefix
        ):
            continue
        pts = [(float(t), float(v)) for t, v in s["values"] if not math.isnan(float(v))]
        if pts:
            label = (
                ",".join(f"{k}={metric[k]}" for k in LABEL_KEYS if k in metric) or name
            )
            out.append((label, pts))
    t0 = min((p[0][0] for _, p in out), default=0.0)
    return [(lab, [(t - t0) / 60 for t, _ in p], [v for _, v in p]) for lab, p in out]


def line(ax, run, name, job=None, scale=1.0, active=None, instance_prefix=None):
    """Plot every series of one query; `active` (a list) collects x values where a series changes from its first value."""
    for label, xs, ys in series(run, name, job, instance_prefix):
        short = next(
            (
                p.split("=", 1)[1].split(".")[0]
                for p in label.split(",")
                if p.startswith(("worker=", "app=", "instance="))
            ),
            label,
        )
        ax.plot(xs, [y * scale for y in ys], label=short, lw=1.4)
        if active is not None:
            active += [x for x, y in zip(xs, ys) if abs(y - ys[0]) > 1e-9]
    ax.set_xlabel("minutes since window start")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7)


def crop(ax, active, pad=1.0) -> None:
    if active:
        ax.set_xlim(min(active) - pad, max(active) + pad)


def save(fig, out: Path, name: str, title: str) -> None:
    fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(out / name, dpi=130)
    plt.close(fig)
    print("wrote", out / name)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/inference-experiments/evidence/plots")
    out = Path(ap.parse_args().out)
    out.mkdir(parents=True, exist_ok=True)
    pods = "kubernetes-pods"

    fig, ax = plt.subplots(2, 1, figsize=(8, 6), sharex=True)
    act: list = []
    line(ax[0], E0, "vllm_requests_running", pods, active=act)
    ax[0].axhline(8, color="k", ls="--", lw=0.8, label="--max-num-seqs 8")
    ax[0].set_ylabel("requests running")
    ax[0].legend(fontsize=7)
    line(ax[1], E0, "vllm_requests_waiting", pods, active=act)
    ax[1].set_ylabel("requests waiting")
    crop(ax[0], act)
    save(
        fig,
        out,
        f"e0-slots-running-waiting-{E0}.png",
        f"E0: worker B reaches the 8-slot cap and requests start waiting (15 s samples, {E0})",
    )

    fig, ax = plt.subplots(figsize=(8, 3.6))
    act = []
    line(ax, E0, "vllm_kv_cache_usage", pods, scale=100, active=act)
    crop(ax, act)
    ax.set_ylim(0, 100)
    ax.set_ylabel("KV cache usage %")
    save(
        fig,
        out,
        f"e0-kv-cache-usage-{E0}.png",
        f"E0: KV never gets near full (peak below 50%, {E0})",
    )

    fig, ax = plt.subplots(figsize=(8, 3.6))
    for name, tag in (("dcgm_fb_used_mib", "used"), ("dcgm_fb_free_mib", "free")):
        for _, xs, ys in series(E0, name, "dcgm"):
            ax.plot(xs, ys, label=f"framebuffer {tag}", lw=1.4)
    ax.axhline(40960, color="k", ls="--", lw=0.8, label="A100 40 GiB total")
    ax.set_ylim(0, 44000)
    ax.set_xlabel("minutes since window start")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7)
    ax.set_ylabel("framebuffer MiB")
    save(
        fig, out, f"e0-gpu-memory-{E0}.png", f"E0: GPU framebuffer used vs free ({E0})"
    )

    fig, ax = plt.subplots(figsize=(8, 3.6))
    act = []
    line(
        ax,
        E2,
        "vllm_prefix_hit_ratio",
        scale=100,
        active=act,
        instance_prefix="inference-worker-",
    )
    crop(ax, act)
    ax.set_ylabel("prefix cache hit ratio %")
    save(
        fig,
        out,
        f"e2-prefix-hit-ratio-{E2}.png",
        f"E2: prefix cache hit ratio per worker ({E2})",
    )

    fig, ax = plt.subplots(figsize=(8, 3.6))
    act = []
    line(ax, E3, "orch_pick_rate", active=act)
    crop(ax, act)
    ax.set_ylabel("picks/s")
    save(
        fig,
        out,
        f"e3-placement-picks-{E3}.png",
        f"E3: placement picks per second by policy, worker, reason ({E3})",
    )

    # Gateway queue wait per arm and tenant, from the x-queue-wait-ms response header of completed requests
    # (the 15 s gauge reads 0 and the p95 histogram has too few points to draw a line).
    fig, ax = plt.subplots(figsize=(8, 3.8))
    labels, p50s, p95s, maxs = [], [], [], []
    for arm, suffix in (("OFF", "041045"), ("ON", "043003")):
        rows = [
            json.loads(x)
            for x in (
                ROOT
                / E4
                / f"e4_admission_overload_manual_20261010_{suffix}"
                / "requests.jsonl"
            )
            .read_text()
            .splitlines()
        ]
        for tenant in ("tenant_interactive", "tenant_noisy"):
            w = sorted(
                float((r.get("gateway_headers") or {}).get("x-queue-wait-ms", 0))
                for r in rows
                if r["tenant_id"] == tenant and r["status"] == "completed"
            )
            if w:
                labels.append(f"{arm}\n{tenant.replace('tenant_', '')}")
                p50s.append(w[len(w) // 2])
                p95s.append(w[min(len(w) - 1, int(0.95 * len(w)))])
                maxs.append(w[-1])
    x = range(len(labels))
    for i, (vals, name) in enumerate(((p50s, "p50"), (p95s, "p95"), (maxs, "max"))):
        bars = ax.bar([j + i * 0.27 for j in x], vals, 0.27, label=name)
        ax.bar_label(bars, fmt="%.0f", fontsize=6, padding=1)
    ax.set_xticks([j + 0.27 for j in x], labels, fontsize=8)
    ax.set_ylabel("gateway queue wait (ms)")
    ax.legend(fontsize=7)
    ax.grid(axis="y", alpha=0.3)
    save(
        fig,
        out,
        f"e4-gateway-queue-wait-{E4}.png",
        f"E4: gateway queue wait of completed requests (vLLM queue stayed 0, {E4})",
    )

    # Per-request outcome counts per arm: the Prometheus rate() series misses the first increment of a
    # labelled counter created during the burst (decode_slots), so the per-request rows are the reliable source.
    fig, ax = plt.subplots(figsize=(8, 3.8))
    arms = {}
    for label, suffix in (("admission OFF", "041045"), ("admission ON", "043003")):
        rows = [
            json.loads(x)
            for x in (
                ROOT
                / E4
                / f"e4_admission_overload_manual_20261010_{suffix}"
                / "requests.jsonl"
            )
            .read_text()
            .splitlines()
        ]
        arms[label] = Counter()
        for r in rows:
            if r["status"] == "completed":
                arms[label]["completed"] += 1
            else:
                h = (r.get("gateway_headers") or {}).get("x-admit-decision", "")
                arms[label][
                    (
                        h.replace("shed:", "shed ")
                        if h.startswith("shed:")
                        else (
                            "503 batch_slot_cap (placement)"
                            if "batch_slot_cap" in str(r["error"])
                            else r["status"]
                        )
                    )
                ] += 1
    kinds = sorted(
        {k for c in arms.values() for k in c}, key=lambda k: (k != "completed", k)
    )
    width = 0.8 / len(kinds)
    for i, kind in enumerate(kinds):
        ax.bar(
            [j + i * width for j in range(len(arms))],
            [arms[a].get(kind, 0) for a in arms],
            width,
            label=kind,
        )
    ax.set_xticks([j + 0.4 - width / 2 for j in range(len(arms))], list(arms))
    ax.set_ylabel("turns (of 48)")
    ax.legend(fontsize=7)
    ax.grid(axis="y", alpha=0.3)
    save(
        fig,
        out,
        f"e4-outcomes-by-arm-{E4}.png",
        f"E4: outcomes per arm, admission OFF vs ON ({E4})",
    )


if __name__ == "__main__":
    main()
