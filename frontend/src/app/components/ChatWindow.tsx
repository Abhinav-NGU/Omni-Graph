"use client";
import { useState, useRef, useEffect, useCallback } from "react";
import { Send, Upload, Trash2, RotateCcw, ChevronUp, ChevronDown, Loader2, GitBranch, KeyRound } from "lucide-react";
import { Message, Session } from "../lib/types";
import MessageBubble from "./MessageBubble";
import SessionSidebar from "./SessionSidebar";
import IngestModal from "./IngestModal";
import GraphExplorerModal from "./GraphExplorerModal";
import HealthBar from "./HealthBar";

interface Props { /* apiKey is now managed internally */ }

interface StoredApiKey {
  key: string;
  expires: number; // Expiry timestamp
}

const API_KEY_STORAGE_KEY = "omnigraph-api-key";
const API_KEY_EXPIRY_MS = 5 * 60 * 60 * 1000; // 5 hours

function generateTopic(question: string): string {
  const cleaned = question.replace(/[?!.,]/g, "").trim();
  return cleaned.length > 40 ? cleaned.slice(0, 40) + "…" : cleaned;
}

export default function ChatWindow({}: Props) {
  const [apiKey, setApiKey] = useState<string | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messagesBySession, setMessagesBySession] = useState<Record<string, Message[]>>({});
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);       // true only BEFORE first token
  const [streaming, setStreaming] = useState(false);   // true while tokens arriving
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [ingestStatus, setIngestStatus] = useState<string | null>(null);
  const [showIngest, setShowIngest] = useState(false);
  const [showGraphExplorer, setShowGraphExplorer] = useState(false);
  const [isAtBottom, setIsAtBottom] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Use a ref to track the current local session key during streaming
  const localKeyRef = useRef<string | null>(null);

  // ── API Key Management ─────────────────────────────────────────────────────
  useEffect(() => {
    const storedDataJSON = localStorage.getItem(API_KEY_STORAGE_KEY);
    if (storedDataJSON) {
      try {
        const storedData: StoredApiKey = JSON.parse(storedDataJSON);
        if (storedData.key && storedData.expires > Date.now()) {
          setApiKey(storedData.key);
        } else {
          localStorage.removeItem(API_KEY_STORAGE_KEY);
        }
      } catch {
        localStorage.removeItem(API_KEY_STORAGE_KEY);
      }
    }
  }, []);

  const handleApiKeySubmit = (submittedKey: string) => {
    if (!submittedKey.trim()) return;
    const expires = Date.now() + API_KEY_EXPIRY_MS;
    const dataToStore: StoredApiKey = { key: submittedKey.trim(), expires };
    localStorage.setItem(API_KEY_STORAGE_KEY, JSON.stringify(dataToStore));
    setApiKey(submittedKey.trim());
  };

  const activeMessages = activeSessionId
    ? (messagesBySession[activeSessionId] ?? [])
    : [];

  // ── Load sessions from Redis on mount ─────────────────────────────────────
  useEffect(() => {
    if (!apiKey) return;
    const load = async () => {
      setLoadingSessions(true);
      try {
        const res = await fetch("/api/chat/sessions", {
          headers: { "X-API-Key": apiKey },
        });
        if (!res.ok) return;
        const data = await res.json();
        setSessions((data.sessions ?? []).map((s: any) => ({
          id: s.id,
          topic: s.topic,
          preview: s.preview,
          messageCount: s.message_count,
          createdAt: new Date(),
          updatedAt: new Date(),
        })));
      } catch (e) {
        console.error("Failed to load sessions:", e);
      } finally {
        setLoadingSessions(false);
      }
    };
    load();
  }, [apiKey]);

  // ── Load history when switching sessions ───────────────────────────────────
  useEffect(() => {
    if (!activeSessionId || messagesBySession[activeSessionId] || !apiKey) return;
    const load = async () => {
      setLoadingHistory(true);
      try {
        const res = await fetch(`/api/chat/${activeSessionId}/history`, {
          headers: { "X-API-Key": apiKey },
        });
        if (!res.ok) return;
        const data = await res.json();
        setMessagesBySession(prev => ({
          ...prev,
          [activeSessionId]: (data.history ?? []).map((m: any) => ({
            role: m.role,
            content: m.content,
            timestamp: new Date(),
          })),
        }));
      } catch (e) {
        console.error("Failed to load history:", e);
      } finally {
        setLoadingHistory(false);
      }
    };
    load();
  }, [activeSessionId, apiKey]);

  // ── Auto scroll ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (isAtBottom) bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [activeMessages.length, activeMessages[activeMessages.length - 1]?.content]);

  useEffect(() => { inputRef.current?.focus(); }, [activeSessionId]);

  const handleScroll = useCallback(() => {
    if (!containerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
    setIsAtBottom(scrollHeight - (scrollTop + clientHeight) < 80);
  }, []);

  const scrollToBottom = () =>
    containerRef.current?.scrollTo({ top: containerRef.current.scrollHeight, behavior: "smooth" });
  const scrollToTop = () =>
    containerRef.current?.scrollTo({ top: 0, behavior: "smooth" });

  // ── Session management ─────────────────────────────────────────────────────
  const createNewSession = useCallback(() => setActiveSessionId(null), []);
  const selectSession = useCallback((id: string) => setActiveSessionId(id), []);

  const deleteSession = useCallback(async (id: string) => {
    if (!apiKey) return;
    try {
      await fetch(`/api/chat/${id}`, {
        method: "DELETE",
        headers: { "X-API-Key": apiKey },
      });
    } catch {}
    setSessions(prev => prev.filter(s => s.id !== id));
    setMessagesBySession(prev => { const n = { ...prev }; delete n[id]; return n; });
    if (activeSessionId === id) setActiveSessionId(null);
  }, [activeSessionId, apiKey]);

  // ── Update the last message in a session ───────────────────────────────────
  const updateLastMessage = useCallback((sessionKey: string, update: Partial<Message>) => {
    setMessagesBySession(prev => {
      const msgs = [...(prev[sessionKey] ?? [])];
      if (msgs.length === 0) return prev;
      msgs[msgs.length - 1] = { ...msgs[msgs.length - 1], ...update };
      return { ...prev, [sessionKey]: msgs };
    });
  }, []);

  // ── Send message via streaming ─────────────────────────────────────────────
  const sendMessage = async () => {
    if (!input.trim() || loading || streaming || !apiKey) return;

    const question = input.trim();
    setInput("");
    setLoading(true);

    const isNewSession = activeSessionId === null;
    const localKey = isNewSession ? `local-${Date.now()}` : activeSessionId!;
    localKeyRef.current = localKey;

    const userMsg: Message = { role: "user", content: question, timestamp: new Date() };
    const placeholder: Message = {
      role: "assistant",
      content: "",
      timestamp: new Date(),
      streaming: true,
    };

    // Add user message + empty assistant placeholder
    setMessagesBySession(prev => ({
      ...prev,
      [localKey]: [...(prev[localKey] ?? []), userMsg, placeholder],
    }));

    if (isNewSession) setActiveSessionId(localKey);

    let fullAnswer = "";
    let reasoning: string[] = [];
    let sources: any[] = [];
    let graphContext = "";
    let strategy = "both";
    let firstToken = true;

    try {
      const res = await fetch("/api/chat/stream", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": apiKey,
        },
        body: JSON.stringify({
          question,
          session_id: isNewSession ? null : activeSessionId,
        }),
      });

      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            const event = JSON.parse(line.slice(6));

            if (event.type === "reasoning") {
              reasoning = [...reasoning, event.content];

            } else if (event.type === "sources") {
              sources = event.content ?? [];

            } else if (event.type === "graph") {
              graphContext = event.content ?? "";

            } else if (event.type === "token") {
              // Switch from loading → streaming on first token
              if (firstToken) {
                setLoading(false);
                setStreaming(true);
                firstToken = false;
              }
              fullAnswer += event.content;
              updateLastMessage(localKey, {
                content: fullAnswer,
                reasoning,
                sources,
                graph_context: graphContext,
                streaming: true,
              });

            } else if (event.type === "done") {
              const finalSid: string = event.session_id;
              strategy = event.strategy ?? "both";

              // Guard against empty answer
              if (!fullAnswer && !event.session_id) {
                updateLastMessage(localKey, {
                  content: "No response received. Check backend logs.",
                  streaming: false,
                });
                return;
              }

              const finalMsg: Message = {
                role: "assistant",
                content: fullAnswer || "(no response)",
                reasoning,
                strategy,
                sources,
                graph_context: graphContext,
                session_id: finalSid,
                timestamp: new Date(),
                streaming: false,
              };

              if (isNewSession) {
                const newSession: Session = {
                  id: finalSid,
                  topic: generateTopic(question),
                  preview: fullAnswer.slice(0, 60) + "…",
                  messageCount: 2,
                  createdAt: new Date(),
                  updatedAt: new Date(),
                };
                setSessions(prev => [newSession, ...prev]);
                setActiveSessionId(finalSid);
                setMessagesBySession(prev => {
                  const next = { ...prev };
                  next[finalSid] = [userMsg, finalMsg];
                  delete next[localKey];
                  return next;
                });
              } else {
                updateLastMessage(localKey, finalMsg);
                setSessions(prev => prev.map(s => s.id === localKey ? {
                  ...s,
                  preview: fullAnswer.slice(0, 60) + "…",
                  messageCount: s.messageCount + 2,
                  updatedAt: new Date(),
                } : s));
              }

            } else if (event.type === "error") {
              updateLastMessage(localKey, {
                content: `Error: ${event.content}`,
                streaming: false,
              });
            }
          } catch { /* malformed SSE line */ }
        }
      }
    } catch (e) {
      updateLastMessage(localKey, {
        content: e instanceof Error ? e.message : "Network error — is the backend running?",
        streaming: false,
      });
    } finally {
      setLoading(false);
      setStreaming(false);
      localKeyRef.current = null;
    }
  };

  const currentTopic = activeSessionId
    ? sessions.find(s => s.id === activeSessionId)?.topic
    : null;

  // ── Render API Key Input if needed ─────────────────────────────────────────
  if (!apiKey) {
    return (
      <div className="h-full flex items-center justify-center" style={{ background: "var(--surface)" }}>
        <div className="w-full max-w-sm p-8 rounded-2xl text-center animate-in fade-in zoom-in-95"
          style={{ background: "var(--surface2)", border: "1px solid var(--border2)" }}>
          <div className="w-16 h-16 rounded-2xl flex items-center justify-center mx-auto mb-6"
            style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
            <KeyRound size={28} style={{ color: "var(--accent)" }} />
          </div>
          <h1 className="text-lg font-bold mb-2" style={{ color: "var(--text)" }}>Enter API Key</h1>
          <p className="text-xs mb-6" style={{ color: "var(--text-muted)" }}>
            An API key is required to connect to the backend. It will be stored locally for 5 hours.
          </p>
          <form onSubmit={(e) => {
            e.preventDefault();
            handleApiKeySubmit(e.currentTarget.apiKey.value);
          }}>
            <input name="apiKey" type="password"
              placeholder="your-secret-api-key"
              className="w-full p-3 rounded-xl text-sm focus:outline-none transition-colors text-center font-mono"
              style={{ background: "var(--surface)", border: "1px solid var(--border)", color: "var(--text)" }} />
            <button type="submit" className="w-full mt-4 py-3 rounded-xl text-sm font-mono font-medium transition-all hover:scale-[1.01]"
              style={{ background: "var(--accent-glow)", border: "1px solid var(--accent2)", color: "var(--accent)" }}>
              Submit & Connect
            </button>
          </form>
        </div>
      </div>
    );
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="h-full flex flex-col relative z-10">
      <HealthBar apiKey={apiKey} />

      {ingestStatus && (
        <div
          className="absolute top-14 left-1/2 -translate-x-1/2 flex items-center gap-2 px-4 py-2 rounded-lg shadow-xl z-50 animate-in fade-in slide-in-from-top-4"
          style={{ background: "var(--accent-glow)", border: "1px solid var(--accent2)", color: "var(--accent)" }}
        >
          <Loader2 size={14} className="animate-spin" />
          <p className="text-xs font-mono">{ingestStatus}</p>
        </div>
      )}


      <div className="flex flex-1 overflow-hidden">
        <SessionSidebar
          sessions={sessions}
          activeId={activeSessionId}
          onSelect={selectSession}
          onNew={createNewSession}
          onDelete={deleteSession}
          loading={loadingSessions}
        />

        <div className="flex-1 flex flex-col overflow-hidden">

          {/* Header */}
          <div className="flex items-center justify-between px-5 py-3 border-b"
            style={{ borderColor: "var(--border)", background: "rgba(13,17,23,0.95)" }}>
            <div>
              {currentTopic ? (
                <>
                  <h2 className="text-sm font-medium" style={{ color: "var(--text)" }}>
                    {currentTopic}
                  </h2>
                  <p className="text-xs font-mono mt-0.5" style={{ color: "var(--text-muted)" }}>
                    {activeSessionId}
                  </p>
                </>
              ) : (
                <h2 className="text-sm font-medium" style={{ color: "var(--text-dim)" }}>
                  New conversation
                </h2>
              )}
            </div>
            <div className="flex items-center gap-2">
              <button onClick={() => setShowIngest(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-mono transition-all hover:scale-105"
                style={{ background: "var(--accent-glow)", border: "1px solid var(--accent2)", color: "var(--accent)" }}>
                <Upload size={10} />Ingest
              </button>
              <button onClick={() => setShowGraphExplorer(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-mono transition-all hover:scale-105"
                style={{ background: "var(--surface2)", border: "1px solid var(--border2)", color: "var(--text-dim)" }}>
                <GitBranch size={10} />Explore
              </button>
              {activeSessionId && sessions.find(s => s.id === activeSessionId) && (
                <button onClick={() => deleteSession(activeSessionId)}
                  className="p-1.5 rounded-lg transition-colors hover:opacity-70"
                  style={{ color: "var(--text-muted)", border: "1px solid var(--border)" }}>
                  <Trash2 size={12} />
                </button>
              )}
            </div>
          </div>

          {/* Messages */}
          <div ref={containerRef} onScroll={handleScroll}
            className="flex-1 overflow-y-auto px-5 py-5 relative">

            {loadingHistory ? (
              <div className="h-full flex items-center justify-center">
                <div className="text-center" style={{ color: "var(--text-muted)" }}>
                  <div className="flex gap-1 justify-center mb-3">
                    {[0,1,2].map(i => (
                      <div key={i} className="w-2 h-2 rounded-full"
                        style={{ background: "var(--accent)", animation: `thinking 1.4s ease-in-out ${i*0.2}s infinite` }} />
                    ))}
                  </div>
                  <p className="text-sm font-mono">Loading conversation…</p>
                </div>
              </div>

            ) : activeMessages.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center gap-4"
                style={{ color: "var(--text-muted)" }}>
                <div className="w-16 h-16 rounded-2xl flex items-center justify-center"
                  style={{ background: "var(--surface2)", border: "1px solid var(--border2)" }}>
                  <span className="text-3xl">🧠</span>
                </div>
                <div className="text-center">
                  <p className="text-sm font-medium mb-1" style={{ color: "var(--text-dim)" }}>
                    {activeSessionId ? "Conversation empty" : "Start a new conversation"}
                  </p>
                  <p className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>
                    Press Enter to send · Click Ingest to add documents
                  </p>
                </div>
                <div className="flex flex-wrap gap-2 justify-center mt-2">
                  {[
                    "Who is in the knowledge base?",
                    "What relationships exist?",
                    "Summarise the main topics",
                  ].map(p => (
                    <button key={p} onClick={() => setInput(p)}
                      className="px-3 py-1.5 rounded-full text-xs font-mono transition-all hover:scale-105"
                      style={{ background: "var(--surface2)", border: "1px solid var(--border2)", color: "var(--text-dim)" }}>
                      {p}
                    </button>
                  ))}
                </div>
              </div>

            ) : (
              <>
                {activeMessages.map((msg, i) => <MessageBubble key={i} msg={msg} />)}

                {/* Show thinking dots only while waiting for FIRST token */}
                {loading && (
                  <div className="flex gap-3 mb-5">
                    <div className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 mt-1"
                      style={{ background: "var(--surface2)", border: "1px solid var(--border2)" }}>
                      <span className="text-xs">🤖</span>
                    </div>
                    <div className="px-4 py-3 rounded-2xl rounded-tl-sm"
                      style={{ background: "var(--surface2)", border: "1px solid var(--border2)" }}>
                      <div className="flex gap-1 items-center h-4">
                        {[0,1,2].map(i => (
                          <div key={i} className="w-1.5 h-1.5 rounded-full"
                            style={{ background: "var(--accent)", animation: `thinking 1.4s ease-in-out ${i*0.2}s infinite` }} />
                        ))}
                      </div>
                    </div>
                  </div>
                )}
                <div ref={bottomRef} />
              </>
            )}

            {/* Scroll controls */}
            {activeMessages.length > 3 && (
              <div className="fixed right-6 bottom-24 flex flex-col gap-2 z-20">
                {!isAtBottom && (
                  <button onClick={scrollToBottom}
                    className="p-2 rounded-lg shadow-lg transition-all hover:scale-110"
                    style={{ background: "var(--accent-glow)", border: "1px solid var(--accent2)", color: "var(--accent)" }}>
                    <ChevronDown size={14} />
                  </button>
                )}
                <button onClick={scrollToTop}
                  className="p-2 rounded-lg shadow-lg transition-all hover:scale-110"
                  style={{ background: "var(--surface2)", border: "1px solid var(--border2)", color: "var(--text-muted)" }}>
                  <ChevronUp size={14} />
                </button>
              </div>
            )}
          </div>

          {/* Input */}
          <div className="px-5 py-4 border-t"
            style={{ borderColor: "var(--border)", background: "rgba(13,17,23,0.95)" }}>
            <div className="flex gap-2 items-center">
              <div className="flex-1 flex items-center rounded-2xl px-4 py-2.5"
                style={{ background: "var(--surface2)", border: "1px solid var(--border2)" }}>
                <input ref={inputRef} value={input}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      sendMessage();
                    }
                  }}
                  placeholder={streaming ? "Receiving response…" : "Ask a question…"}
                  disabled={loading || streaming}
                  className="flex-1 bg-transparent text-sm focus:outline-none"
                  style={{ color: "var(--text)", fontFamily: "'DM Sans', sans-serif" }} />
                <span className="text-xs font-mono ml-2" style={{ color: "var(--text-muted)" }}>↵</span>
              </div>
              <button onClick={sendMessage}
                disabled={loading || streaming || !input.trim()}
                className="p-3 rounded-xl transition-all hover:scale-105 disabled:opacity-40"
                style={{
                  background: !loading && !streaming && input.trim() ? "var(--accent-glow)" : "var(--surface2)",
                  border: `1px solid ${!loading && !streaming && input.trim() ? "var(--accent2)" : "var(--border2)"}`,
                  color: !loading && !streaming && input.trim() ? "var(--accent)" : "var(--text-muted)",
                }}>
                {loading || streaming
                  ? <RotateCcw size={14} className="animate-spin" />
                  : <Send size={14} />
                }
              </button>
            </div>
          </div>
        </div>
      </div>

      {showIngest && (
        <IngestModal
          apiKey={apiKey}
          onClose={() => setShowIngest(false)}
          onIngestStarted={(message: string) => {
            setShowIngest(false);
            setIngestStatus(message);
            setTimeout(() => setIngestStatus(null), 30000); // Show for 30s
          }}
        />
      )}

      {showGraphExplorer && (
        <GraphExplorerModal
          apiKey={apiKey}
          onClose={() => setShowGraphExplorer(false)}
        />
      )}
    </div>
  );
}