import fixture from "./fixtures/pickup-count-choropleth.v1.json";
import { ZoneMap } from "./ZoneMap";

export function MapDemo() {
  return (
    <main className="shell">
      <h1>Fixture demo, not live data</h1>
      <ZoneMap artifact={fixture} />
    </main>
  );
}
