import { useEffect, useState } from "react";

type Status = {
  app: { status: string; service: string };
  mcp: { status: string; tools?: number; resources?: number };
};

const initialStatus: Status = {
  app: { status: "checking", service: "ai-app" },
  mcp: { status: "checking" },
};

function appLabel(status: Status): string {
  return status.app.status === "ok" ? "Backend ready" : "Backend checking";
}

function mcpLabel(status: Status): string {
  if (status.mcp.status === "ok") {
    return `MCP discovered · ${status.mcp.tools} tools · ${status.mcp.resources} resources`;
  }
  return status.mcp.status === "unavailable" ? "MCP unavailable" : "MCP checking";
}

export default function App() {
  const [status, setStatus] = useState<Status>(initialStatus);

  useEffect(() => {
    let active = true;

    fetch("/api/status")
      .then((response) => {
        if (!response.ok) {
          throw new Error("Status request failed");
        }
        return response.json() as Promise<Status>;
      })
      .then((nextStatus) => {
        if (active) {
          setStatus(nextStatus);
        }
      })
      .catch(() => {
        if (active) {
          setStatus({
            app: { status: "unavailable", service: "ai-app" },
            mcp: { status: "unavailable" },
          });
        }
      });

    return () => {
      active = false;
    };
  }, []);

  return (
    <main className="shell">
      <header className="masthead">
        <p className="eyebrow">NYC TLC · milestone 3</p>
        <h1>Taxi analytics control room</h1>
        <p className="lede">A browser shell for checking the analytics path before AI enters it.</p>
      </header>

      <section className="status-board" aria-label="Service status">
        <article className={`status-card ${status.app.status}`}>
          <p className="card-label">Application</p>
          <strong>{appLabel(status)}</strong>
          <span>FastAPI · {status.app.service}</span>
        </article>
        <article className={`status-card ${status.mcp.status}`}>
          <p className="card-label">Analytical boundary</p>
          <strong>{mcpLabel(status)}</strong>
          <span>FastMCP discovery happens through FastAPI</span>
        </article>
      </section>

      <section className="workspace" aria-label="Analytics workspace">
        <form className="prompt-panel">
          <label htmlFor="prompt">Ask about NYC taxi activity</label>
          <textarea
            id="prompt"
            disabled
            placeholder="Prompt execution arrives in a later milestone."
            rows={4}
          />
          <p>This placeholder deliberately does not send data or invoke an LLM.</p>
        </form>

        <aside className="timeline" aria-labelledby="timeline-heading">
          <div>
            <p className="card-label">Run timeline</p>
            <h2 id="timeline-heading">Waiting for a future run</h2>
          </div>
          <ol>
            <li>Browser shell loaded</li>
            <li>Backend status requested</li>
            <li>MCP discovery displayed</li>
          </ol>
        </aside>
      </section>
    </main>
  );
}
