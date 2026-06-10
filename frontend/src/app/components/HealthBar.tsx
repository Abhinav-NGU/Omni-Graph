"use client";
import { useEffect, useState } from "react";
import { HealthStatus } from "../lib/types";

interface Props { apiKey: string; }

export default function HealthBar({ apiKey }: Props) {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    const check = async () => {
      setChecking(true);
      try {
        const res = await fetch("/api/health");
        setHealth(await res.json());
      } catch {
        setHealth(null);
      } finally {
        setChecking(false);
      }
    };
    check();
    const t = setInterval(check, 20000);
    return () => clearInterval(t);
  }, []);

  const Dot = ({ ok }: { ok: boolean }) => (
    <span className="relative flex h-2 w-2">
      {ok && <span className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-50"
        style={{ background: "var(--green)" }} />}
      <span className="relative inline-flex rounded-full h-2 w-2"
        style={{ background: ok ? "var(--green)" : "var(--red)" }} />
    </span>
  );

  const Service = ({ name, ok }: { name: string; ok: boolean }) => (
    <div className="flex items-center gap-1.5">
      <Dot ok={ok} />
      <span className="font-mono text-xs" style={{ color: ok ? "var(--green)" : "var(--red)" }}>
        {name}
      </span>
    </div>
  );

  return (
    <div className="relative z-10 flex items-center justify-between px-5 py-2.5 border-b"
      style={{ borderColor: "var(--border)", background: "rgba(8,11,18,0.95)" }}>
      {/* Logo */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1.5">
          <div className="w-5 h-5 rounded"
            style={{ background: "linear-gradient(135deg, var(--accent), var(--green))" }} />
          <span className="font-mono font-semibold tracking-widest text-sm"
            style={{ color: "var(--accent)", textShadow: "0 0 20px var(--accent)" }}>
            OMNIGRAPH
          </span>
        </div>
        <span className="text-xs font-mono px-2 py-0.5 rounded"
          style={{ color: "var(--text-dim)", background: "var(--surface2)", border: "1px solid var(--border)" }}>
          v0.3
        </span>
      </div>

      {/* Services */}
      <div className="flex items-center gap-5">
        {checking ? (
          <span className="text-xs font-mono animate-pulse" style={{ color: "var(--text-muted)" }}>
            checking…
          </span>
        ) : health ? (
          <>
            <Service name="neo4j" ok={health.neo4j.status === "healthy"} />
            <Service name="qdrant" ok={health.qdrant.status === "healthy"} />
            <Service name="redis" ok={true} />
            <Service name="ollama" ok={true} />
          </>
        ) : (
          <span className="text-xs font-mono" style={{ color: "var(--red)" }}>
            backend unreachable
          </span>
        )}
      </div>

      {/* API key indicator */}
      <div className="flex items-center gap-1.5">
        <div className="w-1.5 h-1.5 rounded-full" style={{ background: "var(--green)" }} />
        <span className="text-xs font-mono" style={{ color: "var(--text-dim)" }}>
          key ••••{apiKey.slice(-4)}
        </span>
      </div>
    </div>
  );
}