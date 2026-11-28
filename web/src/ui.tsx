import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError } from "./api";

/* ---------- Toasts ---------- */
interface ToastItem { id: number; text: string; bad?: boolean; }
const ToastContext = createContext<(text: string, bad?: boolean) => void>(() => {});
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const push = useCallback((text: string, bad?: boolean) => {
    const id = Date.now() + Math.random();
    setItems([{ id, text, bad }]);
    setTimeout(() => setItems((list) => list.filter((t) => t.id !== id)), bad ? 6000 : 2200);
  }, []);
  useEffect(() => {
    const clear = () => setItems([]);
    window.addEventListener("app:clear-toasts", clear);
    return () => window.removeEventListener("app:clear-toasts", clear);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => <div key={t.id} className={`toast${t.bad ? " bad" : ""}`}>{t.text}</div>)}
      </div>
    </ToastContext.Provider>
  );
}
export const useToast = () => useContext(ToastContext);

/** Run an async action with a busy key and an error toast; resolves to undefined on failure. */
export function useAction() {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const run = useCallback(async <T,>(key: string, fn: () => Promise<T>, success?: string): Promise<T | undefined> => {
    setBusy(key);
    try {
      const result = await fn();
      if (success) toast(success);
      return result;
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "操作失败，请重试", true);
      return undefined;
    } finally {
      setBusy(null);
    }
  }, [toast]);
  return { busy, run };
}

export function useLoad<T>(loader: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const version = useRef(0);
  const reload = useCallback(async (): Promise<boolean | null> => {
    const mine = ++version.current;
    try {
      const value = await loader();
      if (mine !== version.current) return null;
      setData(value); setError(null);
      return true;
    } catch (e) {
      if (mine !== version.current) return null;
      setError(e instanceof ApiError ? e.message : "加载失败");
      return false;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => { reload(); }, [reload]);
  return { data, error, reload, setData };
}

/* ---------- Building blocks ---------- */
export function Msg({ tone = "info", children }: { tone?: "info" | "warn" | "bad" | "ok"; children: ReactNode }) {
  return <div className={`msg-bar ${tone}`} role={tone === "bad" ? "alert" : undefined}>{children}</div>;
}
export function Panel({ title, actions, children, flush, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; flush?: boolean; className?: string }) {
  return (
    <section className={`panel ${className || ""}`}>
      {(title || actions) && <div className="panel-head"><h3>{title}</h3>{actions && <div className="row">{actions}</div>}</div>}
      <div className={`panel-body${flush ? " flush" : ""}`}>{children}</div>
    </section>
  );
}
export function Loading({ error, onRetry }: { error?: string | null; onRetry?: () => void }) {
  if (error) return <Msg tone="bad">{error}{onRetry && <>，<button className="btn-link btn" onClick={onRetry}>重试</button></>}</Msg>;
  return <p className="faint" aria-busy="true">正在加载…</p>;
}
export function Bar({ value, tone }: { value: number; tone?: string }) {
  const pct = Math.max(0, Math.min(1, value || 0)) * 100;
  return <div className={`bar ${tone || ""}`} role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}><i style={{ width: `${pct}%` }} /></div>;
}
export const toneFor = (r: number | null | undefined) => r == null ? undefined : r >= 0.8 ? "ok" : r >= 0.5 ? "warn" : "bad";
export const pct = (r: number | null | undefined) => r == null ? "—" : `${Math.round(r * 100)}%`;
export const fmt = (n: number) => (Math.round(n * 100) / 100).toString();

export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { id: T; label: string; mark?: boolean }[] }) {
  return (
    <div className="tabs" role="tablist">
      {items.map((item) => (
        <button key={item.id} role="tab" aria-selected={value === item.id} className="tab" onClick={() => onChange(item.id)}>
          {item.label}{item.mark && <span className="mark" title="有未保存的修改">●</span>}
        </button>
      ))}
    </div>
  );
}
export function JsonView({ value }: { value: unknown }) {
  return <pre className="json mono">{JSON.stringify(value, null, 2)}</pre>;
}
export function Pick({ items, value, onChange, disabled, label }: {
  items: { id: string; label: string }[]; value: string[]; onChange: (v: string[]) => void; disabled?: boolean; label: string;
}) {
  return (
    <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}>
      <span>{label}</span>
      {items.length === 0 ? <small>暂无可选记录</small> : (
        <div className="pick">
          {items.map((item) => (
            <label key={item.id} className="check">
              <input type="checkbox" disabled={disabled} checked={value.includes(item.id)}
                onChange={(e) => onChange(e.target.checked ? [...value, item.id] : value.filter((v) => v !== item.id))} />
              <span>{item.label}</span>
            </label>
          ))}
        </div>
      )}
    </fieldset>
  );
}

export function downloadCsv(name: string, rows: (string | number | null | undefined)[][]) {
  const text = "﻿" + rows.map((r) => r.map((c) => `"${String(c ?? "").replace(/"/g, '""')}"`).join(",")).join("\r\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
