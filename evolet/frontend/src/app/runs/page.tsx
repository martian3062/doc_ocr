"use client";

import React, { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  Clock,
  ExternalLink,
  FileText,
  Loader2,
  Play,
  RefreshCcw,
  Timer,
  Zap,
} from "lucide-react";
import { getRunHistory } from "@/lib/api";
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

function statusTone(status: string) {
  if (status === "completed") return "border-emerald-500/20 bg-emerald-500/10 text-emerald-400";
  if (status === "failed") return "border-rose-500/20 bg-rose-500/10 text-rose-400";
  return "border-amber-500/20 bg-amber-500/10 text-amber-300";
}

function statusIcon(status: string) {
  if (status === "completed") return <CheckCircle2 size={14} />;
  if (status === "failed") return <AlertCircle size={14} />;
  return <Loader2 size={14} className="animate-spin" />;
}

export default function RunsPage() {
  const [runs, setRuns] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refreshRuns = () => {
    getRunHistory()
      .then((res) => {
        setRuns(res.data.runs || []);
        setLastRefresh(new Date());
        setError(null);
      })
      .catch((err) => {
        console.error(err);
        setError("Could not load run history");
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    refreshRuns();
    const interval = window.setInterval(refreshRuns, 5000);
    return () => window.clearInterval(interval);
  }, []);

  const activeRun = useMemo(() => runs.find((run) => run.is_active || ACTIVE_STATUSES.has(run.status)), [runs]);
  const completedCount = runs.filter((run) => run.status === "completed").length;
  const failedCount = runs.filter((run) => run.status === "failed").length;

  if (loading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <Loader2 className="h-10 w-10 animate-spin text-indigo-400" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1600px] space-y-8 p-8 lg:p-12">
      <header className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="mb-3 text-xs font-black uppercase tracking-widest text-indigo-400">Live runner</p>
          <h1 className="font-outfit text-4xl font-black tracking-tight text-white">Pipeline Executions</h1>
          <p className="mt-2 max-w-2xl text-sm text-slate-400">
            Watch active OCR jobs, throughput, ETA, document progress, and run logs from the processing queue.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button
            onClick={refreshRuns}
            className="flex items-center gap-2 rounded-2xl border border-slate-800 bg-slate-900 px-5 py-2.5 text-sm font-bold text-slate-300 transition-all hover:border-indigo-500/40 hover:text-white"
          >
            <RefreshCcw size={16} />
            Refresh
          </button>
          <button
            disabled
            className="flex cursor-not-allowed items-center gap-2 rounded-2xl border border-slate-800 bg-slate-900/60 px-5 py-2.5 text-sm font-bold text-slate-500"
            title="Batch launch is handled from backend import/queue commands right now"
          >
            <Play size={16} />
            Execute Batch
          </button>
        </div>
      </header>

      {error && (
        <div className="rounded-2xl border border-rose-500/20 bg-rose-500/10 p-4 text-sm font-bold text-rose-300">
          {error}
        </div>
      )}

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <div className="rounded-[2rem] border border-slate-800/60 bg-[#11111d]/70 p-6">
          <div className="mb-4 flex items-center justify-between">
            <span className="text-xs font-black uppercase tracking-widest text-slate-500">Runs</span>
            <Activity className="text-indigo-400" size={18} />
          </div>
          <p className="font-outfit text-3xl font-black text-white">{runs.length}</p>
          <p className="mt-1 text-xs text-slate-500">Total execution records</p>
        </div>
        <div className="rounded-[2rem] border border-slate-800/60 bg-[#11111d]/70 p-6">
          <div className="mb-4 flex items-center justify-between">
            <span className="text-xs font-black uppercase tracking-widest text-slate-500">Active</span>
            <Zap className="text-amber-300" size={18} />
          </div>
          <p className="font-outfit text-3xl font-black text-white">{activeRun ? 1 : 0}</p>
          <p className="mt-1 text-xs text-slate-500">{activeRun ? activeRun.status : "No job running"}</p>
        </div>
        <div className="rounded-[2rem] border border-slate-800/60 bg-[#11111d]/70 p-6">
          <div className="mb-4 flex items-center justify-between">
            <span className="text-xs font-black uppercase tracking-widest text-slate-500">Completed</span>
            <CheckCircle2 className="text-emerald-400" size={18} />
          </div>
          <p className="font-outfit text-3xl font-black text-white">{completedCount}</p>
          <p className="mt-1 text-xs text-slate-500">{failedCount} failed</p>
        </div>
        <div className="rounded-[2rem] border border-slate-800/60 bg-[#11111d]/70 p-6">
          <div className="mb-4 flex items-center justify-between">
            <span className="text-xs font-black uppercase tracking-widest text-slate-500">Refresh</span>
            <Clock className="text-cyan-400" size={18} />
          </div>
          <p className="font-outfit text-xl font-black text-white">{lastRefresh ? lastRefresh.toLocaleTimeString() : "pending"}</p>
          <p className="mt-1 text-xs text-slate-500">Auto-refresh every 5s</p>
        </div>
      </section>

      {activeRun && (
        <section className="rounded-[2.5rem] border border-amber-500/20 bg-amber-500/5 p-8">
          <div className="mb-6 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-amber-500/20 bg-amber-500/10 px-3 py-1 text-[10px] font-black uppercase tracking-widest text-amber-300">
                <Loader2 size={12} className="animate-spin" />
                Running now
              </div>
              <h2 className="font-outfit text-2xl font-black text-white">{activeRun.name || "Untitled run"}</h2>
              <p className="mt-1 font-mono text-xs text-slate-500">{activeRun.id}</p>
            </div>
            <Link
              href={`/runs/${activeRun.id}`}
              className="inline-flex items-center gap-2 rounded-2xl bg-amber-400 px-5 py-2.5 text-sm font-black text-slate-950 transition-all hover:bg-amber-300"
            >
              Open live logs
              <ExternalLink size={16} />
            </Link>
          </div>
          <div className="grid gap-4 md:grid-cols-4">
            <div className="rounded-2xl border border-slate-800/60 bg-slate-950/40 p-5">
              <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Progress</p>
              <p className="mt-2 font-outfit text-3xl font-black text-white">{activeRun.progress}%</p>
            </div>
            <div className="rounded-2xl border border-slate-800/60 bg-slate-950/40 p-5">
              <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Documents</p>
              <p className="mt-2 font-outfit text-3xl font-black text-white">{activeRun.processed_pdfs}/{activeRun.total_pdfs}</p>
            </div>
            <div className="rounded-2xl border border-slate-800/60 bg-slate-950/40 p-5">
              <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">ETA</p>
              <p className="mt-2 font-outfit text-3xl font-black text-white">{activeRun.eta_text || formatDuration(activeRun.eta_seconds)}</p>
            </div>
            <div className="rounded-2xl border border-slate-800/60 bg-slate-950/40 p-5">
              <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Rate</p>
              <p className="mt-2 font-outfit text-3xl font-black text-white">{activeRun.docs_per_minute || 0}/m</p>
            </div>
          </div>
          <div className="mt-6 h-3 overflow-hidden rounded-full bg-slate-950/70">
            <motion.div
              animate={{ width: `${activeRun.progress}%` }}
              className="h-full rounded-full bg-gradient-to-r from-amber-400 via-indigo-400 to-cyan-400"
            />
          </div>
        </section>
      )}

      <section className="overflow-hidden rounded-[2rem] border border-slate-800 bg-[#11111d]">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr className="border-b border-slate-800 bg-slate-900/50">
              <th className="px-6 py-4 text-[10px] font-bold uppercase tracking-widest text-slate-500">Execution</th>
              <th className="px-6 py-4 text-[10px] font-bold uppercase tracking-widest text-slate-500">Status</th>
              <th className="px-6 py-4 text-[10px] font-bold uppercase tracking-widest text-slate-500">Progress</th>
              <th className="px-6 py-4 text-[10px] font-bold uppercase tracking-widest text-slate-500">ETA / Rate</th>
              <th className="px-6 py-4 text-[10px] font-bold uppercase tracking-widest text-slate-500">Volume</th>
              <th className="px-6 py-4 text-[10px] font-bold uppercase tracking-widest text-slate-500">Started</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {runs.map((run, i) => (
              <motion.tr
                key={run.id}
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: i * 0.03 }}
                className="group transition-colors hover:bg-white/5"
              >
                <td className="px-6 py-5">
                  <Link href={`/runs/${run.id}`} className="flex items-center gap-4">
                    <div
                      className={cn(
                        "flex h-10 w-10 items-center justify-center rounded-xl",
                        run.status === "completed"
                          ? "bg-emerald-500/10 text-emerald-400"
                          : run.status === "failed"
                            ? "bg-rose-500/10 text-rose-400"
                            : "bg-amber-500/10 text-amber-300"
                      )}
                    >
                      <Activity size={20} />
                    </div>
                    <div>
                      <p className="text-sm font-bold text-white transition-colors group-hover:text-indigo-400">
                        {run.name || "Untitled run"}
                      </p>
                      <p className="mt-0.5 font-mono text-[10px] text-slate-600">{run.id.substring(0, 13)}</p>
                    </div>
                  </Link>
                </td>
                <td className="px-6 py-5">
                  <div className={cn("inline-flex items-center gap-2 rounded-full border px-3 py-1 text-[10px] font-bold uppercase tracking-widest", statusTone(run.status))}>
                    {statusIcon(run.status)}
                    {run.status}
                  </div>
                </td>
                <td className="px-6 py-5">
                  <div className="w-36">
                    <div className="mb-1 flex justify-between text-[10px]">
                      <span className="text-slate-500">Processing</span>
                      <span className="text-slate-200">{run.progress}%</span>
                    </div>
                    <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
                      <motion.div animate={{ width: `${run.progress}%` }} className="h-full bg-indigo-500" />
                    </div>
                  </div>
                </td>
                <td className="px-6 py-5">
                  <div className="flex items-center gap-3 text-sm text-slate-300">
                    <Timer size={16} className="text-slate-500" />
                    <div>
                      <p className="font-bold text-white">{run.eta_text || (run.is_active ? "calculating" : formatDuration(run.elapsed_seconds))}</p>
                      <p className="text-[10px] font-bold uppercase tracking-widest text-slate-600">{run.docs_per_minute || 0} docs/min</p>
                    </div>
                  </div>
                </td>
                <td className="px-6 py-5">
                  <div className="text-sm font-medium text-slate-400">
                    <span className="text-white">{run.processed_pdfs}</span> / {run.total_pdfs} docs
                    <p className="text-[10px] font-bold text-slate-600">{run.total_mentions} mentions</p>
                  </div>
                </td>
                <td className="px-6 py-5">
                  <div className="flex items-center gap-2 text-xs text-slate-500">
                    <FileText size={14} />
                    {run.started_at ? formatDate(run.started_at) : "Not started"}
                  </div>
                </td>
              </motion.tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
