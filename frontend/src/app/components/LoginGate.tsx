"use client";
import { useState } from "react";
import { Key, ArrowRight, Loader } from "lucide-react";

interface Props { onConnect: (key: string) => void; }

export default function LoginGate({ onConnect }: Props) {
  const [key, setKey] = useState("");
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");

  const connect = async () => {
    if (!key.trim()) return;
    setChecking(true);
    setError("");
    try {
      // Verify key works by hitting health endpoint
      const res = await fetch("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": key },
        body: JSON.stringify({ question: "test" }),
      });
      if (res.status === 401 || res.status === 403) {
        setError("Invalid API key.");
        return;
      }
      onConnect(key);
    } catch {
      // Network error — still let them in, backend might just be slow
      onConnect(key);
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="h-screen flex items-center justify-center relative"
      style={{ background: "var(--bg)" }}>

      {/* Background glow */}
      <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
        <div className="w-96 h-96 rounded-full opacity-5"
          style={{ background: "radial-gradient(circle, var(--accent) 0%, transparent 70%)" }} />
      </div>

      <div className="relative w-full max-w-sm mx-4">
        {/* Card */}
        <div className="rounded-2xl overflow-hidden"
          style={{
            background: "var(--surface)",
            border: "1px solid var(--border2)",
            boxShadow: "0 0 60px rgba(0,212,255,0.05)",
          }}>

          {/* Top accent bar */}
          <div className="h-0.5 w-full"
            style={{ background: "linear-gradient(90deg, var(--accent), var(--green), transparent)" }} />

          <div className="p-8">
            {/* Logo */}
            <div className="flex items-center gap-3 mb-8">
              <div className="w-10 h-10 rounded-xl flex items-center justify-center"
                style={{ background: "var(--accent-glow)", border: "1px solid var(--accent2)" }}>
                <div className="w-5 h-5 rounded"
                  style={{ background: "linear-gradient(135deg, var(--accent), var(--green))" }} />
              </div>
              <div>
                <h1 className="font-mono font-bold tracking-widest text-base"
                  style={{ color: "var(--accent)" }}>
                  OMNIGRAPH
                </h1>
                <p className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>
                  knowledge graph agent
                </p>
              </div>
            </div>

            {/* Form */}
            <div className="space-y-4">
              <div>
                <label className="block text-xs font-mono mb-2" style={{ color: "var(--text-dim)" }}>
                  API Key
                </label>
                <div className="relative">
                  <Key size={13} className="absolute left-3 top-1/2 -translate-y-1/2"
                    style={{ color: "var(--text-muted)" }} />
                  <input
                    type="password"
                    value={key}
                    onChange={e => { setKey(e.target.value); setError(""); }}
                    onKeyDown={e => e.key === "Enter" && connect()}
                    placeholder="Enter your API key"
                    className="w-full pl-9 pr-4 py-3 rounded-xl text-sm focus:outline-none transition-all"
                    style={{
                      background: "var(--surface2)",
                      border: `1px solid ${error ? "var(--red)" : "var(--border2)"}`,
                      color: "var(--text)",
                      fontFamily: "'JetBrains Mono', monospace",
                    }}
                    onFocus={e => !error && (e.target.style.borderColor = "var(--accent2)")}
                    onBlur={e => !error && (e.target.style.borderColor = "var(--border2)")}
                  />
                </div>
                {error && (
                  <p className="mt-1.5 text-xs font-mono" style={{ color: "var(--red)" }}>
                    {error}
                  </p>
                )}
              </div>

              <button
                onClick={connect}
                disabled={!key.trim() || checking}
                className="w-full py-3 rounded-xl text-sm font-mono font-medium transition-all hover:scale-[1.02] disabled:opacity-40 disabled:hover:scale-100 flex items-center justify-center gap-2"
                style={{
                  background: "var(--accent-glow)",
                  border: "1px solid var(--accent2)",
                  color: "var(--accent)",
                }}>
                {checking
                  ? <><Loader size={13} className="animate-spin" />Connecting…</>
                  : <><span>Connect</span><ArrowRight size={13} /></>
                }
              </button>
            </div>

            {/* Hint */}
            <p className="mt-6 text-xs text-center font-mono" style={{ color: "var(--text-muted)" }}>
              Set via <span style={{ color: "var(--accent)" }}>API_KEY</span> in your .env
            </p>
          </div>
        </div>

        {/* Status dots */}
        <div className="flex items-center justify-center gap-2 mt-4">
          {["neo4j", "qdrant", "ollama", "redis"].map(s => (
            <span key={s} className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>
              {s}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}