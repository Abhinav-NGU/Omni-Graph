"use client";
import { useEffect } from "react";

interface Props { onConnect: (key: string) => void; }

export default function LoginGate({ onConnect }: Props) {
  useEffect(() => {
    onConnect(process.env.NEXT_PUBLIC_API_KEY || "changeme");
  }, [onConnect]);

  return null;
}