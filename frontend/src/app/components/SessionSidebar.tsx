"use client";
import { Session } from "../lib/types";
import { MessageSquare, Plus, Trash2, Clock } from "lucide-react";

interface Props {
  sessions: Session[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  loading?: boolean;
}

function timeAgo(date: Date): string {
  const diff = Date.now() - date.getTime();
  const mins = Math.floor(diff / 60000);
  const hrs = Math.floor(mins / 60);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export default function SessionSidebar({ sessions, activeId, onSelect, onNew, onDelete, loading }: Props) {
  return (
    <div className="flex flex-col h-full w-64 border-r relative z-10"
      style={{ borderColor: "var(--border)", background: "rgba(13,17,23,0.97)" }}>

      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b"
        style={{ borderColor: "var(--border)" }}>
        <span className="text-xs font-mono font-medium tracking-wider uppercase"
          style={{ color: "var(--text-dim)" }}>
          Sessions
        </span>
        <button onClick={onNew}
          className="flex items-center gap-1 px-2 py-1 rounded text-xs font-mono transition-all hover:scale-105"
          style={{
            background: "var(--accent-glow)",
            border: "1px solid var(--accent2)",
            color: "var(--accent)",
          }}>
          <Plus size={10} />
          New
        </button>
      </div>

      {/* Session list */}
      <div className="flex-1 overflow-y-auto py-2">
        {loading ? (
          <div className="flex flex-col items-center justify-center h-32 gap-2"
            style={{ color: "var(--text-muted)" }}>
            <div className="flex gap-1">
              {[0, 1, 2].map(i => (
                <div key={i} className="w-1.5 h-1.5 rounded-full"
                  style={{
                    background: "var(--accent)",
                    animation: `thinking 1.4s ease-in-out ${i * 0.2}s infinite`,
                  }} />
              ))}
            </div>
            <span className="text-xs font-mono">loading sessions…</span>
          </div>

        ) : sessions.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-32 gap-2"
            style={{ color: "var(--text-muted)" }}>
            <MessageSquare size={20} />
            <span className="text-xs font-mono">no sessions yet</span>
          </div>

        ) : (
          sessions.map((s) => (
            <div key={s.id}
              onClick={() => onSelect(s.id)}
              className="group relative mx-2 mb-1 rounded-lg cursor-pointer transition-all"
              style={{
                background: activeId === s.id ? "var(--surface2)" : "transparent",
                border: `1px solid ${activeId === s.id ? "var(--border2)" : "transparent"}`,
              }}>
              <div className="px-3 py-2.5">
                {/* Topic */}
                <div className="text-xs font-medium mb-1 pr-6 leading-tight"
                  style={{ color: activeId === s.id ? "var(--accent)" : "var(--text)" }}>
                  {s.topic}
                </div>

                {/* Preview */}
                <div className="text-xs truncate mb-1.5"
                  style={{ color: "var(--text-muted)" }}>
                  {s.preview}
                </div>

                {/* Meta */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1" style={{ color: "var(--text-muted)" }}>
                    <Clock size={9} />
                    <span className="text-xs font-mono">{timeAgo(s.updatedAt)}</span>
                  </div>
                  <span className="text-xs font-mono px-1.5 py-0.5 rounded"
                    style={{ background: "var(--border)", color: "var(--text-dim)" }}>
                    {s.messageCount} msg
                  </span>
                </div>

                {/* Session ID */}
                <div className="mt-1 text-xs font-mono truncate"
                  style={{ color: "var(--text-muted)", fontSize: "10px" }}>
                  {s.id.slice(0, 8)}…{s.id.slice(-4)}
                </div>
              </div>

              {/* Delete button */}
              <button
                onClick={(e) => { e.stopPropagation(); onDelete(s.id); }}
                className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 p-1 rounded transition-all hover:scale-110"
                style={{ color: "var(--red)" }}>
                <Trash2 size={10} />
              </button>

              {/* Active indicator */}
              {activeId === s.id && (
                <div className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-8 rounded-r"
                  style={{ background: "var(--accent)" }} />
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}