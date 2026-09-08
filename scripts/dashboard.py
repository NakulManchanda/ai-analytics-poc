"""Streamlit Telemetry & Comparison Dashboard.

Uses DuckDB to analyze local JSONL metrics (metrics/runs.jsonl or downloaded
CloudWatch EMF logs) for latency waterfall, TTFT, token, and cost comparisons.
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import streamlit as st

st.set_page_config(
    page_title="AI Analytics — Telemetry & Comparison Dashboard",
    page_icon="📊",
    layout="wide",
)

st.title("📊 AI Analytics — Telemetry & Model Comparison Dashboard")
st.caption(
    "Analyze real-time latency waterfall, TTFT, token burn, and cost across milestones and models."
)

# 1. Resolve metrics file path
DEFAULT_METRICS_PATH = Path("metrics/runs.jsonl")
ALT_METRICS_PATH = Path("local_runs.jsonl")

metrics_file_candidate = (
    DEFAULT_METRICS_PATH
    if DEFAULT_METRICS_PATH.exists()
    else ALT_METRICS_PATH
    if ALT_METRICS_PATH.exists()
    else None
)

with st.sidebar:
    st.header("⚙️ Configuration")
    metrics_path_input = st.text_input(
        "Metrics JSONL Path",
        value=str(metrics_file_candidate or DEFAULT_METRICS_PATH),
    )
    refresh_button = st.button("🔄 Refresh Data")

metrics_path = Path(metrics_path_input)

# 2. Check if metrics file exists and is populated
if not metrics_path.exists() or metrics_path.stat().st_size == 0:
    st.info(
        f"**No metrics log found at `{metrics_path}` yet.**\n\n"
        "Run queries via the local application to populate metrics:\n"
        "- Run `make dev` or `make local-bedrock-compose`\n"
        "- Submit analytics questions in the UI at `http://localhost:3000`\n"
        "- Or download public deployment logs:\n"
        "  ```bash\n"
        "  aws logs tail /aws/ecs/ai-app --filter-pattern '{ $.entity_type = \"run\" }' --format json > metrics/runs.jsonl\n"
        "  ```"
    )
    st.stop()

# 3. Query metrics using DuckDB
try:
    escaped_path = str(metrics_path).replace("'", "''")
    df = duckdb.query(
        f"""
        SELECT
            run_id,
            conversation_id,
            COALESCE(Milestone, milestone, 'unspecified') AS milestone,
            COALESCE(Model, model, 'default') AS model,
            COALESCE(TurnType, turn_type, 'text') AS turn_type,
            status,
            TRY_CAST(COALESCE(EndToEndLatency, end_to_end_latency_ms) AS DOUBLE) AS end_to_end_latency_ms,
            TRY_CAST(COALESCE(ProposalLLMLatency, proposal_llm_latency_ms) AS DOUBLE) AS proposal_llm_latency_ms,
            TRY_CAST(COALESCE(ToolExecutionLatency, tool_latency_ms) AS DOUBLE) AS tool_latency_ms,
            TRY_CAST(COALESCE(FinalAnswerLLMLatency, final_answer_llm_latency_ms) AS DOUBLE) AS final_answer_llm_latency_ms,
            TRY_CAST(COALESCE(TimeToOneFirstToken, ttft_latency_ms) AS DOUBLE) AS ttft_latency_ms,
            TRY_CAST(COALESCE(InputTokens, input_tokens, 0) AS BIGINT) AS input_tokens,
            TRY_CAST(COALESCE(OutputTokens, output_tokens, 0) AS BIGINT) AS output_tokens,
            TRY_CAST(COALESCE(TotalCostUSD, estimated_cost_usd, 0.0) AS DOUBLE) AS estimated_cost_usd,
            COALESCE(started_at, timestamp) AS started_at
        FROM read_json_auto('{escaped_path}')
        ORDER BY started_at DESC
        """
    ).df()
except Exception as err:
    st.error(f"Failed to read `{metrics_path}`: {err}")
    st.stop()

if df.empty:
    st.warning("Metrics file exists but contains no valid records.")
    st.stop()

# 4. Sidebar filters
with st.sidebar:
    st.header("🔍 Filters")
    available_milestones = sorted(df["milestone"].dropna().unique().tolist())
    selected_milestones = st.multiselect(
        "Milestone",
        options=available_milestones,
        default=available_milestones,
    )

    available_models = sorted(df["model"].dropna().unique().tolist())
    selected_models = st.multiselect(
        "Model",
        options=available_models,
        default=available_models,
    )

    available_statuses = sorted(df["status"].dropna().unique().tolist())
    selected_statuses = st.multiselect(
        "Status",
        options=available_statuses,
        default=available_statuses,
    )

filtered_df = df[
    df["milestone"].isin(selected_milestones)
    & df["model"].isin(selected_models)
    & df["status"].isin(selected_statuses)
]

if filtered_df.empty:
    st.warning("No runs match the selected filters.")
    st.stop()

# 5. Summary KPI Cards
st.subheader("📌 Key Performance Indicators")
col1, col2, col3, col4, col5 = st.columns(5)

total_runs = len(filtered_df)
avg_ttft = filtered_df["ttft_latency_ms"].dropna().mean()
p95_e2e = filtered_df["end_to_end_latency_ms"].dropna().quantile(0.95)
avg_tool = filtered_df["tool_latency_ms"].dropna().mean()
total_cost = filtered_df["estimated_cost_usd"].dropna().sum()

col1.metric("Total Runs", f"{total_runs:,}")
col2.metric(
    "Avg TTFT",
    f"{avg_ttft:.0f} ms" if not pd_isna(avg_ttft) else "N/A",
)
col3.metric(
    "p95 E2E Latency",
    f"{p95_e2e:.0f} ms" if not pd_isna(p95_e2e) else "N/A",
)
col4.metric(
    "Avg Tool Latency",
    f"{avg_tool:.0f} ms" if not pd_isna(avg_tool) else "N/A",
)
col5.metric("Total Cost", f"${total_cost:.4f}")


def pd_isna(val: object) -> bool:
    return val is None or val != val


# 6. Visualizations
tab_lat, tab_ttft, tab_cost = st.tabs(
    ["⏱️ Latency Waterfall & Breakdown", "⚡ TTFT Trend", "💰 Cost & Tokens"]
)

with tab_lat:
    st.markdown("#### Latency Waterfall by Milestone")
    milestone_summary = (
        filtered_df.groupby(["milestone", "model"])
        .agg(
            {
                "proposal_llm_latency_ms": "mean",
                "tool_latency_ms": "mean",
                "final_answer_llm_latency_ms": "mean",
                "end_to_end_latency_ms": "mean",
                "run_id": "count",
            }
        )
        .reset_index()
        .rename(columns={"run_id": "runs"})
    )
    st.dataframe(
        milestone_summary.style.format(
            {
                "proposal_llm_latency_ms": "{:.0f} ms",
                "tool_latency_ms": "{:.0f} ms",
                "final_answer_llm_latency_ms": "{:.0f} ms",
                "end_to_end_latency_ms": "{:.0f} ms",
            }
        ),
        use_container_width=True,
    )

    st.bar_chart(
        milestone_summary.set_index("milestone")[
            [
                "proposal_llm_latency_ms",
                "tool_latency_ms",
                "final_answer_llm_latency_ms",
            ]
        ]
    )

with tab_ttft:
    st.markdown("#### Time-to-First-Token (TTFT) Over Time")
    ttft_df = (
        filtered_df[filtered_df["ttft_latency_ms"].notna()]
        .sort_values("started_at")
        .copy()
    )
    if not ttft_df.empty:
        st.line_chart(ttft_df.set_index("started_at")["ttft_latency_ms"])
    else:
        st.info("No runs with streaming TTFT recorded yet in selection.")

with tab_cost:
    st.markdown("#### Tokens & Spend per Milestone")
    cost_summary = (
        filtered_df.groupby(["milestone", "model"])
        .agg(
            {
                "input_tokens": "sum",
                "output_tokens": "sum",
                "estimated_cost_usd": "sum",
                "run_id": "count",
            }
        )
        .reset_index()
        .rename(columns={"run_id": "runs"})
    )
    st.dataframe(
        cost_summary.style.format(
            {
                "input_tokens": "{:,}",
                "output_tokens": "{:,}",
                "estimated_cost_usd": "${:.4f}",
            }
        ),
        use_container_width=True,
    )

# 7. Detailed Run History
st.markdown("---")
st.subheader("📋 Detailed Run History")
st.dataframe(
    filtered_df[
        [
            "started_at",
            "milestone",
            "model",
            "status",
            "ttft_latency_ms",
            "end_to_end_latency_ms",
            "tool_latency_ms",
            "input_tokens",
            "output_tokens",
            "estimated_cost_usd",
            "run_id",
        ]
    ],
    use_container_width=True,
)
