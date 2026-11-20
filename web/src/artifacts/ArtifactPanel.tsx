import { ZoneMap } from "../map/ZoneMap";
import {
  parseArtifactEnvelope,
  type ArtifactEnvelope,
  type SegmentComparisonBody,
  type SynthesisReport,
  type TableBody,
  type TimeSeriesBody,
} from "./envelope";

const fmt = (n: number) => n.toLocaleString("en-US", { maximumFractionDigits: 2 });

interface ArtifactPanelProps {
  rawEnvelope: unknown;
  interactiveMap?: boolean;
  onAskAboutZone?: (zoneName: string) => void;
}

export function ArtifactPanel({
  rawEnvelope,
  interactiveMap = true,
  onAskAboutZone,
}: ArtifactPanelProps) {
  const parsed = parseArtifactEnvelope(rawEnvelope);
  if (!parsed.ok) {
    return (
      <section className="artifact-card artifact-card-error" role="alert">
        <h4>Artifact rejected</h4>
        <p>
          Unable to render artifact ({parsed.code}): {parsed.message}
        </p>
      </section>
    );
  }

  const env = parsed.envelope;
  return (
    <section
      className="artifact-card"
      data-artifact-id={env.artifact_id}
      data-artifact-kind={env.kind}
      aria-label={env.title}
    >
      <header className="artifact-header">
        <div>
          <h4>{env.title}</h4>
          <p className="artifact-provenance">
            <span>Agent: {env.provenance.agent_id}</span>
            {" · "}
            <span>Dataset: {env.provenance.dataset}</span>
            {" · "}
            <span>Window: {env.provenance.time_window}</span>
            {" · "}
            <span>Basis: {env.provenance.filter_basis}</span>
            {" · "}
            <code>{env.provenance.query_id}</code>
          </p>
        </div>
        {env.provenance.truncated && (
          <span className="artifact-badge artifact-badge-warn" role="status">
            Truncated source
          </span>
        )}
      </header>

      {env.kind === "table" && <TableView body={env.body as TableBody} />}
      {env.kind === "time_series" && <TimeSeriesView body={env.body as TimeSeriesBody} />}
      {env.kind === "segment_comparison" && (
        <SegmentComparisonView body={env.body as SegmentComparisonBody} />
      )}
      {env.kind === "map" && (
        <ZoneMap
          artifact={env.body}
          interactive={interactiveMap}
          onAskAboutZone={onAskAboutZone}
        />
      )}
    </section>
  );
}

function TableView({ body }: { body: TableBody }) {
  return (
    <div className="artifact-table-wrap">
      <table>
        <thead>
          <tr>
            {body.columns.map((col) => (
              <th key={col.key} scope="col">
                {col.label}
                {col.unit ? ` (${col.unit})` : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.rows.map((row, rIdx) => (
            <tr key={rIdx}>
              {row.map((cell, cIdx) => (
                <td key={cIdx}>
                  {typeof cell === "number" ? fmt(cell) : cell === null ? "—" : String(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {body.truncated && (
        <p className="artifact-note" role="status">
          Showing {body.rows.length} of {body.row_count} rows (truncated).
        </p>
      )}
    </div>
  );
}

function TimeSeriesView({ body }: { body: TimeSeriesBody }) {
  const maxVal = Math.max(...body.points.map((p) => p.y), 1);
  const width = 420;
  const height = 140;
  const padX = 24;
  const padY = 18;
  const barWidth = Math.max(12, Math.floor((width - padX * 2) / body.points.length) - 6);

  return (
    <div className="artifact-ts">
      <svg
        role="img"
        aria-label={`${body.y_label} by ${body.x_label}`}
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        height={height}
      >
        {body.points.map((pt, idx) => {
          const x = padX + idx * ((width - padX * 2) / body.points.length) + 3;
          const barH = Math.max(2, ((height - padY * 2) * pt.y) / maxVal);
          const y = height - padY - barH;
          return (
            <g key={pt.x}>
              <rect x={x} y={y} width={barWidth} height={barH} rx={3} fill="#2563eb" />
              <text x={x + barWidth / 2} y={height - 4} fontSize="10" textAnchor="middle">
                {pt.x}
              </text>
            </g>
          );
        })}
      </svg>
      <p className="artifact-summary">
        Total {body.y_label}: <strong>{fmt(body.total_y)}</strong>
        {body.unit ? ` ${body.unit}` : ""} across {body.points.length} buckets
      </p>
    </div>
  );
}

function SegmentComparisonView({ body }: { body: SegmentComparisonBody }) {
  return (
    <div className="artifact-table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col">{body.dimension}</th>
            <th scope="col">
              {body.baseline_label}
              {body.unit ? ` (${body.unit})` : ""}
            </th>
            <th scope="col">
              {body.comparison_label}
              {body.unit ? ` (${body.unit})` : ""}
            </th>
            <th scope="col">Delta</th>
          </tr>
        </thead>
        <tbody>
          {body.rows.map((row) => (
            <tr key={row.segment}>
              <th scope="row">{row.segment}</th>
              <td>{fmt(row.baseline)}</td>
              <td>{fmt(row.comparison)}</td>
              <td>
                {row.delta >= 0 ? "+" : ""}
                {fmt(row.delta)}
                {row.delta_pct != null ? ` (${row.delta_pct >= 0 ? "+" : ""}${row.delta_pct}%)` : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SynthesisBanner({ report }: { report: SynthesisReport }) {
  if (report.consistent) {
    return (
      <div className="synthesis-banner synthesis-ok" role="status">
        Cross-agent synthesis check passed ({report.artifact_ids.length} artifacts aligned on
        filter basis, time window, and shared totals).
      </div>
    );
  }
  return (
    <div className="synthesis-banner synthesis-warn" role="alert">
      <strong>Cross-agent synthesis discrepancies detected:</strong>
      <ul>
        {report.issues.map((issue, idx) => (
          <li key={`${issue.code}-${idx}`}>
            <code>{issue.code}</code>: {issue.message}
          </li>
        ))}
      </ul>
    </div>
  );
}

export type { ArtifactEnvelope };
