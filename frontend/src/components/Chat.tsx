import { useEffect, useRef, useState } from "react";
import { ApiError, api, streamSSE } from "../api";
import type { AiTool, ChatEvent, ChatMessage } from "../types";

type Entry =
  | { kind: "msg"; role: "user" | "assistant"; text: string }
  | { kind: "tool"; text: string };

export function Chat() {
  const [available, setAvailable] = useState<boolean | null>(null);
  const [model, setModel] = useState<string>("");
  const [toolNames, setToolNames] = useState<string[]>([]);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api
      .get<{ available: boolean; model: string; tools: AiTool[] }>("/ai/tools")
      .then((r) => {
        setAvailable(r.available);
        setModel(r.model);
        setToolNames(r.tools.map((t) => t.name));
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [entries]);

  // The conversation history we send to the API (user + assistant text turns only).
  const historyRef = useRef<ChatMessage[]>([]);

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setError(null);
    setBusy(true);
    historyRef.current = [...historyRef.current, { role: "user", content: text }];
    setEntries((es) => [...es, { kind: "msg", role: "user", text }, { kind: "msg", role: "assistant", text: "" }]);

    let assistantText = "";
    const appendAssistant = (chunk: string) => {
      assistantText += chunk;
      setEntries((es) => {
        const copy = es.slice();
        for (let i = copy.length - 1; i >= 0; i--) {
          if (copy[i].kind === "msg" && (copy[i] as any).role === "assistant") {
            copy[i] = { kind: "msg", role: "assistant", text: assistantText };
            break;
          }
        }
        return copy;
      });
    };
    const addTool = (line: string) => setEntries((es) => [...es, { kind: "tool", text: line }]);

    try {
      await streamSSE("/ai/chat", { messages: historyRef.current }, (raw) => {
        const ev = raw as ChatEvent;
        if (ev.type === "token") appendAssistant(ev.text ?? "");
        else if (ev.type === "tool_call") addTool(`▸ ${ev.name}(${ev.summary ?? ""})`);
        else if (ev.type === "tool_result") addTool(`${ev.ok ? "✓" : "✗"} ${ev.name} → ${ev.summary ?? ""}`);
        else if (ev.type === "error") {
          setError(ev.message ?? "error");
          appendAssistant(assistantText ? "" : "(no response)");
        }
        // "thinking" / "done" — ignore for display
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      historyRef.current = [...historyRef.current, { role: "assistant", content: assistantText }];
      setBusy(false);
    }
  }

  function reset() {
    historyRef.current = [];
    setEntries([]);
    setError(null);
  }

  return (
    <div className="stack">
      <h2 style={{ marginBottom: 0 }}>Assistant</h2>
      <p className="muted" style={{ marginTop: 4 }}>
        {available === null ? "…" : available ? <>Model: <code>{model}</code> · tools: {toolNames.join(", ") || "none"}</> : <span className="err">The assistant is not configured (no ANTHROPIC_API_KEY on the server).</span>}
        {entries.length > 0 && (
          <>
            {" · "}
            <button className="linkish" onClick={reset}>
              new conversation
            </button>
          </>
        )}
      </p>

      <div className="chat">
        <div className="msgs" ref={scrollRef}>
          {entries.length === 0 && <div className="muted">Ask about your connectors and data — e.g. “list the connectors, then run liberty.ping”.</div>}
          {entries.map((e, i) =>
            e.kind === "tool" ? (
              <div key={i} className="tool mono">
                {e.text}
              </div>
            ) : (
              <div key={i} className={`msg ${e.role}`}>
                {e.text || <span className="muted">…</span>}
              </div>
            ),
          )}
        </div>
        {error && <div className="err">{error}</div>}
        <form
          onSubmit={(ev) => {
            ev.preventDefault();
            void send();
          }}
        >
          <textarea
            value={input}
            onChange={(ev) => setInput(ev.target.value)}
            onKeyDown={(ev) => {
              if (ev.key === "Enter" && !ev.shiftKey) {
                ev.preventDefault();
                void send();
              }
            }}
            placeholder={available ? "Type a message… (Enter to send, Shift+Enter for newline)" : "Assistant unavailable"}
            disabled={!available || busy}
          />
          <button className="btn" type="submit" disabled={!available || busy || !input.trim()}>
            {busy ? "…" : "Send"}
          </button>
        </form>
      </div>
    </div>
  );
}
