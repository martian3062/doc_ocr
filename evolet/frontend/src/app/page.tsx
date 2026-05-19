"use client";

import React, { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Activity,
  CheckCircle2,
  Cpu,
  Database,
  FileText,
  GitBranch,
  Layers3,
  PenLine,
  ShieldCheck,
  Users,
} from "lucide-react";
import { getDashboardData } from "@/lib/api";
import { cn, formatDate } from "@/lib/utils";

type DashboardData = {
  stats?: {
    total_patients: number;
    total_pdfs: number;
    total_mentions: number;
    total_artifacts: number;
    total_relations: number;
  };
  gpu?: {
    available: boolean;
    name?: string;
    used_gb?: number;
    total_gb?: number;
    utilization_pct?: number;
  };
  recent_runs?: Array<{
    id: string;
    name: string;
    status: string;
    progress: number;
    total_pdfs: number;
    total_mentions: number;
    started_at?: string | null;
  }>;
  pipeline_stack?: {
    parsers_enabled: boolean;
    parser_backends: string[];
    handwriting_model: string;
    medical_handwriting_model?: string;
    medocr_reference_dataset?: string;
    verification_model: string;
    handwriting_ocr_enabled: boolean;
    medical_handwriting_ocr_enabled?: boolean;
    page_vision_sweep_enabled?: boolean;
    verification_enabled: boolean;
    medical_validation_required?: boolean;
  };
};

const defaultStats = {
  total_patients: 0,
  total_pdfs: 0,
  total_mentions: 0,
  total_artifacts: 0,
  total_relations: 0,
};

export default function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getDashboardData()
      .then((res) => setData(res.data))
      .finally(() => setLoading(false));
  }, []);

  const stats = data?.stats || defaultStats;
  const gpu = data?.gpu || { available: false };
  const stack = data?.pipeline_stack;
  const parserBackends = useMemo(
    () => stack?.parser_backends?.length ? stack.parser_backends : ["native", "pymupdf"],
    [stack]
  );

  if (loading) {
    return (
      <div className="flex min-h-[70vh] items-center justify-center">
        <div className="flex items-center gap-3 rounded-xl border border-slate-800 bg-slate-950 px-5 py-4 text-slate-300">
          <Activity className="animate-spin text-cyan-400" size={20} />
          Loading pipeline state
        </div>
      </div>
    );
  }

  const statCards = [
    { label: "Patients", value: stats.total_patients, icon: Users },
    { label: "Documents", value: stats.total_pdfs, icon: FileText },
    { label: "Evidence artifacts", value: stats.total_artifacts, icon: Layers3 },
    { label: "Relations", value: stats.total_relations, icon: GitBranch },
  ];

  return (
    <div className="mx-auto max-w-7xl space-y-8 p-6 lg:p-10">
      <header className="relative overflow-hidden rounded-3xl border border-sky-200/70 bg-white/70 p-7 shadow-[0_24px_90px_rgba(56,189,248,0.18)] backdrop-blur-2xl lg:flex lg:items-end lg:justify-between">
        <div className="absolute inset-0 -z-10 bg-[radial-gradient(circle_at_20%_10%,rgba(255,255,255,0.95),transparent_30%),radial-gradient(circle_at_88%_0%,rgba(111,220,255,0.44),transparent_34%)]" />
        <div>
          <div className="mb-3 flex items-center gap-3 text-sm font-semibold uppercase tracking-widest text-sky-500">
            <ShieldCheck size={18} />
            Evidence-native clinical reader
          </div>
          <h1 className="font-outfit text-4xl font-black tracking-tight text-sky-950">
            doc-reader
          </h1>
          <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-600">
            LLM-first document understanding with grouped text extraction,
            adaptive schemas, validation, relation extraction, and exact source
            provenance.
          </p>
        </div>
        <div className="flex gap-3">
          <Link
            href="/documents"
            className="rounded-lg bg-gradient-to-r from-white via-sky-200 to-cyan-400 px-4 py-2 text-sm font-bold text-sky-950 shadow-[0_12px_34px_rgba(56,189,248,0.26)] transition hover:brightness-105"
          >
            Documents
          </Link>
          <Link
            href="/runs"
            className="rounded-lg border border-sky-200 bg-white/70 px-4 py-2 text-sm font-bold text-sky-700 transition hover:border-sky-400 hover:bg-white"
          >
            Runs
          </Link>
        </div>
      </header>

      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {statCards.map((item) => (
          <div key={item.label} className="rounded-xl border border-slate-800 bg-slate-950 p-5">
            <div className="mb-4 flex items-center justify-between">
              <item.icon className="text-cyan-300" size={22} />
              <span className="rounded-full bg-slate-900 px-2 py-1 text-[11px] font-semibold text-slate-400">
                Live
              </span>
            </div>
            <div className="font-outfit text-3xl font-black text-white">{item.value}</div>
            <div className="mt-1 text-sm text-slate-400">{item.label}</div>
          </div>
        ))}
      </section>

      <section className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <div className="rounded-xl border border-slate-800 bg-slate-950 p-6 xl:col-span-2">
          <div className="mb-5 flex items-center gap-3">
            <Layers3 className="text-cyan-300" size={22} />
            <h2 className="font-outfit text-xl font-bold text-white">Active Pipeline</h2>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <PipelineItem
              title="Parser layer"
              value={parserBackends.join(" + ")}
              active={Boolean(stack?.parsers_enabled)}
            />
            <PipelineItem
              title="Handwriting OCR"
              value={stack?.handwriting_model || "microsoft/trocr-large-handwritten"}
              active={Boolean(stack?.handwriting_ocr_enabled)}
            />
            <PipelineItem
              title="Medical handwriting"
              value={stack?.medical_handwriting_model || stack?.medocr_reference_dataset || "MedOCR reference dataset"}
              active={Boolean(stack?.medical_handwriting_ocr_enabled)}
            />
            <PipelineItem
              title="Page vision sweep"
              value="Header + vitals + handwritten orders"
              active={Boolean(stack?.page_vision_sweep_enabled)}
            />
            <PipelineItem
              title="Verification OCR"
              value={stack?.verification_model || "stepfun-ai/GOT-OCR-2.0-hf"}
              active={Boolean(stack?.verification_enabled)}
            />
            <PipelineItem
              title="Medical validation"
              value={stack?.medical_validation_required ? "Required after extraction" : "Heuristic fallback"}
              active={Boolean(stack?.medical_validation_required)}
            />
            <PipelineItem
              title="Extraction output"
              value={`${stats.total_mentions} mentions, ${stats.total_relations} relation edges`}
              active={stats.total_mentions > 0 || stats.total_relations > 0}
            />
          </div>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-950 p-6">
          <div className="mb-5 flex items-center gap-3">
            <Cpu className="text-emerald-300" size={22} />
            <h2 className="font-outfit text-xl font-bold text-white">GPU Runtime</h2>
          </div>
          <div className="space-y-4">
            <div>
              <div className="text-sm text-slate-400">Device</div>
              <div className="mt-1 font-semibold text-white">
                {gpu.available ? gpu.name || "CUDA GPU" : "CPU fallback"}
              </div>
            </div>
            <div>
              <div className="mb-2 flex justify-between text-sm">
                <span className="text-slate-400">VRAM</span>
                <span className="font-semibold text-slate-200">
                  {gpu.used_gb ?? 0} / {gpu.total_gb ?? 0} GB
                </span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-slate-900">
                <div
                  className="h-full rounded-full bg-emerald-400"
                  style={{
                    width: `${gpu.total_gb ? Math.min(100, ((gpu.used_gb || 0) / gpu.total_gb) * 100) : 0}%`,
                  }}
                />
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="rounded-xl border border-slate-800 bg-slate-950 p-6">
        <div className="mb-5 flex items-center gap-3">
          <Database className="text-cyan-300" size={22} />
          <h2 className="font-outfit text-xl font-bold text-white">Recent Runs</h2>
        </div>
        <div className="space-y-3">
          {(data?.recent_runs || []).map((run) => (
            <Link
              href={`/runs/${run.id}`}
              key={run.id}
              className="grid gap-3 rounded-lg border border-slate-800 bg-slate-900/40 p-4 transition hover:border-cyan-500/50 md:grid-cols-[1fr_auto_auto]"
            >
              <div>
                <div className="font-semibold text-white">{run.name}</div>
                <div className="mt-1 text-xs text-slate-500">
                  {run.started_at ? formatDate(run.started_at) : "Not started"}
                </div>
              </div>
              <div className="text-sm text-slate-300">{run.total_pdfs} files</div>
              <div className={cn("text-sm font-bold", run.status === "completed" ? "text-emerald-300" : "text-cyan-300")}>
                {run.status} · {run.progress}%
              </div>
            </Link>
          ))}
          {(data?.recent_runs || []).length === 0 && (
            <div className="rounded-lg border border-dashed border-slate-800 p-8 text-center text-sm text-slate-500">
              No runs yet. Upload documents and start a pipeline run.
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

function PipelineItem({ title, value, active }: { title: string; value: string; active: boolean }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-4">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="text-sm font-semibold text-slate-300">{title}</div>
        {active ? (
          <CheckCircle2 className="shrink-0 text-emerald-300" size={18} />
        ) : (
          <PenLine className="shrink-0 text-slate-600" size={18} />
        )}
      </div>
      <div className="break-words text-sm text-slate-500">{value}</div>
    </div>
  );
}
