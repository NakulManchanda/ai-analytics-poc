import {
  memo,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
  type PointerEvent,
} from "react";

import { parseMapArtifact, type Choropleth, type Highlight, type MapArtifact, type Region, type Unmapped } from "./artifact";
import { boundsView, loadGeometry, type Geometry, type Projected, projectShapes } from "./geometry";

const STEPS = 5;
const DISCLAIMER = "Shows where values are high or low; it does not show why.";

/** Incremented whenever the 263-path layer renders; tests assert it stays put on hover and selection. */
export const pathLayerStats = { renders: 0 };

function step(value: number, min: number, max: number): number {
  if (max <= min) return Math.floor(STEPS / 2);
  return Math.min(STEPS - 1, Math.max(0, Math.floor(((value - min) / (max - min)) * STEPS)));
}

const fmt = (n: number) => n.toLocaleString("en-US", { maximumFractionDigits: 2 });

function fitView(artifact: MapArtifact, projected: Projected): string {
  const fit = artifact.view.fit;
  if (fit === "all") return projected.allView;
  return boundsView(projected.shapes.filter((s) => fit.location_ids.includes(s.id)));
}

function zoneIdFrom(target: EventTarget | null): number | null {
  const el = (target as Element | null)?.closest?.("[data-location-id]");
  if (!el) return null;
  const id = Number(el.getAttribute("data-location-id"));
  return Number.isInteger(id) ? id : null;
}

function Notice({ children }: { children: string }) {
  return (
    <p className="zm-notice" role="alert">
      {children}
    </p>
  );
}

interface PathModel {
  id: number;
  d: string;
  cls: string;
  title: string;
}

// Rendered once per artifact: hover and selection live outside this layer.
const PathLayer = memo(function PathLayer({ paths }: { paths: PathModel[] }) {
  pathLayerStats.renders += 1;
  return (
    <>
      {paths.map((p) => (
        <path key={p.id} data-location-id={p.id} className={p.cls} d={p.d} fillRule="evenodd">
          <title>{p.title}</title>
        </path>
      ))}
    </>
  );
});

interface TableProps {
  regions: Region[];
  unmapped: Unmapped[];
  measure: string;
  unit: string;
  selectedId: number | null;
  onSelect?: (id: number) => void;
}

const ZoneTable = memo(function ZoneTable({ regions, unmapped, measure, unit, selectedId, onSelect }: TableProps) {
  return (
    <table className="zm-table">
      <caption>{`${measure} by zone`}</caption>
      <thead>
        <tr>
          <th scope="col">Zone</th>
          <th scope="col">{`Value (${unit})`}</th>
          <th scope="col">On map</th>
        </tr>
      </thead>
      <tbody>
        {regions.map((r) =>
          onSelect ? (
            <tr
              key={r.location_ids.join(",")}
              aria-selected={selectedId !== null && r.location_ids.includes(selectedId)}
              className={selectedId !== null && r.location_ids.includes(selectedId) ? "zm-row-selected" : undefined}
            >
              <th scope="row">
                <button type="button" className="zm-row-btn" onClick={() => onSelect(r.location_ids[0])}>
                  {r.label}
                </button>
              </th>
              <td>{fmt(r.value)}</td>
              <td>Yes</td>
            </tr>
          ) : (
            <tr key={r.location_ids.join(",")}>
              <th scope="row">{r.label}</th>
              <td>{fmt(r.value)}</td>
              <td>Yes</td>
            </tr>
          ),
        )}
        {unmapped.map((u, i) => (
          <tr key={`u${i}`}>
            <th scope="row">{u.label}</th>
            <td>{fmt(u.value)}</td>
            <td>{`No (${u.reason === "no_geometry" ? "no geometry" : "unknown zone"})`}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
});

interface ZoneMapProps {
  artifact: unknown;
  /** Adds hover, selection, borough views and a keyboard-operable table. Off for the static fixture demo. */
  interactive?: boolean;
  /** Called with the zone name from the geometry index, never from artifact text. */
  onAskAboutZone?: (zoneName: string) => void;
}

export function ZoneMap({ artifact, interactive = false, onAskAboutZone }: ZoneMapProps) {
  const [geometry, setGeometry] = useState<Geometry | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    loadGeometry().then(
      (g) => live && setGeometry(g),
      () => live && setFailed(true),
    );
    return () => {
      live = false;
    };
  }, []);

  // Projection runs once per geometry load; parsing once per artifact. Neither runs on hover or selection.
  const projected = useMemo(() => (geometry ? projectShapes(geometry) : null), [geometry]);
  const parsed = useMemo(
    () => (projected ? parseMapArtifact(artifact, new Set(projected.shapes.map((s) => s.id))) : null),
    [projected, artifact],
  );

  if (failed) return <Notice>Map could not be shown.</Notice>;
  if (!projected || !parsed) return <p className="zm-loading">Loading map…</p>;
  if (!parsed.ok) return <Notice>Map could not be shown.</Notice>;
  return (
    <LoadedMap
      key={parsed.artifact.artifact_id}
      artifact={parsed.artifact}
      projected={projected}
      interactive={interactive}
      onAskAboutZone={onAskAboutZone}
    />
  );
}

interface LoadedProps {
  artifact: MapArtifact;
  projected: Projected;
  interactive: boolean;
  onAskAboutZone?: (zoneName: string) => void;
}

function LoadedMap({ artifact: a, projected, interactive, onAskAboutZone }: LoadedProps) {
  const uid = useId();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [viewChoice, setViewChoice] = useState<string | null>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const tableWrapRef = useRef<HTMLDivElement>(null);
  const hoverId = useRef<number | null>(null);

  const choropleth = a.layers.find((l): l is Choropleth => l.kind === "choropleth")!;
  const highlight = a.layers.find((l): l is Highlight => l.kind === "highlight");
  const { palette, min, max } = choropleth.scale;
  const unit = choropleth.unit;

  const model = useMemo(() => {
    const regionOf = new Map<number, Region>();
    for (const r of choropleth.regions) for (const id of r.location_ids) regionOf.set(id, r);
    const highlighted = new Set(highlight?.location_ids ?? []);
    // Highlighted zones are drawn last so their stroke is not covered by neighbours.
    const ordered = [...projected.shapes].sort((x, y) => Number(highlighted.has(x.id)) - Number(highlighted.has(y.id)));
    const paths: PathModel[] = ordered.map((s) => {
      const region = regionOf.get(s.id);
      const cls = ["zm-zone"];
      if (region) cls.push(`zm-${palette}-${step(region.value, min, max)}`);
      if (highlighted.has(s.id)) cls.push("zm-highlight");
      return {
        id: s.id,
        d: s.d,
        cls: cls.join(" "),
        title: region ? `${region.label}: ${fmt(region.value)} ${unit}` : `${s.zone}: no data`,
      };
    });
    const sorted = [...choropleth.regions].sort((x, y) => y.value - x.value);
    const rank = new Map<number, number>();
    let prevRank = 1;
    sorted.forEach((r, i) => {
      const rRank = i > 0 && r.value === sorted[i - 1].value ? prevRank : i + 1;
      prevRank = rRank;
      for (const id of r.location_ids) rank.set(id, rRank);
    });
    return { regionOf, paths, rank, initialView: fitView(a, projected) };
  }, [a, projected, choropleth, highlight, palette, min, max, unit]);

  const select = useCallback((id: number) => setSelectedId((cur) => (cur === id ? null : id)), []);

  useEffect(() => {
    if (selectedId === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelectedId(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [selectedId]);

  // Keep the selected table row visible without scrolling the page.
  useEffect(() => {
    const wrap = tableWrapRef.current;
    const row = wrap?.querySelector<HTMLElement>('tr[aria-selected="true"]');
    if (!wrap || !row) return;
    if (row.offsetTop < wrap.scrollTop || row.offsetTop + row.offsetHeight > wrap.scrollTop + wrap.clientHeight) {
      wrap.scrollTop = Math.max(0, row.offsetTop - wrap.clientHeight / 2);
    }
  }, [selectedId]);

  const onSvgClick = (e: MouseEvent<SVGSVGElement>) => {
    const id = zoneIdFrom(e.target);
    if (id !== null) select(id);
  };

  // Hover updates the tooltip element directly; no React state, so nothing re-renders.
  const onSvgMove = (e: PointerEvent<SVGSVGElement>) => {
    const tip = tipRef.current;
    const wrap = wrapRef.current;
    if (!tip || !wrap) return;
    const id = zoneIdFrom(e.target);
    const shape = id === null ? undefined : projected.byId.get(id);
    if (!shape) {
      tip.hidden = true;
      hoverId.current = null;
      return;
    }
    if (hoverId.current !== shape.id) {
      hoverId.current = shape.id;
      const region = model.regionOf.get(shape.id);
      tip.textContent = `${shape.zone} · ${shape.borough} · ${region ? `${fmt(region.value)} ${unit}` : "no data"}`;
    }
    tip.hidden = false;
    const r = wrap.getBoundingClientRect();
    const x = e.clientX - r.left;
    const y = e.clientY - r.top;
    tip.style.transform = `translate(${x > r.width - 240 ? x - 232 : x + 12}px, ${y + 12}px)`;
  };
  const onSvgLeave = () => {
    if (tipRef.current) tipRef.current.hidden = true;
    hoverId.current = null;
  };

  const titleId = `${uid}-title`;
  const descId = `${uid}-desc`;
  const filters = Object.entries(a.filters);
  const selected = selectedId === null ? undefined : projected.byId.get(selectedId);
  const selectedRegion = selectedId === null ? undefined : model.regionOf.get(selectedId);
  const viewBox =
    viewChoice === null ? model.initialView : viewChoice === "All" ? projected.allView : (projected.boroughViews[viewChoice] ?? model.initialView);
  const activeView = viewChoice ?? (a.view.fit === "all" ? "All" : null);
  const provenance = [
    `Source: ${a.provenance.source}`,
    `dataset ${a.evidence.dataset}`,
    a.evidence.run_id !== null && `run ${a.evidence.run_id}`,
    a.evidence.tool_call_id !== null && `tool call ${a.evidence.tool_call_id}`,
    `query ${a.evidence.query_id}`,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <figure className="zone-map">
      {interactive && (
        <div className="zm-views" role="group" aria-label="Map view">
          {["All", ...Object.keys(projected.boroughViews)].map((name) => (
            <button
              key={name}
              type="button"
              className="zm-view-btn"
              aria-pressed={activeView === name}
              onClick={() => setViewChoice(name)}
            >
              {name}
            </button>
          ))}
        </div>
      )}
      <div className="zm-map-wrap" ref={wrapRef}>
        <svg
          role="img"
          aria-labelledby={`${titleId} ${descId}`}
          viewBox={viewBox}
          className="zm-svg"
          onClick={interactive ? onSvgClick : undefined}
          onPointerMove={interactive ? onSvgMove : undefined}
          onPointerLeave={interactive ? onSvgLeave : undefined}
        >
          <title id={titleId}>{a.title}</title>
          <desc id={descId}>
            {`Choropleth of ${choropleth.measure} in ${unit} across ${choropleth.regions.length} regions.`}
          </desc>
          <PathLayer paths={model.paths} />
          {selected && <path className="zm-selected" d={selected.d} fillRule="evenodd" aria-hidden="true" />}
        </svg>
        {interactive && <div ref={tipRef} className="zm-tooltip" role="tooltip" hidden />}
      </div>
      <figcaption>
        {interactive && (
          <div className="zm-detail" aria-label="Selected zone" aria-live="polite">
            {selected ? (
              <>
                <h4 className="zm-detail-title">{selected.zone}</h4>
                <dl>
                  <div><dt>Borough</dt><dd>{selected.borough}</dd></div>
                  <div>
                    <dt>{choropleth.measure}</dt>
                    <dd>{selectedRegion ? `${fmt(selectedRegion.value)} ${unit}` : "No data for this zone"}</dd>
                  </div>
                  {selectedRegion && selectedId !== null && (
                    <div>
                      <dt>Rank</dt>
                      <dd>{`${model.rank.get(selectedId)} of ${choropleth.regions.length}`}</dd>
                    </div>
                  )}
                </dl>
                <div className="zm-detail-actions">
                  {onAskAboutZone && (
                    <button type="button" onClick={() => onAskAboutZone(selected.zone)}>
                      Ask about this zone
                    </button>
                  )}
                  <button type="button" className="btn-text" onClick={() => setSelectedId(null)}>
                    Clear selection
                  </button>
                </div>
              </>
            ) : (
              <p className="zm-detail-empty">Hover a zone for its value, or select one from the map or table. Escape clears.</p>
            )}
          </div>
        )}
        <h3 className="zm-title">{a.title}</h3>
        <div className="zm-legend" aria-label="Legend">
          <span>{`${fmt(min)} ${unit}`}</span>
          <span className="zm-swatches" aria-hidden="true">
            {Array.from({ length: STEPS }, (_, i) => (
              <span key={i} className={`zm-swatch zm-${palette}-${i}`} />
            ))}
          </span>
          <span>{`${fmt(max)} ${unit}`}</span>
          {highlight && <span className="zm-legend-highlight">{`Outlined: ${highlight.label}`}</span>}
        </div>
        <p className="zm-filters">
          {filters.length
            ? `Filters: ${filters.map(([k, v]) => `${k} = ${String(v)}`).join(", ")}`
            : "Filters: none"}
        </p>
        <p className="zm-provenance">{provenance}</p>
        <p className="zm-disclaimer">{DISCLAIMER}</p>
        {interactive ? (
          <div className="zm-table-wrap" ref={tableWrapRef}>
            <ZoneTable
              regions={choropleth.regions}
              unmapped={a.unmapped}
              measure={choropleth.measure}
              unit={unit}
              selectedId={selectedId}
              onSelect={select}
            />
          </div>
        ) : (
          <ZoneTable
            regions={choropleth.regions}
            unmapped={a.unmapped}
            measure={choropleth.measure}
            unit={unit}
            selectedId={null}
          />
        )}
      </figcaption>
    </figure>
  );
}
