"use client";
import { useState } from "react";
import { Search, Loader, X, GitBranch, AlertCircle } from "lucide-react";
import Graph from "react-graph-vis";

interface Props {
  apiKey: string;
  onClose: () => void;
}

const graphOptions = {
  layout: { hierarchical: false },
  edges: {
    color: "#888",
    arrows: { to: { enabled: true, scaleFactor: 0.5 } },
    font: { color: '#fff', size: 10, strokeWidth: 0, align: 'top' },
  },
  physics: {
    enabled: true,
    barnesHut: {
      gravitationalConstant: -10000,
      centralGravity: 0.1,
      springLength: 120,
      springConstant: 0.05,
      damping: 0.09,
      avoidOverlap: 0.1
    },
    solver: 'barnesHut',
    stabilization: { iterations: 200 },
  },
  nodes: {
    shape: "dot",
    size: 16,
    font: { size: 12, color: '#fff' },
    borderWidth: 2,
    color: { border: 'var(--accent2)', background: 'var(--surface)' },
  },
  interaction: {
    dragNodes: true,
    dragView: true,
    zoomView: true,
    tooltipDelay: 100,
  },
  height: "500px",
};

export default function GraphExplorerModal({ apiKey, onClose }: Props) {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [graphData, setGraphData] = useState<{ nodes: any[], edges: any[] }>({ nodes: [], edges: [] });

  const searchNode = async () => {
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    setGraphData({ nodes: [], edges: [] });

    try {
      const res = await fetch(`/api/graph/visual_search?q=${encodeURIComponent(query)}`, {
        headers: { "X-API-Key": apiKey },
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Search failed");
      setGraphData(data);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.7)", backdropFilter: "blur(4px)" }}
      onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="w-full max-w-3xl rounded-2xl overflow-hidden animate-slide-up"
        style={{ background: "var(--surface)", border: "1px solid var(--border2)" }}>

        <div className="flex items-center justify-between px-5 py-4 border-b"
          style={{ borderColor: "var(--border)", background: "var(--surface2)" }}>
          <div>
            <h2 className="font-mono font-semibold text-sm flex items-center gap-2" style={{ color: "var(--accent)" }}>
              <GitBranch size={14} /> Graph Explorer
            </h2>
            <p className="text-xs mt-0.5" style={{ color: "var(--text-dim)" }}>
              Search for a node to visualize its connections
            </p>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg transition-colors hover:opacity-70"
            style={{ color: "var(--text-dim)" }}><X size={14} /></button>
        </div>

        <div className="p-5">
          <div className="flex gap-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && searchNode()}
              placeholder="Search for an entity name (e.g., 'Elon Musk')"
              className="flex-1 p-3 rounded-xl text-sm focus:outline-none transition-colors"
              style={{ background: "var(--surface2)", border: "1px solid var(--border)", color: "var(--text)" }}
            />
            <button onClick={searchNode} disabled={!query.trim() || loading}
              className="px-4 py-3 rounded-xl text-sm font-mono font-medium transition-all disabled:opacity-50 flex items-center justify-center gap-2"
              style={{ background: "var(--accent-glow)", border: "1px solid var(--accent2)", color: "var(--accent)" }}>
              {loading ? <Loader size={12} className="animate-spin" /> : <Search size={12} />} Search
            </button>
          </div>

          <div className="mt-4 h-[500px] rounded-lg relative" style={{ background: "var(--surface2)", border: "1px solid var(--border)" }}>
            {loading && <div className="absolute inset-0 flex items-center justify-center"><Loader size={24} className="animate-spin" style={{ color: "var(--accent)" }} /></div>}
            
            {!loading && graphData.nodes.length > 0 && (
              <Graph graph={graphData} options={graphOptions} />
            )}

            {!loading && graphData.nodes.length === 0 && !error && (
              <div className="h-full flex items-center justify-center text-center text-xs font-mono" style={{ color: "var(--text-muted)" }}>
                {graphData.nodes.length === 0 && query ? `No results for "${query}"` : "Search for a node to see the graph"}
              </div>
            )}

            {error && (
              <div className="h-full flex items-center justify-center text-center text-xs font-mono gap-2" style={{ color: "var(--red)" }}>
                <AlertCircle size={14} /> {error}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}