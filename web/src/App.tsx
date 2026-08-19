import { FormEvent, useEffect, useState } from "react";

type Status = {
  app: { status: string; service: string };
  mcp: { status: string; tools?: number; resources?: number };
};

type AskResponse = {
  answer: string;
  usage: { input_tokens: number; output_tokens: number; total_tokens: number };
  latency_ms: number;
};

const initialStatus: Status = {
  app: { status: "checking", service: "ai-app" },
  mcp: { status: "checking" },
};

const STATUS_RETRY_DELAY_MS = 500;
const MAX_STATUS_ATTEMPTS = 10;

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
  const [prompt, setPrompt] = useState("");
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [promptError, setPromptError] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(false);

  useEffect(() => {
    let active = true;
    let retryTimer: number | undefined;

    const loadStatus = async (attempt: number) => {
      try {
        const response = await fetch("/api/status");
        if (!response.ok) {
          throw new Error("Status request failed");
        }
        const nextStatus = (await response.json()) as Status;
        if (active) {
          setStatus(nextStatus);
        }
      } catch {
        if (attempt + 1 < MAX_STATUS_ATTEMPTS && active) {
          retryTimer = window.setTimeout(() => {
            void loadStatus(attempt + 1);
          }, STATUS_RETRY_DELAY_MS);
        } else if (active) {
          setStatus({
            app: { status: "unavailable", service: "ai-app" },
            mcp: { status: "unavailable" },
          });
        }
      }
    };

    void loadStatus(0);

    return () => {
      active = false;
      window.clearTimeout(retryTimer);
    };
  }, []);

  const submitPrompt = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!prompt.trim() || isRunning) return;

    setIsRunning(true);
    setAnswer(null);
    setPromptError(null);
    try {
      const response = await fetch("/api/ask", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ prompt }),
      });
      const payload = (await response.json()) as AskResponse | { detail?: { code?: string; retryable?: boolean } };
      if (!response.ok) {
        const detail = "detail" in payload ? payload.detail : undefined;
        if (detail?.code === "mcp_tool_error" && detail.retryable) {
          throw new Error("The query service is temporarily unavailable. Try again.");
        }
        throw new Error("The analytics request could not be completed. Try again.");
      }
      setAnswer(payload as AskResponse);
    } catch (error) {
      setPromptError(error instanceof Error ? error.message : "The analytics request could not be completed. Try again.");
    } finally {
      setIsRunning(false);
    }
  };

  return (
    <main className="shell">
      <header className="masthead">
        <p className="eyebrow">NYC TLC · milestone 6</p>
        <h1>Taxi analytics control room</h1>
        <p className="lede">Ask one bounded question. The application queries the governed dataset before it answers.</p>
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
        <form className="prompt-panel" onSubmit={submitPrompt}>
          <label htmlFor="prompt">Ask about NYC taxi activity</label>
          <textarea
            id="prompt"
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            disabled={isRunning}
            placeholder="Which pickup zones have the most trips?"
            rows={4}
          />
          <button type="submit" disabled={!prompt.trim() || isRunning}>
            {isRunning ? "Running analysis…" : "Run analysis"}
          </button>
          <p>This run uses one governed query tool and two bounded model calls.</p>
          <div aria-live="polite" className="run-result">
            {isRunning && <p>Calling the governed query tool…</p>}
            {promptError && <p className="prompt-error">{promptError}</p>}
            {answer && (
              <>
                <p className="answer">{answer.answer}</p>
                <p className="usage">{answer.usage.total_tokens} tokens · {answer.latency_ms} ms</p>
              </>
            )}
          </div>
        </form>

        <aside className="timeline" aria-labelledby="timeline-heading">
          <div>
            <p className="card-label">Run timeline</p>
            <h2 id="timeline-heading">Governed query run</h2>
          </div>
          <ol>
            <li>First model call proposes the query</li>
            <li>FastMCP executes the governed DuckDB query</li>
            <li>Second model call returns the answer</li>
          </ol>
        </aside>
      </section>
    </main>
  );
}
