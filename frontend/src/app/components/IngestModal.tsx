"use client";
import { useState } from "react";
import { Upload, Link, FileText, Check, AlertCircle, Loader, X } from "lucide-react";

interface Props {
  apiKey: string;
  onClose: () => void;
}

type Tab = "text" | "pdf" | "url";
type Status = "idle" | "loading" | "success" | "error";

export default function IngestModal({ apiKey, onClose }: Props) {
  const [tab, setTab] = useState<Tab>("text");
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState("");

  const headers = { "X-API-Key": apiKey };

  const submit = async () => {
    setStatus("loading");
    setMessage("");
    try {
      let res: Response;
      if (tab === "text") {
        res = await fetch("/api/ingest", {
          method: "POST",
          headers: { ...headers, "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        });
      } else if (tab === "url") {
        res = await fetch("/api/ingest/url", {
          method: "POST",
          headers: { ...headers, "Content-Type": "application/json" },
          body: JSON.stringify({ url }),
        });
      } else {
        const fd = new FormData();
        fd.append("file", file!);
        res = await fetch("/api/ingest/pdf", { method: "POST", headers, body: fd });
      }
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? "Failed");
      setStatus("success");
      const chars = data.characters_extracted;
      setMessage(chars
        ? `Queued — ${chars.toLocaleString()} characters extracted`
        : data.message ?? "Queued successfully");
      setText(""); setUrl(""); setFile(null);
    } catch (e: any) {
      setStatus("error");
      setMessage(e.message ?? "Ingestion failed");
    }
  };

  const canSubmit = tab === "text" ? !!text.trim() : tab === "url" ? !!url.trim() : !!file;

  const tabs: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: "text", label: "Text", icon: <FileText size={11} /> },
    { id: "pdf",  label: "PDF",  icon: <Upload size={11} /> },
    { id: "url",  label: "URL",  icon: <Link size={11} /> },
  ];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.7)", backdropFilter: "blur(4px)" }}
      onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="w-full max-w-lg rounded-2xl overflow-hidden animate-slide-up"
        style={{ background: "var(--surface)", border: "1px solid var(--border2)" }}>

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b"
          style={{ borderColor: "var(--border)", background: "var(--surface2)" }}>
          <div>
            <h2 className="font-mono font-semibold text-sm" style={{ color: "var(--accent)" }}>
              Ingest Document
            </h2>
            <p className="text-xs mt-0.5" style={{ color: "var(--text-dim)" }}>
              Add content to the knowledge base
            </p>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg transition-colors hover:opacity-70"
            style={{ color: "var(--text-dim)" }}>
            <X size={14} />
          </button>
        </div>

        <div className="p-5">
          {/* Tabs */}
          <div className="flex gap-1 mb-4 p-1 rounded-xl"
            style={{ background: "var(--surface2)", border: "1px solid var(--border)" }}>
            {tabs.map(t => (
              <button key={t.id} onClick={() => { setTab(t.id); setStatus("idle"); }}
                className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-xs font-mono font-medium transition-all"
                style={{
                  background: tab === t.id ? "var(--border2)" : "transparent",
                  color: tab === t.id ? "var(--accent)" : "var(--text-dim)",
                  border: tab === t.id ? "1px solid var(--border2)" : "1px solid transparent",
                }}>
                {t.icon}{t.label}
              </button>
            ))}
          </div>

          {/* Input */}
          {tab === "text" && (
            <textarea value={text} onChange={e => setText(e.target.value)}
              placeholder="Paste your text here…"
              className="w-full h-40 p-3 rounded-xl text-sm resize-none focus:outline-none transition-colors"
              style={{
                background: "var(--surface2)",
                border: "1px solid var(--border)",
                color: "var(--text)",
                fontFamily: "'DM Sans', sans-serif",
              }}
              onFocus={e => (e.target.style.borderColor = "var(--accent2)")}
              onBlur={e => (e.target.style.borderColor = "var(--border)")} />
          )}

          {tab === "url" && (
            <input value={url} onChange={e => setUrl(e.target.value)}
              placeholder="https://en.wikipedia.org/wiki/…"
              className="w-full p-3 rounded-xl text-sm focus:outline-none transition-colors"
              style={{
                background: "var(--surface2)",
                border: "1px solid var(--border)",
                color: "var(--text)",
              }}
              onFocus={e => (e.target.style.borderColor = "var(--accent2)")}
              onBlur={e => (e.target.style.borderColor = "var(--border)")} />
          )}

          {tab === "pdf" && (
            <div onClick={() => document.getElementById("pdf-upload")?.click()}
              className="flex flex-col items-center justify-center h-32 rounded-xl cursor-pointer transition-all"
              style={{
                border: `2px dashed ${file ? "var(--accent2)" : "var(--border2)"}`,
                background: file ? "var(--accent-glow)" : "var(--surface2)",
              }}>
              <input id="pdf-upload" type="file" accept=".pdf" className="hidden"
                onChange={e => setFile(e.target.files?.[0] ?? null)} />
              <Upload size={20} className="mb-2"
                style={{ color: file ? "var(--accent)" : "var(--text-muted)" }} />
              <span className="text-xs font-mono"
                style={{ color: file ? "var(--accent)" : "var(--text-dim)" }}>
                {file ? file.name : "click to select PDF"}
              </span>
              {file && (
                <span className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>
                  {(file.size / 1024).toFixed(1)} KB
                </span>
              )}
            </div>
          )}

          {/* Submit */}
          <button onClick={submit} disabled={!canSubmit || status === "loading"}
            className="w-full mt-4 py-3 rounded-xl text-sm font-mono font-medium transition-all hover:scale-[1.01]"
            style={{
              background: canSubmit && status !== "loading" ? "var(--accent-glow)" : "var(--surface2)",
              border: `1px solid ${canSubmit && status !== "loading" ? "var(--accent2)" : "var(--border)"}`,
              color: canSubmit && status !== "loading" ? "var(--accent)" : "var(--text-muted)",
            }}>
            {status === "loading"
              ? <span className="flex items-center justify-center gap-2">
                  <Loader size={12} className="animate-spin" /> Ingesting…
                </span>
              : "Ingest"}
          </button>

          {/* Status */}
          {status === "success" && (
            <div className="mt-3 flex items-center gap-2 p-3 rounded-xl text-xs font-mono"
              style={{ background: "rgba(0,255,157,0.05)", border: "1px solid var(--green-dim)", color: "var(--green)" }}>
              <Check size={12} />{message}
            </div>
          )}
          {status === "error" && (
            <div className="mt-3 flex items-center gap-2 p-3 rounded-xl text-xs font-mono"
              style={{ background: "rgba(255,77,109,0.05)", border: "1px solid var(--red)", color: "var(--red)" }}>
              <AlertCircle size={12} />{message}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}