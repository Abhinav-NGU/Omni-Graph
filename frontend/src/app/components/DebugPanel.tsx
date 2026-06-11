"use client";
import { Source } from "../lib/types";
import { GitBranch, Database, Cpu, Hash } from "lucide-react";

interface Props {
  reasoning: string[];
  strategy: string;
  sources: Source[];
  graph_context: string;
  session_id: string;
}

function ScoreBadge({ score }: { score: number }) {
  const color = score > 0.6 ? "var(--green)" : score > 0.4 ? "var(--yellow)" : "var(--red)";
  const pct = Math.round(score * 100);
  return (
    <div className="flex items-center gap-1.5">
      <div className="h-1 w-16 rounded-full overflow-hidden" style={{ background: "var(--border2)" }}>
        <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="text-xs font-mono" style={{ color }}>{score.toFixed(3)}</span>
    </div>
  );
}

function PathVisualizer({ path }: { path: string }) {
  const components: React.ReactNode[] = [];
  const initialSplit = path.split(' --[');

  if (initialSplit.length > 0) {
    components.push(
      <span key="node-0" className="px-2 py-1 rounded text-center" style={{ background: 'var(--border)', color: 'var(--text-dim)', border: '1px solid var(--border2)' }}>
        {initialSplit[0].trim()}
      </span>
    );
  }

  for (let i = 1; i < initialSplit.length; i++) {
    const relAndNode = initialSplit[i].split(']-->');
    if (relAndNode.length !== 2) continue;
    const rel = relAndNode[0];
    const node = relAndNode[1];

    components.push(
      <div key={`rel-${i}`} className="flex items-center gap-1 mx-2 flex-shrink-0" style={{ color: 'var(--green)' }}>
        <div className="h-px w-4" style={{ background: 'var(--green)', opacity: 0.3 }} />
        <span className="text-xs font-bold tracking-wider" style={{ fontSize: '9px' }}>{rel}</span>
        <div className="h-px w-4" style={{ background: 'var(--green)', opacity: 0.3 }} />
        <span>→</span>
      </div>
    );
    components.push(
      <span key={`node-${i}`} className="px-2 py-1 rounded text-center" style={{ background: 'var(--border)', color: 'var(--text-dim)', border: '1px solid var(--border2)' }}>
        {node.trim()}
      </span>
    );
  }
  return <div className="flex items-center p-1">{components}</div>;
}

export default function DebugPanel({ reasoning, strategy, sources, graph_context, session_id }: Props) {
  const stratColor = strategy === "both" ? "var(--yellow)" : strategy === "graph" ? "var(--green)" : "var(--accent)";

  return (
    <div className="mt-2 rounded-xl overflow-hidden text-xs font-mono animate-fade-in"
      style={{ border: "1px solid var(--border2)", background: "var(--surface)" }}>

      {/* Header strip */}
      <div className="flex items-center justify-between px-3 py-2 border-b"
        style={{ borderColor: "var(--border)", background: "var(--surface2)" }}>
        <div className="flex items-center gap-2">
          <Cpu size={10} style={{ color: "var(--accent)" }} />
          <span style={{ color: "var(--text-dim)" }}>agent debug</span>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1">
            <Hash size={9} style={{ color: "var(--text-muted)" }} />
            <span style={{ color: "var(--text-muted)" }}>{session_id.slice(0, 8)}</span>
          </div>
          <div className="px-2 py-0.5 rounded text-xs font-bold"
            style={{ background: "rgba(0,0,0,0.3)", color: stratColor, border: `1px solid ${stratColor}` }}>
            {strategy}
          </div>
        </div>
      </div>

      <div className="p-3 space-y-3">

        {/* Reasoning trace */}
        <div>
          <div className="flex items-center gap-1.5 mb-2" style={{ color: "var(--text-dim)" }}>
            <Cpu size={9} />
            <span className="uppercase tracking-wider" style={{ fontSize: "10px" }}>reasoning trace</span>
          </div>
          <div className="space-y-1 pl-2 border-l" style={{ borderColor: "var(--border2)" }}>
            {reasoning.map((step, i) => (
              <div key={i} className="flex gap-2 py-0.5">
                <span style={{ color: "var(--text-muted)", minWidth: "16px" }}>{i + 1}.</span>
                <span style={{ color: "var(--text)" }}>{step}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Sources */}
        {sources.length > 0 && (
          <div>
            <div className="flex items-center gap-1.5 mb-2" style={{ color: "var(--text-dim)" }}>
              <Database size={9} />
              <span className="uppercase tracking-wider" style={{ fontSize: "10px" }}>
                vector sources ({sources.length})
              </span>
            </div>
            <div className="space-y-2">
              {sources.map((s, i) => (
                <div key={i} className="p-2 rounded-lg" style={{ background: "var(--surface2)", border: "1px solid var(--border)" }}>
                  <div className="flex items-center justify-between mb-1.5">
                    <span style={{ color: "var(--text-muted)", fontSize: "10px" }}>
                      {s.id.slice(0, 12)}…
                    </span>
                    <ScoreBadge score={s.score} />
                  </div>
                  <p className="leading-relaxed line-clamp-3" style={{ color: "var(--text-dim)" }}>
                    {s.text}
                  </p>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Graph paths */}
        {graph_context && (
          <div>
            <div className="flex items-center gap-1.5 mb-2" style={{ color: "var(--text-dim)" }}>
              <GitBranch size={9} />
              <span className="uppercase tracking-wider" style={{ fontSize: "10px" }}>graph paths</span>
            </div>
            <div className="p-2 rounded-lg space-y-2 overflow-x-auto" style={{ background: "var(--surface2)", border: "1px solid var(--border)" }}>
              {graph_context.split('\n').map((path, i) => (
                path.trim() && <PathVisualizer key={i} path={path} />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}