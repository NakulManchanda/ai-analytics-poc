export type Status = {
  app: { status: string; service: string };
  mcp: { status: string; tools?: number; resources?: number };
};

export type LLMCallMetadata = {
  llm_call_id: string;
  model_id: string;
  usage: { input_tokens: number; output_tokens: number; total_tokens: number };
  latency_ms: number;
};

export type AskResponse = {
  answer: string;
  tool_call_id?: string;
  query_id?: string;
  llm_calls?: LLMCallMetadata[];
  usage: { input_tokens: number; output_tokens: number; total_tokens: number };
  latency_ms: number;
  conversation_id?: string;
  run_id?: string;
};

export type WorkingContextData = {
  conversation_summary?: string | null;
  current_user_message?: string;
  recent_messages?: Array<{
    message_id: string;
    role: string;
    content: string;
    sequence: number;
  }>;
  available_tools?: string[];
  dataset_schema?: {
    dataset?: string;
    columns?: string[];
    [key: string]: unknown;
  };
  recent_tool_observations?: Array<{
    query_id: string;
    columns?: string[];
    row_count?: number;
    preview_rows?: unknown[][];
    artifact_ref?: string;
    execution_duration_ms?: number;
  }>;
  assumptions?: string[];
  artifacts?: string[];
  failures?: string[];
  remaining_budget?: {
    current_iteration: number;
    max_iterations: number;
    remaining_iterations: number;
    remaining_tool_calls: number;
    remaining_llm_calls: number;
    remaining_input_tokens: number;
    remaining_estimated_cost_usd: number;
    max_tool_calls: number;
    max_llm_calls: number;
    max_input_tokens: number;
    max_estimated_cost_usd: number;
  };
  stored_message_count: number;
  included_message_count: number;
  schema_size_bytes: number;
};

export type RunEvent = {
  event_id: string;
  event_type: string;
  run_id: string;
  conversation_id: string;
  sequence: number;
  step_id?: string | null;
  llm_call_id?: string | null;
  tool_call_id?: string | null;
  query_id?: string | null;
  timestamp: string;
  payload: {
    status?: string;
    prompt_summary?: string;
    resource?: string;
    llm_call_id?: string;
    phase?: string;
    latency_ms?: number;
    tokens?: { input: number; output: number };
    tool_name?: string;
    analysis?: string;
    limit?: number;
    query_id?: string;
    row_count?: number;
    duration_ms?: number;
    working_context?: WorkingContextData;
    total_tokens?: number;
    estimated_cost_usd?: number;
    failure_code?: string;
    reason?: string;
    error?: string;
    input_summary?: string;
    output_summary?: string;
    [key: string]: unknown;
  };
};

export type ChatTurn = {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: string;
  runId?: string;
  tokens?: number;
  latencyMs?: number;
};
