"use client";
import { useState } from "react";
import LoginGate from "./components/LoginGate";
import ChatWindow from "./components/ChatWindow";

export default function Home() {
  const [apiKey, setApiKey] = useState("");

  if (!apiKey) {
    return <LoginGate onConnect={setApiKey} />;
  }

  return (
    <div className="h-screen flex flex-col overflow-hidden">
      <ChatWindow apiKey={apiKey} />
    </div>
  );
}