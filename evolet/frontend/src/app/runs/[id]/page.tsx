"use client";

import React, { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  Activity,
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Clock,
  Database,
  FileText,
  Loader2,
  RefreshCcw,
  Terminal,
  Timer,
  UserRound,
  Zap,
} from "lucide-react";
import { getRunDetail } from "@/lib/api";
import { cn, formatDate } from "@/lib/utils";

const ACTIVE_STATUSES = new Set(["pending", "extracting", "triaging", "llm_processing", "merging", "qc"]);

function formatDuration(seconds?: number | null) {
  if (seconds === null || seconds === undefined) return "calculating";
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.round(seconds % 60);
  if (minutes < 60) return remainingSeconds ? `${minutes}m ${remainingSeconds}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
}

function logTone(level: string) {
  if (level === "error") return "border-rose-500/30 bg-rose-500/10 text-rose-300";
  if (level === "warning") return "border-amber-500/30 bg-amber-500/10 text-amber-300";
  return "border-slate-800 bg-slate-950/50 text-slate-300";
}

function statusTone(status: string) {
  if (status === "completed") return "border-emerald-500/20 bg-emerald-500/10 text-emerald-400";
  if (status === "failed") return "border-rose-500/20 bg-rose-500/10 text-rose-400";
  return "border-amber-500/20 bg-amber-500/10 text-amber-300";
}

export default function RunDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = () => {
    if (!id) return;
    getRunDetail(id)
      .then((res) => {
        setData(res.data);
        setLastRefresh(new Date());
        setError(null);
      })
      .catch((err) => {
        console.error(err);
        setError("Could not load this run");
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    refresh();
    const interval = window.setInterval(refresh, 3000);
    return () => window.clearInterval(interval);
  }, [id]);

  const run = data?.run;
  const logs = data?.logs || [];
  const documents = data?.documents || [];
  const patients = data?.patients || [];
  const active = run ? run.is_active || ACTIVE_STATUSES.has(run.status) : false;
  const latestLog = useMemo(() => logs[0], [logs]);

  if (loading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <Loader2 className="h-10 w-10 animate-spin text-indigo-400" />
      </div>
    );
  }

  if (error || !run) {
    return (
      <div className="mx-auto max-w-3xl p-8">
        <Link href="/runs" className="mb-6 inline-flex items-center gap-2 text-sm font-bold text-slate-400 hover:text-white">
          <ArrowLeft size={16} />
          Runs
        </Link>
        <div className="rounded-[2rem] border border-rose-500/20 bg-rose-500/10 p-8 text-rose-300">
          {error || "Run not found"}
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1700px] space-y-8 p-8 lg:p-12">
      <nav className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <Link href="/runs" className="inline-flex items-center gap-2 text-sm font-bold text-slate-500 hover:text-white">
          <ArrowLeft size={16} />
          Pipeline executions
        </Link>
        <button
          onClick={refresh}
          className="inline-flex w-fit items-center gap-2 rounded-2xl border border-slate-800 bg-slate-900 px-5 py-2.5 text-sm font-bold text-slate-300 transition-all hover:border-indigo-500/40 hover:text-white"
        >
          <RefreshCcw size={16} />
          Refresh now
        </button>
      </nav>

      <header className="rounded-[2.5rem] border border-slate-800/60 bg-[#11111d]/70 p-8">
        <div className="flex flex-col gap-6 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <div className={cn("mb-4 inline-flex items-center gap-2 rounded-full border px-3 py-1 text-[10px] font-black uppercase tracking-widest", statusTone(run.status))}>
              {active ? <Loader2 size={12} className="animate-spin" /> : run.status === "completed" ? <CheckCircle2 size={12} /> : <AlertCircle size={12} />}
              {run.status}
            </div>
            <h1 className="font-outfit text-4xl font-black tracking-tight text-white">{run.name || "Untitled run"}</h1>
            <p className="mt-2 font-mono text-xs text-slate-500">{run.id}</p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 xl:w-[520px]">
            <div className="rounded-2xl border border-slate-800 bg-slate-950/40 p-4">
              <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Last refresh</p>
              <p className="mt-2 text-sm font-bold text-white">{lastRefresh ? lastRefresh.toLocaleTimeString() : "pending"}</p>
            </div>
            <div className="rounded-2xl border border-slate-800 bg-slate-950/40 p-4">
              <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Latest event</p>
              <p className="mt-2 truncate text-sm font-bold text-white">{latestLog?.message || "Waiting for logs"}</p>
            </div>
          </div>
        </div>

        <div className="mt-8 h-3 overflow-hidden rounded-full bg-slate-950/70">
          <div className="h-full rounded-full bg-gradient-to-r from-amber-400 via-indigo-400 to-cyan-400 transition-all" style={{ width: `${run.progress}%` }} />
        </div>
      </header>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        <div className="rounded-[2rem] border border-slate-800/60 bg-[#11111d]/70 p-6">
          <Activity className="mb-4 text-indigo-400" size={20} />
          <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Progress</p>
          <p className="mt-2 font-outfit text-3xl font-black text-white">{run.progress}%</p>
        </div>
        <div className="rounded-[2rem] border border-slate-800/60 bg-[#11111d]/70 p-6">
          <FileText className="mb-4 text-cyan-400" size={20} />
          <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Documents</p>
          <p className="mt-2 font-outfit text-3xl font-black text-white">{run.processed_pdfs}/{run.total_pdfs}</p>
        </div>
        <div className="rounded-[2rem] border border-slate-800/60 bg-[#11111d]/70 p-6">
          <Timer className="mb-4 text-amber-300" size={20} />
          <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">ETA</p>
          <p className="mt-2 font-outfit text-3xl font-black text-white">{run.eta_text || (active ? "calculating" : formatDuration(run.elapsed_seconds))}</p>
        </div>
        <div className="rounded-[2rem] border border-slate-800/60 bg-[#11111d]/70 p-6">
          <Zap className="mb-4 text-emerald-400" size={20} />
          <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Rate</p>
          <p className="mt-2 font-outfit text-3xl font-black text-white">{run.docs_per_minute || 0}/m</p>
        </div>
        <div className="rounded-[2rem] border border-slate-800/60 bg-[#11111d]/70 p-6">
          <Database className="mb-4 text-rose-400" size={20} />
          <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Mentions</p>
          <p className="mt-2 font-outfit text-3xl font-black text-white">{run.total_mentions}</p>
        </div>
      </section>

      <div className="grid gap-8 xl:grid-cols-[1.15fr_0.85fr]">
        <section className="rounded-[2rem] border border-slate-800 bg-[#11111d]">
          <div className="flex items-center justify-between border-b border-slate-800 px-6 py-4">
            <div>
              <p className="text-sm font-black text-white">Live Logs</p>
              <p className="text-xs text-slate-500">Newest events appear first, refreshed every 3 seconds.</p>
            </div>
            <Terminal className="text-slate-500" size={20} />
          </div>
          <div className="max-h-[720px] space-y-3 overflow-auto p-5">
            {logs.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-slate-800 p-8 text-center text-sm text-slate-500">
                No log events yet.
              </div>
            ) : (
              logs.map((log: any) => (
                <div key={log.id} className={cn("rounded-2xl border p-4", logTone(log.level))}>
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <span className="rounded-lg bg-black/20 px-2 py-1 text-[10px] font-black uppercase tracking-widest">{log.level}</span>
                      <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">{log.stage || "pipeline"}</span>
                    </div>
                    <span className="flex items-center gap-1 text-[10px] font-bold text-slate-500">
                      <Clock size={12} />
                      {new Date(log.created_at).toLocaleTimeString()}
                    </span>
                  </div>
                  <p className="text-sm font-semibold leading-relaxed">{log.message}</p>
                </div>
              ))
            )}
          </div>
        </section>

        <aside className="space-y-8">
          <section className="rounded-[2rem] border border-slate-800 bg-[#11111d]">
            <div className="border-b border-slate-800 px-6 py-4">
              <p className="text-sm font-black text-white">Run Results</p>
              <p className="text-xs text-slate-500">Only patients attached to this run are shown here.</p>
            </div>
            <div className="max-h-[360px] divide-y divide-slate-800 overflow-auto">
              {patients.map((patient: any) => (
                <Link key={patient.id} href={`/patients/${patient.id}`} className="flex items-center justify-between gap-4 p-5 transition-colors hover:bg-white/5">
                  <div className="flex min-w-0 items-center gap-3">
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-indigo-500/20 bg-indigo-500/10 text-indigo-300">
                      <UserRound size={18} />
                    </div>
                    <div className="min-w-0">
                      <p className="truncate text-sm font-bold text-white">{patient.display_name}</p>
                      <p className="truncate font-mono text-[10px] text-slate-500">{patient.code}</p>
                    </div>
                  </div>
                  <span className="text-xs font-bold text-slate-500">{patient.mention_count} mentions</span>
                </Link>
              ))}
              {patients.length === 0 && <p className="p-6 text-sm text-slate-500">No patients linked yet.</p>}
            </div>
          </section>

          <section className="rounded-[2rem] border border-slate-800 bg-[#11111d]">
            <div className="border-b border-slate-800 px-6 py-4">
              <p className="text-sm font-black text-white">Source PDFs</p>
              <p className="text-xs text-slate-500">{documents.length} documents in this run.</p>
            </div>
            <div className="max-h-[360px] divide-y divide-slate-800 overflow-auto">
              {documents.map((doc: any) => (
                <div key={doc.id} className="p-5">
                  <p className="truncate text-sm font-bold text-white">{doc.filename}</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <span className="rounded-lg border border-slate-800 bg-slate-950/50 px-2 py-1 text-[10px] font-bold uppercase tracking-widest text-slate-500">
                      {doc.page_count || 0} pages
                    </span>
                    <span className="rounded-lg border border-slate-800 bg-slate-950/50 px-2 py-1 text-[10px] font-bold uppercase tracking-widest text-slate-500">
                      {formatDate(doc.created_at)}
                    </span>
                  </div>
                </div>
              ))}
              {documents.length === 0 && <p className="p-6 text-sm text-slate-500">No source documents linked yet.</p>}
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}
