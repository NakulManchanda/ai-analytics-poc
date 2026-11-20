// Runtime validator and cross-agent synthesizer for artifact_envelope.v1.
// Mirrors services/app/app/artifacts/envelope.py.

export const ARTIFACT_ENVELOPE_SCHEMA_VERSION = "artifact_envelope.v1" as const;
export const MAX_ENVELOPE_BYTES = 96 * 1024;

export type ArtifactKind = "table" | "time_series" | "segment_comparison" | "map";

export interface ArtifactProvenance {
  run_id: string;
  agent_id: string;
  tool_call_id: string;
  tool_name: string;
  query_id: string;
  dataset: string;
  time_window: string;
  filter_basis: string;
  truncated?: boolean;
}

export interface TableColumn {
  key: string;
  label: string;
  unit?: string | null;
}

export interface TableBody {
  columns: TableColumn[];
  rows: (string | number | boolean | null)[][];
  row_count: number;
  truncated?: boolean;
}

export interface TimeSeriesPoint {
  x: string;
  y: number;
}

export interface TimeSeriesBody {
  x_label: string;
  y_label: string;
  unit?: string | null;
  points: TimeSeriesPoint[];
  total_y: number;
}

export interface SegmentComparisonRow {
  segment: string;
  baseline: number;
  comparison: number;
  delta: number;
  delta_pct?: number | null;
}

export interface SegmentComparisonBody {
  dimension: string;
  metric: string;
  unit?: string | null;
  baseline_label: string;
  comparison_label: string;
  rows: SegmentComparisonRow[];
}

export interface ArtifactEnvelope<T = unknown> {
  schema_version: typeof ARTIFACT_ENVELOPE_SCHEMA_VERSION;
  artifact_id: string;
  kind: ArtifactKind;
  title: string;
  provenance: ArtifactProvenance;
  body: T;
}

export type ParseEnvelopeResult =
  | { ok: true; envelope: ArtifactEnvelope }
  | { ok: false; code: string; message: string };

export interface SynthesisIssue {
  code:
    | "basis_mismatch"
    | "time_window_mismatch"
    | "value_contradiction"
    | "truncated_source";
  artifact_ids: string[];
  message: string;
}

export interface SynthesisReport {
  artifact_ids: string[];
  consistent: boolean;
  issues: SynthesisIssue[];
}

const UNSAFE_HTML_RE =
  /<\s*script|javascript:|on(?:error|load|click)\s*=|<\s*iframe|<\s*svg/i;
const CONTROL_RE = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/;

function isSafeString(value: unknown, maxLen = 200): value is string {
  return (
    typeof value === "string" &&
    value.trim().length > 0 &&
    value.length <= maxLen &&
    !UNSAFE_HTML_RE.test(value) &&
    !CONTROL_RE.test(value)
  );
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseArtifactEnvelope(input: unknown): ParseEnvelopeResult {
  let encodedLen = 0;
  try {
    encodedLen = new TextEncoder().encode(JSON.stringify(input)).length;
  } catch {
    return { ok: false, code: "invalid_schema", message: "Artifact is not serializable JSON." };
  }
  if (encodedLen > MAX_ENVELOPE_BYTES) {
    return {
      ok: false,
      code: "too_large",
      message: `Artifact exceeds ${MAX_ENVELOPE_BYTES} byte limit.`,
    };
  }
  if (!isRecord(input)) {
    return { ok: false, code: "invalid_schema", message: "Artifact envelope must be an object." };
  }
  if (input.schema_version !== ARTIFACT_ENVELOPE_SCHEMA_VERSION) {
    return { ok: false, code: "invalid_schema", message: "Unsupported artifact schema_version." };
  }
  if (!isSafeString(input.artifact_id, 128) || !isSafeString(input.title, 200)) {
    return { ok: false, code: "unsafe_content", message: "Invalid or unsafe artifact header." };
  }
  const kind = input.kind;
  if (
    kind !== "table" &&
    kind !== "time_series" &&
    kind !== "segment_comparison" &&
    kind !== "map"
  ) {
    return { ok: false, code: "invalid_schema", message: "Unsupported artifact kind." };
  }

  const prov = input.provenance;
  if (
    !isRecord(prov) ||
    !isSafeString(prov.run_id, 128) ||
    !isSafeString(prov.agent_id, 128) ||
    !isSafeString(prov.tool_call_id, 128) ||
    !isSafeString(prov.tool_name, 128) ||
    !isSafeString(prov.query_id, 128) ||
    !isSafeString(prov.dataset, 128) ||
    !isSafeString(prov.time_window, 128) ||
    !isSafeString(prov.filter_basis, 200)
  ) {
    return { ok: false, code: "invalid_provenance", message: "Incomplete or unsafe provenance." };
  }

  const body = input.body;
  if (!isRecord(body)) {
    return { ok: false, code: "invalid_body", message: "Artifact body must be an object." };
  }

  if (kind === "table") {
    const columns = body.columns;
    const rows = body.rows;
    if (!Array.isArray(columns) || columns.length === 0 || !Array.isArray(rows)) {
      return { ok: false, code: "invalid_body", message: "Table requires columns and rows." };
    }
    for (const col of columns) {
      if (!isRecord(col) || !isSafeString(col.key, 64) || !isSafeString(col.label, 80)) {
        return { ok: false, code: "unsafe_content", message: "Invalid table column definition." };
      }
      if (col.unit != null && !isSafeString(col.unit, 32)) {
        return { ok: false, code: "unsafe_content", message: "Invalid table column unit." };
      }
    }
    for (const row of rows) {
      if (!Array.isArray(row) || row.length !== columns.length) {
        return { ok: false, code: "invalid_table_row", message: "Table row width mismatch." };
      }
      for (const cell of row) {
        if (typeof cell === "string" && (UNSAFE_HTML_RE.test(cell) || CONTROL_RE.test(cell))) {
          return { ok: false, code: "unsafe_content", message: "Unsafe markup in table cell." };
        }
      }
    }
  } else if (kind === "time_series") {
    if (
      !isSafeString(body.x_label, 80) ||
      !isSafeString(body.y_label, 80) ||
      !Array.isArray(body.points) ||
      body.points.length === 0 ||
      !isFiniteNumber(body.total_y)
    ) {
      return { ok: false, code: "invalid_body", message: "Invalid time_series body." };
    }
    let sum = 0;
    for (const pt of body.points) {
      if (!isRecord(pt) || !isSafeString(pt.x, 80) || !isFiniteNumber(pt.y)) {
        return { ok: false, code: "invalid_body", message: "Invalid time_series point." };
      }
      sum += pt.y;
    }
    const tol = Math.max(1e-4, Math.abs(sum) * 1e-4);
    if (Math.abs(sum - body.total_y) > tol) {
      return {
        ok: false,
        code: "sum_mismatch",
        message: `Time series total_y (${body.total_y}) does not match point sum (${sum}).`,
      };
    }
  } else if (kind === "segment_comparison") {
    if (
      !isSafeString(body.dimension, 80) ||
      !isSafeString(body.metric, 80) ||
      !isSafeString(body.baseline_label, 64) ||
      !isSafeString(body.comparison_label, 64) ||
      !Array.isArray(body.rows) ||
      body.rows.length === 0
    ) {
      return { ok: false, code: "invalid_body", message: "Invalid segment_comparison body." };
    }
    for (const row of body.rows) {
      if (
        !isRecord(row) ||
        !isSafeString(row.segment, 120) ||
        !isFiniteNumber(row.baseline) ||
        !isFiniteNumber(row.comparison) ||
        !isFiniteNumber(row.delta)
      ) {
        return { ok: false, code: "invalid_body", message: "Invalid segment_comparison row." };
      }
      const expected = row.comparison - row.baseline;
      const tol = Math.max(1e-4, Math.abs(expected) * 1e-4);
      if (Math.abs(expected - row.delta) > tol) {
        return {
          ok: false,
          code: "delta_mismatch",
          message: `Segment '${row.segment}' delta (${row.delta}) does not match comparison - baseline (${expected}).`,
        };
      }
    }
  } else if (kind === "map") {
    if (body.schema !== "map_artifact" || body.version !== 1) {
      return { ok: false, code: "invalid_body", message: "Map body must be map_artifact v1." };
    }
  }

  return { ok: true, envelope: input as unknown as ArtifactEnvelope };
}

export function synthesizeArtifacts(
  envelopes: ArtifactEnvelope[],
  tolerancePct = 0.01,
): SynthesisReport {
  const issues: SynthesisIssue[] = [];
  if (envelopes.length === 0) {
    return { artifact_ids: [], consistent: true, issues: [] };
  }

  const bases = new Set(envelopes.map((e) => e.provenance.filter_basis));
  if (bases.size > 1) {
    issues.push({
      code: "basis_mismatch",
      artifact_ids: envelopes.map((e) => e.artifact_id),
      message: `Artifacts use incompatible filter bases: ${[...bases].sort().join(", ")}`,
    });
  }

  const windows = new Set(envelopes.map((e) => e.provenance.time_window));
  if (windows.size > 1) {
    issues.push({
      code: "time_window_mismatch",
      artifact_ids: envelopes.map((e) => e.artifact_id),
      message: `Artifacts span different time windows: ${[...windows].sort().join(", ")}`,
    });
  }

  for (const env of envelopes) {
    const bodyTrunc =
      env.kind === "table" && Boolean((env.body as TableBody).truncated);
    if (env.provenance.truncated || bodyTrunc) {
      issues.push({
        code: "truncated_source",
        artifact_ids: [env.artifact_id],
        message: `Artifact '${env.title}' (${env.artifact_id}) is based on truncated rows.`,
      });
    }
  }

  // Detect contradictions for the same (metric_label, key) across artifacts.
  const observed = new Map<string, Array<{ artifactId: string; agentId: string; value: number }>>();
  const record = (metric: string, key: string, env: ArtifactEnvelope, value: number) => {
    const k = `${metric.toLowerCase()}::${key.toLowerCase()}`;
    const list = observed.get(k) ?? [];
    list.push({ artifactId: env.artifact_id, agentId: env.provenance.agent_id, value });
    observed.set(k, list);
  };

  for (const env of envelopes) {
    if (env.kind === "time_series") {
      const ts = env.body as TimeSeriesBody;
      record(ts.y_label, "__total__", env, ts.total_y);
      for (const p of ts.points) record(ts.y_label, p.x, env, p.y);
    } else if (env.kind === "segment_comparison") {
      const sc = env.body as SegmentComparisonBody;
      for (const r of sc.rows) record(sc.metric, `${r.segment}:baseline`, env, r.baseline);
    } else if (env.kind === "table") {
      const tb = env.body as TableBody;
      if (tb.columns.length >= 2) {
        const metricKey = tb.columns[1].key;
        for (const row of tb.rows) {
          if (typeof row[0] === "string" && isFiniteNumber(row[1])) {
            record(metricKey, row[0], env, row[1]);
          }
        }
      }
    }
  }

  for (const [compoundKey, entries] of observed.entries()) {
    if (entries.length < 2) continue;
    const base = entries[0].value;
    const scale = Math.max(Math.abs(base), 1e-9);
    for (const other of entries.slice(1)) {
      if (Math.abs(other.value - base) / scale > tolerancePct) {
        const [metric, label] = compoundKey.split("::");
        issues.push({
          code: "value_contradiction",
          artifact_ids: [entries[0].artifactId, other.artifactId],
          message: `Contradiction for ${metric}[${label}]: ${entries[0].agentId}=${base} vs ${other.agentId}=${other.value}`,
        });
        break;
      }
    }
  }

  return {
    artifact_ids: envelopes.map((e) => e.artifact_id),
    consistent: issues.length === 0,
    issues,
  };
}
