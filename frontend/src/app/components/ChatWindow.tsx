"use client";
import { useState, useRef, useEffect, useCallback } from "react";
import { Send, Upload, Trash2, RotateCcw, ChevronUp, ChevronDown } from "lucide-react";
import { Message, Session } from "../lib/types";
import MessageBubble from "./MessageBubble";
import SessionSidebar from "./SessionSidebar";
import IngestModal from "./IngestModal";
import HealthBar from "./HealthBar";

interface Props { apiKey: string; }

function generateTopic(question: string): string {
  const cleaned = question.replace(/[?!.,]/g, "").trim();
  return cleaned.length > 40 ? cleaned.slice(0, 40) + "…" : cleaned;
}

export default function ChatWindow({ apiKey }: Props) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messagesBySession, setMessagesBySession] = useState<Record<string, Message[]>>({});
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [showIngest, setShowIngest] = useState(false);
  const [isAtBottom, setIsAtBottom] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const activeMessages = activeSessionId ? (messagesBySession[activeSessionId] ?? []) : [];

  // ── Load all sessions from Redis on mount ──────────────────────────────────
  useEffect(() => {
    const loadSessions = async () => {
      setLoadingSessions(true);
      try {
        const res = await fetch("/api/chat/sessions", {
          headers: { "X-API-Key": apiKey },
        });
        if (!res.ok) return;
        const data = await res.json();
        const loaded: Session[] = (data.sessions ?? []).map((s: any) => ({
          id: s.id,
          topic: s.topic,
          preview: s.preview,
          messageCount: s.message_count,
          createdAt: new Date(),
          updatedAt: new Date(),
        }));
        setSessions(loaded);
      } catch (e) {
        console.error("Failed to load sessions:", e);
      } finally {
        setLoadingSessions(false);
      }
    };
    loadSessions();
  }, [apiKey]);

  // ── Load history when switching sessions ───────────────────────────────────
  useEffect(() => {
    if (!activeSessionId || messagesBySession[activeSessionId]) return;
    const load = async () => {
      setLoadingHistory(true);
      try {
        const res = await fetch(`/api/chat/${activeSessionId}/history`, {
          headers: { "X-API-Key": apiKey },
        });
        if (!res.ok) return;
        const data = await res.json();
        const msgs: Message[] = (data.history ?? []).map((m: any) => ({
          role: m.role,
          content: m.content,
          timestamp: new Date(),
        }));
        setMessagesBySession(prev => ({ ...prev, [activeSessionId]: msgs }));
      } catch (e) {
        console.error("Failed to load history:", e);
      } finally {
        setLoadingHistory(false);
      }
    };
    load();
  }, [activeSessionId, apiKey]);

  // ── Scroll handling ────────────────────────────────────────────────────────
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    setIsAtBottom(true);
  }, [activeMessages.length, loading]);

  useEffect(() => {
    inputRef.current?.focus();
  }, [activeSessionId]);

  const handleScroll = useCallback(() => {
    if (!containerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
    setIsAtBottom(scrollHeight - (scrollTop + clientHeight) < 50);
  }, []);

  const scrollToBottom = () =>
    containerRef.current?.scrollTo({ top: containerRef.current.scrollHeight, behavior: "smooth" });
  const scrollToTop = () =>
    containerRef.current?.scrollTo({ top: 0, behavior: "smooth" });

  // ── Session management ─────────────────────────────────────────────────────
  const createNewSession = useCallback(() => setActiveSessionId(null), []);
  const selectSession = useCallback((id: string) => setActiveSessionId(id), []);

  const deleteSession = useCallback(async (id: string) => {
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

  // ── Send message ───────────────────────────────────────────────────────────
  const sendMessage = async () => {
    if (!input.trim() || loading) return;
    const question = input.trim();
    setInput("");
    setLoading(true);

    const userMsg: Message = { role: "user", content: question, timestamp: new Date() };
    const tempSessionId = activeSessionId;

    if (tempSessionId) {
      setMessagesBySession(prev => ({
        ...prev,
        [tempSessionId]: [...(prev[tempSessionId] ?? []), userMsg],
      }));
    }

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
        body: JSON.stringify({ question, session_id: tempSessionId }),
      });
      const data = await res.json();
      const sid: string = data.session_id;

      const assistantMsg: Message = {
        role: "assistant",
        content: res.ok ? data.answer : `Error: ${data.detail ?? "Unknown error"}`,
        reasoning: data.reasoning,
        strategy: data.strategy,
        sources: data.sources,
        graph_context: data.graph_context,
        session_id: sid,
        timestamp: new Date(),
      };

      if (!tempSessionId) {
        // Brand new session
        const newSession: Session = {
          id: sid,
          topic: generateTopic(question),
          preview: assistantMsg.content.slice(0, 60) + "…",
          messageCount: 2,
          createdAt: new Date(),
          updatedAt: new Date(),
        };
        setSessions(prev => [newSession, ...prev]);
        setActiveSessionId(sid);
        setMessagesBySession(prev => ({ ...prev, [sid]: [userMsg, assistantMsg] }));
      } else {
        // Existing session — append assistant reply
        setMessagesBySession(prev => ({
          ...prev,
          [sid]: [...(prev[sid] ?? []), assistantMsg],
        }));
        setSessions(prev => prev.map(s => s.id === sid ? {
          ...s,
          preview: assistantMsg.content.slice(0, 60) + "…",
          messageCount: s.messageCount + 2,
          updatedAt: new Date(),
        } : s));
      }
    } catch {
      const errMsg: Message = {
        role: "assistant",
        content: "Network error — is the backend running?",
        timestamp: new Date(),
      };
      if (tempSessionId) {
        setMessagesBySession(prev => ({
          ...prev,
          [tempSessionId]: [...(prev[tempSessionId] ?? []), errMsg],
        }));
      }
    } finally {
      setLoading(false);
    }
  };

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="h-full flex flex-col relative z-10">
      <HealthBar apiKey={apiKey} />

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
              {activeSessionId ? (
                <>
                  <h2 className="text-sm font-medium" style={{ color: "var(--text)" }}>
                    {sessions.find(s => s.id === activeSessionId)?.topic ?? "Chat"}
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
              {activeSessionId && (
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
                  {["Who is in the knowledge base?", "What relationships exist?", "Summarise the main topics"].map(p => (
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
                  onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }}}
                  placeholder="Ask a question…"
                  disabled={loading}
                  className="flex-1 bg-transparent text-sm focus:outline-none"
                  style={{ color: "var(--text)", fontFamily: "'DM Sans', sans-serif" }} />
                <span className="text-xs font-mono ml-2" style={{ color: "var(--text-muted)" }}>↵</span>
              </div>
              <button onClick={sendMessage} disabled={loading || !input.trim()}
                className="p-3 rounded-xl transition-all hover:scale-105 disabled:opacity-40"
                style={{
                  background: !loading && input.trim() ? "var(--accent-glow)" : "var(--surface2)",
                  border: `1px solid ${!loading && input.trim() ? "var(--accent2)" : "var(--border2)"}`,
                  color: !loading && input.trim() ? "var(--accent)" : "var(--text-muted)",
                }}>
                {loading ? <RotateCcw size={14} className="animate-spin" /> : <Send size={14} />}
              </button>
            </div>
          </div>
        </div>
      </div>

      {showIngest && <IngestModal apiKey={apiKey} onClose={() => setShowIngest(false)} />}
    </div>
  );
}