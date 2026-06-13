"use client";
import { useState } from "react";
import { ChevronDown, ChevronUp, User, Bot, FileText } from "lucide-react";
import { Message } from "../lib/types";
import DebugPanel from "./DebugPanel";

export default function MessageBubble({ msg }: { msg: Message }) {
  const [showDebug, setShowDebug] = useState(false);
  const isUser = msg.role === "user";
  const isStreaming = (msg as any).streaming && !msg.content;
  const hasDebug = !isUser && msg.reasoning && msg.reasoning.length > 0;
  const sourceCount = msg.sources?.length ?? 0;
  const hasGraph = !!msg.graph_context;

  // Intercept backend history so we don't render the entire raw text of the PDF
  let displayContent = msg.content;
  let attachedFileName: string | null = null;

  if (isUser) {
    const trimmed = displayContent.trim();
    if (trimmed.startsWith("📎 Attached:")) {
      attachedFileName = trimmed.replace("📎 Attached:", "").trim();
      displayContent = "";
    } else if (trimmed.includes("[ATTACHMENT:")) {
      const match = trimmed.match(/\[ATTACHMENT:\s*(.*?)\]/i);
      if (match) {
        attachedFileName = match[1].trim();
        displayContent = "";
      }
    } else if (trimmed.includes("[System: User uploaded a PDF named")) {
      const match = trimmed.match(/\[System:\s*User uploaded a PDF named\s*'(.*?)'\]/i);
      if (match) {
        attachedFileName = match[1].trim();
        displayContent = "";
      }
    }
  }

  return (
    <div className={`flex gap-3 mb-5 animate-slide-up ${isUser ? "flex-row-reverse" : "flex-row"}`}>

      {/* Avatar */}
      <div className="flex-shrink-0 w-7 h-7 rounded-lg flex items-center justify-center mt-1"
        style={{
          background: isUser ? "var(--user-bg)" : "var(--surface2)",
          border: `1px solid ${isUser ? "var(--user-border)" : "var(--border2)"}`,
        }}>
        {isUser
          ? <User size={12} style={{ color: "var(--accent)" }} />
          : <Bot size={12} style={{ color: "var(--green)" }} />
        }
      </div>

      <div className={`flex flex-col max-w-[80%] ${isUser ? "items-end" : "items-start"}`}>

        {/* Bubble */}
        <div className="px-4 py-3 rounded-2xl leading-relaxed text-sm whitespace-pre-wrap"
          style={isUser ? {
            background: "var(--user-bg)",
            border: "1px solid var(--user-border)",
            color: "var(--text)",
            borderTopRightRadius: "4px",
          } : {
            background: "var(--surface2)",
            border: "1px solid var(--border2)",
            color: "var(--text)",
            borderTopLeftRadius: "4px",
          }}>
          {attachedFileName && (
            <div className="flex items-center gap-3 pr-2">
              <div className="flex items-center justify-center w-8 h-8 rounded-lg" 
                style={{ background: "rgba(0,0,0,0.2)", border: "1px solid var(--border)" }}>
                <FileText size={14} style={{ color: "var(--accent)" }} />
              </div>
              <span className="font-mono text-xs">{attachedFileName}</span>
            </div>
          )}
          {displayContent}
          {isStreaming && (
            <span className="inline-block w-1.5 h-4 ml-1 bg-current rounded-sm align-text-bottom animate-pulse" />
          )}
        </div>

        {/* Debug toggle */}
        {hasDebug && (
          <>
            <button
              onClick={() => setShowDebug(!showDebug)}
              className="flex items-center gap-1.5 mt-1.5 px-2 py-1 rounded-lg text-xs font-mono transition-all hover:scale-105"
              style={{
                color: showDebug ? "var(--accent)" : "var(--text-muted)",
                background: showDebug ? "var(--accent-glow)" : "transparent",
                border: `1px solid ${showDebug ? "var(--accent2)" : "var(--border)"}`,
              }}>
              {showDebug ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
              <span>
                {showDebug ? "hide debug" : `debug · ${sourceCount} src${sourceCount !== 1 ? "s" : ""}${hasGraph ? " · graph" : ""} · ${msg.strategy}`}
              </span>
            </button>

            {showDebug && (
              <div className="w-full max-w-2xl">
                <DebugPanel
                  reasoning={msg.reasoning!}
                  strategy={msg.strategy!}
                  sources={msg.sources ?? []}
                  graph_context={msg.graph_context ?? ""}
                  session_id={msg.session_id ?? ""}
                />
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}