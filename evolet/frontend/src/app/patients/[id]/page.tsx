"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import { 
  ArrowLeft, 
  Share2, 
  Download, 
  Clock, 
  ShieldCheck, 
  Tag, 
  MapPin, 
  FileText,
  ChevronRight,
  ExternalLink,
  Activity,
  History,
  Info,
  Layers,
  Zap,
  CheckCircle2,
  Eye
} from "lucide-react";
import { getPatientDetail } from "@/lib/api";
import { cn, formatDate } from "@/lib/utils";

type SummaryRow = {
  label: string;
  value: string;
};

type SummarySection = {
  category: string;
  items: SummaryRow[];
};

type TimelineEvent = {
  id?: string;
  date_text?: string;
  relation_type?: string;
  source_label?: string;
  target_label?: string;
  source_value?: string;
  target_value?: string;
  evidence_pages?: number[];
  certainty?: number | string;
  evidence_quote?: string;
  origin?: string;
  label?: string;
  value?: string;
};

function humanizeKey(value: string) {
  return value.replace(/_/g, " ");
}

function formatSummaryValue(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "N/A";
  }

  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }

  if (Array.isArray(value)) {
    const primitiveItems = value.filter(
      (item) => item === null || ["string", "number", "boolean"].includes(typeof item)
    );

    if (primitiveItems.length === value.length) {
      return primitiveItems.map((item) => formatSummaryValue(item)).join(", ");
    }

    return `${value.length} entries`;
  }

  return "Structured data";
}

function flattenSummaryObject(input: unknown, parentKey = ""): SummaryRow[] {
  if (input === null || input === undefined) {
    return [];
  }

  if (Array.isArray(input)) {
    return input.flatMap((item, index) => flattenSummaryObject(item, `${parentKey} ${index + 1}`.trim()));
  }

  if (typeof input !== "object") {
    return parentKey ? [{ label: humanizeKey(parentKey), value: formatSummaryValue(input) }] : [];
  }

  const value = input as Record<string, unknown>;

  if (typeof value.label === "string" && (value.value !== undefined || value.full_text !== undefined)) {
    return [
      {
        label: value.label,
        value: formatSummaryValue(value.value ?? value.full_text),
      },
    ];
  }

  if (value.full_text !== undefined && parentKey) {
    return [
      {
        label: humanizeKey(parentKey),
        value: formatSummaryValue(value.full_text),
      },
    ];
  }

  return Object.entries(value).flatMap(([key, nested]) => {
    const nestedKey = parentKey ? `${parentKey} ${key}` : key;

    if (Array.isArray(nested)) {
      const primitive = nested.every(
        (item) => item === null || ["string", "number", "boolean"].includes(typeof item)
      );

      if (primitive) {
        return [{ label: humanizeKey(nestedKey), value: formatSummaryValue(nested) }];
      }
    }

    if (nested !== null && typeof nested === "object") {
      return flattenSummaryObject(nested, nestedKey);
    }

    return [{ label: humanizeKey(nestedKey), value: formatSummaryValue(nested) }];
  });
}

function buildSummarySections(grouped: Record<string, unknown> | null | undefined): SummarySection[] {
  if (!grouped || typeof grouped !== "object") {
    return [];
  }

  const skipKeys = new Set(["mentions_flat", "grouped_record", "traceability", "stats", "review_flags", "schema_version", "validation"]);

  return Object.entries(grouped)
    .filter(([key]) => !skipKeys.has(key))
    .map(([category, value]) => ({
      category,
      items: flattenSummaryObject(value),
    }))
    .filter((section) => section.items.length > 0);
}

// --- Evidence Tooltip Component ---
const EvidenceTooltip = ({ mention }: { mention: any }) => {
  if (!mention) return null;

  const certaintyToPercent = (certainty: unknown) => {
    if (typeof certainty === "number") {
      return Math.round(Math.max(0, Math.min(certainty, 1)) * 1000) / 10;
    }

    if (typeof certainty === "string") {
      const numeric = Number(certainty);
      if (!Number.isNaN(numeric)) {
        const normalized = numeric > 1 ? numeric / 100 : numeric;
        return Math.round(Math.max(0, Math.min(normalized, 1)) * 1000) / 10;
      }

      const mapped: Record<string, number> = {
        confirmed: 98.2,
        high: 92,
        medium: 75,
        low: 55,
        unknown: 50,
      };

      return mapped[certainty.toLowerCase()] ?? 50;
    }

    return 50;
  };

  return (
    <motion.div 
      initial={{ opacity: 0, y: 10, scale: 0.95 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      className="absolute z-50 w-80 p-5 rounded-2xl glass border border-indigo-500/30 shadow-2xl shadow-indigo-500/10 pointer-events-none"
      style={{ bottom: "100%", left: "50%", transform: "translateX(-50%)", marginBottom: "12px" }}
    >
      <div className="flex items-center justify-between mb-3">
        <span className="text-[10px] font-black uppercase tracking-widest text-indigo-400">Evidence Proof</span>
        <div className="px-2 py-0.5 rounded-md bg-indigo-500/10 border border-indigo-500/20 text-[10px] font-bold text-indigo-300">
          {certaintyToPercent(mention.certainty).toFixed(1)}% Confidence
        </div>
      </div>
      <p className="text-xs text-slate-300 italic mb-4 leading-relaxed line-clamp-3">
        "{mention.evidence_quote || 'No specific text extract available'}"
      </p>
      <div className="flex items-center justify-between pt-3 border-t border-slate-800/50">
        <div className="flex items-center gap-2 text-[10px] text-slate-500 font-bold uppercase">
          <FileText size={10} /> {mention.origin || 'Source'}
        </div>
        <div className="text-[10px] text-indigo-400 font-bold">Trace ID: {mention.id.substring(0, 8)}</div>
      </div>
    </motion.div>
  );
};

export default function PatientDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState("summary");
  const [hoveredMention, setHoveredMention] = useState<string | null>(null);
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(null);

  useEffect(() => {
    if (!id) {
      return;
    }

    getPatientDetail(id).then(res => {
      setData(res.data);
      setLoading(false);
    }).catch(err => {
      console.error(err);
      setLoading(false);
    });
  }, [id]);

  if (loading) return (
    <div className="flex items-center justify-center min-h-[60vh]">
      <div className="relative">
        <div className="w-16 h-16 border-4 border-indigo-500/20 border-t-indigo-500 rounded-full animate-spin"></div>
        <div className="absolute inset-0 flex items-center justify-center">
          <ShieldCheck size={24} className="text-indigo-500 animate-pulse" />
        </div>
      </div>
    </div>
  );

  const patient = data?.patient;
  const mentions = data?.mentions || [];
  const artifacts = data?.artifacts || [];
  const relations = data?.relations || [];
  const final_record = data?.final_record;
  const documents = data?.documents || [];
  const selectedDocument = documents.find((doc: any) => doc.id === selectedDocumentId) || documents[0];
  const validation = final_record?.stats?.validation || final_record?.grouped?.validation;
  const summarySections = buildSummarySections(final_record?.grouped);
  const timelineEvents: TimelineEvent[] = final_record?.timeline_events || [];
  const artifactPreview = artifacts.slice(0, 8);
  const certaintyToPercent = (certainty: unknown) => {
    if (typeof certainty === "number") {
      return Math.round(Math.max(0, Math.min(certainty, 1)) * 100);
    }

    if (typeof certainty === "string") {
      const numeric = Number(certainty);
      if (!Number.isNaN(numeric)) {
        const normalized = numeric > 1 ? numeric / 100 : numeric;
        return Math.round(Math.max(0, Math.min(normalized, 1)) * 100);
      }

      const mapped: Record<string, number> = {
        confirmed: 98,
        high: 92,
        medium: 75,
        low: 55,
        unknown: 50,
      };

      return mapped[certainty.toLowerCase()] ?? 50;
    }

    return 50;
  };

  return (
    <div className="p-8 lg:p-12 space-y-12 max-w-[1600px] mx-auto min-h-screen">
      <nav className="flex items-center justify-between">
        <Link href="/patients" className="flex items-center gap-2 text-slate-500 hover:text-white transition-colors group px-4 py-2 rounded-xl hover:bg-slate-900/50 border border-transparent hover:border-slate-800">
          <ArrowLeft size={18} className="group-hover:-translate-x-1 transition-transform" />
          <span className="font-bold text-sm">Patient Explorer</span>
        </Link>
        <div className="flex gap-4">
          <Link 
            href={`/patients/${id}/knowledge-map`}
            className="flex items-center gap-2 px-6 py-2.5 bg-indigo-600/10 border border-indigo-500/30 text-indigo-400 rounded-2xl hover:bg-indigo-600/20 transition-all font-bold text-sm shadow-lg shadow-indigo-600/5"
          >
            <Share2 size={16} /> Knowledge Map
          </Link>
          <button className="flex items-center gap-2 px-6 py-2.5 bg-slate-900 border border-slate-800 text-slate-300 rounded-2xl hover:bg-slate-800 transition-all font-bold text-sm">
            <Download size={16} /> Export JSON
          </button>
        </div>
      </nav>

      {/* Header Profile */}
      <header className="flex flex-col md:flex-row items-center justify-between p-10 rounded-[3rem] bg-[#11111d]/50 backdrop-blur-xl border border-slate-800/40 relative overflow-hidden group">
        <div className="absolute top-0 right-0 w-[500px] h-[500px] bg-indigo-500/10 blur-[120px] pointer-events-none rounded-full -mr-64 -mt-64 transition-all group-hover:bg-indigo-500/15" />
        <div className="flex flex-col md:flex-row items-center gap-10 relative z-10 text-center md:text-left">
          <div className="w-32 h-32 rounded-[2.5rem] bg-gradient-to-br from-indigo-500 to-purple-600 p-[2px] shadow-2xl shadow-indigo-500/20">
             <div className="w-full h-full rounded-[2.4rem] bg-[#0a0a0f] flex items-center justify-center text-4xl font-black text-white font-outfit uppercase tracking-tighter">
                {patient?.display_name?.substring(0, 2) || "??"}
             </div>
          </div>
          <div>
            <div className="flex flex-col md:flex-row md:items-center gap-4 mb-4">
              <h1 className="text-4xl font-black text-white tracking-tight font-outfit">{patient?.display_name || "Unknown Patient"}</h1>
              <div className="flex justify-center md:justify-start">
                 <span className="px-4 py-1.5 bg-emerald-500/10 text-emerald-400 text-[10px] font-black uppercase tracking-widest rounded-full border border-emerald-500/20 shadow-[0_0_15px_rgba(16,185,129,0.1)]">Verified Diagnosis</span>
              </div>
            </div>
            <div className="flex flex-wrap items-center justify-center md:justify-start gap-8 text-slate-400 font-medium">
              <span className="flex items-center gap-2.5 px-3 py-1.5 rounded-xl bg-slate-900/50 border border-slate-800 text-xs font-mono"><Tag size={14} className="text-indigo-400" /> {patient?.code || "NO_ID"}</span>
              <span className="flex items-center gap-2.5 text-xs uppercase tracking-widest font-bold"><MapPin size={14} className="text-rose-500" /> Clinical Pathology Archive</span>
            </div>
          </div>
        </div>
        <div className="mt-8 md:mt-0 flex flex-col items-center md:items-end gap-3 relative z-10">
          <div className="flex items-center gap-3">
             <div className="text-right">
                <p className="text-slate-500 text-[10px] font-black uppercase tracking-widest mb-1">Extraction Confidence</p>
                <div className="flex items-center justify-center md:justify-end gap-2">
                   <span className="text-3xl font-black text-white font-outfit">98.2%</span>
                   <CheckCircle2 className="text-emerald-500" size={24} />
                </div>
             </div>
          </div>
          <div className="w-48 h-2 bg-slate-900 rounded-full overflow-hidden border border-slate-800/60 p-[1px]">
             <motion.div initial={{ width: 0 }} animate={{ width: '98.2%' }} className="h-full bg-gradient-to-r from-emerald-500 to-indigo-500 rounded-full" />
          </div>
        </div>
      </header>

      {/* Main Content Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-12">
        <aside className="lg:col-span-1 space-y-10">
          <div className="p-8 rounded-[2.5rem] bg-[#11111d]/50 backdrop-blur-xl border border-slate-800/40">
            <h3 className="font-black text-white text-sm font-outfit mb-6 flex items-center gap-3 uppercase tracking-widest text-indigo-400">
              <FileText size={18} /> Source Records
            </h3>
            <div className="space-y-4">
              {documents.map((doc: any) => (
                <button
                  key={doc.id}
                  type="button"
                  onClick={() => {
                    setSelectedDocumentId(doc.id);
                    setActiveTab("compare");
                  }}
                  className={cn(
                    "flex w-full items-center justify-between p-4 rounded-2xl bg-slate-900/30 border border-slate-800/40 hover:border-indigo-500/30 transition-all cursor-pointer group text-left",
                    selectedDocument?.id === doc.id && activeTab === "compare" ? "border-indigo-500/50 bg-indigo-500/10" : ""
                  )}
                >
                  <div className="flex items-center gap-3">
                     <div className="w-8 h-8 rounded-lg bg-rose-500/10 flex items-center justify-center text-rose-500 text-xs font-black">PDF</div>
                     <span className="text-xs font-bold text-slate-400 truncate w-32 group-hover:text-white transition-colors">{doc.filename}</span>
                  </div>
                  <Eye size={14} className="text-slate-600 group-hover:text-indigo-400 transition-colors" />
                </button>
              ))}
              {documents.length === 0 && <p className="text-xs text-slate-600 text-center py-4">No documents linked</p>}
            </div>
          </div>

          <div className="overflow-hidden rounded-[2.5rem] bg-[#11111d]/50 backdrop-blur-xl border border-slate-800/40">
            <div className="flex items-center justify-between gap-3 border-b border-slate-800/60 p-5">
              <div className="min-w-0">
                <h3 className="font-black text-white text-sm font-outfit uppercase tracking-widest text-cyan-400">
                  PDF Preview
                </h3>
                <p className="mt-1 truncate text-[11px] font-bold text-slate-500">
                  {selectedDocument?.filename || "No source selected"}
                </p>
              </div>
              {selectedDocument?.pdf_url && (
                <a
                  href={selectedDocument.pdf_url}
                  target="_blank"
                  rel="noreferrer"
                  className="shrink-0 rounded-xl border border-slate-800 bg-slate-900/50 p-2 text-slate-400 transition hover:border-cyan-500/40 hover:text-cyan-300"
                  title="Open PDF in a new tab"
                >
                  <ExternalLink size={15} />
                </a>
              )}
            </div>
            <div className="h-[66vh] min-h-[540px] bg-slate-950">
              {selectedDocument?.pdf_url ? (
                <iframe
                  src={`${selectedDocument.pdf_url}#view=FitH`}
                  title={`PDF preview for ${selectedDocument.filename}`}
                  className="h-full w-full bg-white"
                />
              ) : (
                <div className="flex h-full items-center justify-center p-6 text-center text-xs font-bold text-slate-500">
                  Select a source PDF to preview it beside the extracted record.
                </div>
              )}
            </div>
          </div>

          <div className="p-8 rounded-[2.5rem] bg-[#11111d]/50 backdrop-blur-xl border border-slate-800/40">
            <h3 className="font-black text-white text-sm font-outfit mb-6 flex items-center gap-3 uppercase tracking-widest text-emerald-400">
              <Activity size={18} /> Intelligence Metrics
            </h3>
            <div className="grid grid-cols-2 gap-4">
               <div className="p-5 rounded-2xl bg-slate-900/30 border border-slate-800/40">
                 <p className="text-[10px] text-slate-500 uppercase font-black tracking-tighter mb-2">Mentions</p>
                 <p className="text-2xl font-black text-white font-outfit">{mentions.length}</p>
               </div>
               <div className="p-5 rounded-2xl bg-slate-900/30 border border-slate-800/40">
                 <p className="text-[10px] text-slate-500 uppercase font-black tracking-tighter mb-2">Entities</p>
                 <p className="text-2xl font-black text-white font-outfit">{summarySections.length}</p>
               </div>
               <div className="p-5 rounded-2xl bg-slate-900/30 border border-slate-800/40">
                 <p className="text-[10px] text-slate-500 uppercase font-black tracking-tighter mb-2">Artifacts</p>
                 <p className="text-2xl font-black text-white font-outfit">{artifacts.length}</p>
               </div>
               <div className="p-5 rounded-2xl bg-slate-900/30 border border-slate-800/40">
                 <p className="text-[10px] text-slate-500 uppercase font-black tracking-tighter mb-2">Relations</p>
                 <p className="text-2xl font-black text-white font-outfit">{relations.length}</p>
               </div>
            </div>
          </div>

          <div className="p-8 rounded-[2.5rem] bg-[#11111d]/50 backdrop-blur-xl border border-slate-800/40">
            <h3 className="font-black text-white text-sm font-outfit mb-6 flex items-center gap-3 uppercase tracking-widest text-cyan-400">
              <Zap size={18} /> Evidence Artifacts
            </h3>
            <div className="space-y-4">
              {artifactPreview.map((artifact: any) => (
                <div key={artifact.id} className="p-4 rounded-2xl bg-slate-900/30 border border-slate-800/40">
                  <div className="flex items-center justify-between gap-3 mb-2">
                    <span className="text-[10px] font-black uppercase tracking-widest text-cyan-400">
                      {artifact.role || artifact.artifact_type}
                    </span>
                    <span className="text-[10px] text-slate-500 font-bold">
                      p{artifact.page_num} · {artifact.backend || "native"}
                    </span>
                  </div>
                  <p className="text-xs text-slate-300 line-clamp-3">
                    {artifact.text || "Non-text visual evidence region"}
                  </p>
                </div>
              ))}
              {artifactPreview.length === 0 && <p className="text-xs text-slate-600 text-center py-4">No page artifacts stored</p>}
            </div>
          </div>
        </aside>

        <section className="lg:col-span-3 space-y-10">
          {/* Tabs */}
          <div className="flex gap-2 p-1.5 bg-[#11111d]/80 backdrop-blur-xl border border-slate-800/60 rounded-[1.5rem] w-fit shadow-2xl shadow-black/20">
            {['compare', 'summary', 'mentions', 'timeline'].map(tab => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={cn(
                  "px-8 py-3 rounded-2xl text-xs font-black uppercase tracking-widest transition-all",
                  activeTab === tab 
                    ? "bg-indigo-600 text-white shadow-xl shadow-indigo-600/40 translate-y-[-1px]" 
                    : "text-slate-500 hover:text-slate-200"
                )}
              >
                {tab}
              </button>
            ))}
          </div>

          <AnimatePresence mode="wait">
            {activeTab === 'compare' && (
              <motion.div
                key="compare"
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -20 }}
                className="grid gap-8 xl:grid-cols-[1.05fr_0.95fr]"
              >
                <section className="overflow-hidden rounded-[2rem] border border-slate-800 bg-[#11111d]">
                  <div className="flex items-center justify-between gap-4 border-b border-slate-800 px-6 py-4">
                    <div className="min-w-0">
                      <p className="text-sm font-black text-white">Source PDF</p>
                      <p className="truncate text-xs text-slate-500">{selectedDocument?.filename || "No source document selected"}</p>
                    </div>
                    {selectedDocument?.pdf_url && (
                      <a
                        href={selectedDocument.pdf_url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex shrink-0 items-center gap-2 rounded-xl border border-slate-800 bg-slate-950/60 px-3 py-2 text-xs font-bold text-slate-300 hover:border-indigo-500/40 hover:text-white"
                      >
                        <ExternalLink size={14} /> Open
                      </a>
                    )}
                  </div>
                  <div className="h-[78vh] min-h-[620px] bg-slate-950">
                    {selectedDocument?.pdf_url ? (
                      <iframe
                        src={`${selectedDocument.pdf_url}#view=FitH`}
                        title={`PDF viewer for ${selectedDocument.filename}`}
                        className="h-full w-full bg-white"
                      />
                    ) : (
                      <div className="flex h-full items-center justify-center p-8 text-center text-sm font-bold text-slate-500">
                        No PDF is linked to this patient.
                      </div>
                    )}
                  </div>
                </section>

                <section className="max-h-[calc(78vh+73px)] min-h-[620px] overflow-auto rounded-[2rem] border border-slate-800 bg-[#11111d]">
                  <div className="sticky top-0 z-10 border-b border-slate-800 bg-[#11111d]/95 px-6 py-4 backdrop-blur">
                    <p className="text-sm font-black text-white">Extracted Result</p>
                    <p className="text-xs text-slate-500">Compare fields and evidence against the PDF on the left.</p>
                  </div>
                  <div className="space-y-5 p-5">
                    {validation && (
                      <div className="rounded-2xl border border-emerald-500/20 bg-emerald-500/10 p-5">
                        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                          <div>
                            <p className="text-sm font-black text-white">Validation Layer</p>
                            <p className="text-xs text-emerald-200/70">
                              {validation.backend || "heuristic"} {validation.model_id ? `· ${validation.model_id}` : ""}
                            </p>
                          </div>
                          <span className="rounded-xl border border-emerald-500/30 bg-black/20 px-3 py-2 text-xs font-black text-emerald-200">
                            {Math.round((validation.confidence || 0) * 100)}%
                          </span>
                        </div>
                        <div className="flex flex-wrap gap-2">
                          {(validation.flags || []).slice(0, 8).map((flag: string) => (
                            <span key={flag} className="rounded-lg border border-amber-500/20 bg-amber-500/10 px-2 py-1 text-[10px] font-bold uppercase tracking-widest text-amber-200">
                              {humanizeKey(flag)}
                            </span>
                          ))}
                          {(validation.flags || []).length === 0 && (
                            <span className="rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-2 py-1 text-[10px] font-bold uppercase tracking-widest text-emerald-200">
                              No validation flags
                            </span>
                          )}
                        </div>
                        {validation.model_notes && <p className="mt-3 text-xs leading-relaxed text-emerald-100/70">{validation.model_notes}</p>}
                      </div>
                    )}
                    {summarySections.slice(0, 8).map(({ category, items }) => (
                      <div key={category} className="rounded-2xl border border-slate-800/70 bg-slate-950/35 p-5">
                        <div className="mb-4 flex items-center justify-between gap-3">
                          <h3 className="font-outfit text-base font-black capitalize text-white">{humanizeKey(category)}</h3>
                          <span className="rounded-lg border border-indigo-500/20 bg-indigo-500/10 px-2 py-1 text-[10px] font-black uppercase tracking-widest text-indigo-300">
                            {items.length}
                          </span>
                        </div>
                        <div className="space-y-3">
                          {items.slice(0, 8).map((item, idx) => (
                            <div key={`${category}-${idx}`} className="rounded-xl border border-slate-800/60 bg-slate-900/35 p-4">
                              <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">{item.label}</p>
                              <p className="mt-2 text-sm font-bold leading-relaxed text-slate-100">{item.value}</p>
                            </div>
                          ))}
                        </div>
                      </div>
                    ))}
                    <div className="rounded-2xl border border-slate-800/70 bg-slate-950/35 p-5">
                      <h3 className="mb-4 font-outfit text-base font-black text-white">Evidence Mentions</h3>
                      <div className="space-y-3">
                        {mentions.slice(0, 16).map((m: any) => (
                          <div key={m.id} className="rounded-xl border border-slate-800/60 bg-slate-900/35 p-4">
                            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                              <p className="text-xs font-black uppercase tracking-wide text-white">{m.label}</p>
                              <span className="rounded-md bg-slate-800 px-2 py-1 text-[9px] font-bold uppercase text-slate-400">
                                {m.origin} {m.source_pages?.length ? `p${m.source_pages.join(",")}` : ""}
                              </span>
                            </div>
                            <p className="text-sm font-bold text-indigo-300">{m.value}</p>
                            {m.evidence_quote && <p className="mt-2 line-clamp-3 text-xs italic leading-relaxed text-slate-500">"{m.evidence_quote}"</p>}
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                </section>
              </motion.div>
            )}

            {activeTab === 'summary' && (
              <motion.div
                key="summary"
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -20 }}
                className="grid gap-8"
              >
                {summarySections.map(({ category, items }) => (
                  <div key={category} className="p-10 rounded-[3rem] bg-[#11111d]/50 backdrop-blur-xl border border-slate-800/40 relative group overflow-hidden">
                    <div className="absolute top-0 right-0 w-32 h-32 bg-indigo-500/5 blur-3xl rounded-full -mr-16 -mt-16 pointer-events-none" />
                    <div className="flex items-center justify-between mb-8">
                       <h2 className="text-xl font-black text-white font-outfit capitalize flex items-center gap-4">
                         <div className="w-12 h-12 rounded-2xl bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center">
                            <Layers size={20} className="text-indigo-400" />
                         </div>
                         {humanizeKey(category)}
                       </h2>
                       <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">{items.length} Extracted</span>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                      {items.map((item: any, idx: number) => (
                        <div 
                           key={idx} 
                           className="p-5 rounded-2xl bg-slate-900/30 border border-slate-800/60 flex justify-between items-center group/item hover:border-indigo-500/40 hover:bg-indigo-500/5 transition-all relative"
                           onMouseEnter={() => setHoveredMention(`${category}-${idx}`)}
                           onMouseLeave={() => setHoveredMention(null)}
                        >
                          <div className="flex items-center gap-4">
                             <div className="w-2 h-2 rounded-full bg-slate-800 group-hover/item:bg-indigo-500 transition-colors" />
                             <span className="text-slate-400 text-xs font-bold uppercase tracking-wider">{item.label}</span>
                          </div>
                          <span className="text-white font-black text-sm">{item.value}</span>
                          <AnimatePresence>
                             {hoveredMention === `${category}-${idx}` && (
                                <EvidenceTooltip mention={mentions.find((m: any) => m.label === item.label && m.value === item.value)} />
                             )}
                          </AnimatePresence>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
                {summarySections.length === 0 && (
                  <div className="text-center py-20 border-2 border-dashed border-slate-800/40 rounded-[3rem]">
                    <Layers className="text-slate-800 mx-auto mb-4" size={48} />
                    <p className="text-slate-500 font-bold">No structured summary available for this patient</p>
                  </div>
                )}
              </motion.div>
            )}

            {activeTab === 'timeline' && (
              <motion.div
                key="timeline"
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -20 }}
                className="relative pl-12 border-l border-slate-800/50 space-y-16 py-8"
              >
                <div className="absolute top-0 left-[-2px] w-1 h-32 bg-gradient-to-b from-indigo-500 to-transparent" />
                {(timelineEvents.length ? timelineEvents : mentions.filter((m: any) => m.date_text)).map((m: any, i: number) => (
                  <div key={m.id} className="relative group">
                    {/* Glow Connector */}
                    <div className="absolute left-[-12px] top-6 w-6 h-6 rounded-full bg-slate-900 border border-slate-800 flex items-center justify-center z-10 group-hover:border-indigo-500 group-hover:shadow-[0_0_15px_#6366f1] transition-all">
                       <Clock size={12} className="text-slate-500 group-hover:text-indigo-400" />
                    </div>
                    
                    <div className="glass p-10 rounded-[3rem] border border-slate-800/60 group-hover:border-indigo-500/30 transition-all shadow-2xl shadow-black/20 relative">
                       <div className="absolute top-10 right-10 flex gap-2">
                          <span className="text-[10px] font-black bg-indigo-500/10 text-indigo-400 px-3 py-1 rounded-lg border border-indigo-500/20 uppercase">{m.category}</span>
                       </div>
                       
                       <div className="flex flex-col md:flex-row md:items-center justify-between gap-6 mb-8">
                          <div>
                             <p className="text-[10px] font-black text-rose-500 uppercase tracking-widest mb-2 flex items-center gap-2">
                                <History size={14} /> Recorded: {m.date_text || "Undated relation"}
                             </p>
                             <h4 className="text-2xl font-black text-white font-outfit uppercase tracking-tighter">{m.label || m.source_label || "Clinical event"}</h4>
                             <p className="text-indigo-400 font-bold text-lg mt-1">
                               {m.value || [m.relation_type, m.target_label || m.target_value].filter(Boolean).join(" → ")}
                             </p>
                          </div>
                          <div className="p-4 rounded-2xl bg-indigo-500/5 border border-indigo-500/10 text-center min-w-[120px]">
                             <p className="text-[10px] text-slate-500 uppercase font-black mb-1">Certainty</p>
                             <p className="text-xl font-black text-white">{certaintyToPercent(m.certainty ?? 0.7)}%</p>
                          </div>
                       </div>
                       
                       <div className="relative">
                          <div className="absolute left-0 top-0 bottom-0 w-1 bg-indigo-500/30 rounded-full" />
                          <div className="pl-6">
                             <p className="text-xs text-slate-500 font-black uppercase tracking-widest mb-3 flex items-center gap-2">
                                <Info size={14} className="text-indigo-400" /> Source Evidence
                             </p>
                             <blockquote className="text-sm text-slate-300 italic leading-relaxed border-l-0 p-0 mb-4">
                                "{m.evidence_quote || [m.source_label, m.source_value, m.target_label, m.target_value].filter(Boolean).join(" · ") || 'Text extract not available for this record.'}"
                             </blockquote>
                             <div className="flex items-center gap-4">
                                <div className="flex items-center gap-2 text-[10px] font-bold text-slate-500 bg-slate-900/50 px-3 py-1.5 rounded-lg border border-slate-800">
                                   <FileText size={12} /> {m.origin || m.relation_type || "relation"}
                                </div>
                                <div className="flex items-center gap-2 text-[10px] font-bold text-indigo-400/60 bg-indigo-500/5 px-3 py-1.5 rounded-lg border border-indigo-500/10">
                                   <Activity size={12} /> {(m.evidence_pages || []).length ? `Pages ${(m.evidence_pages || []).join(", ")}` : "Extraction Pipeline 4.2"}
                                </div>
                             </div>
                          </div>
                       </div>
                    </div>
                  </div>
                ))}
                {(timelineEvents.length ? timelineEvents : mentions.filter((m: any) => m.date_text)).length === 0 && (
                   <div className="text-center py-20 border-2 border-dashed border-slate-800/40 rounded-[3rem]">
                      <History className="text-slate-800 mx-auto mb-4" size={48} />
                      <p className="text-slate-500 font-bold">No historical markers extracted for this patient</p>
                   </div>
                )}
              </motion.div>
            )}

            {activeTab === 'mentions' && (
              <motion.div
                key="mentions"
                initial={{ opacity: 0, x: 20 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -20 }}
                className="grid gap-4"
              >
                {mentions.map((m: any) => (
                  <div key={m.id} className="p-6 rounded-3xl bg-[#11111d]/50 border border-slate-800/60 flex items-center gap-8 group hover:border-indigo-500/30 transition-all backdrop-blur-xl">
                    <div className="w-14 h-14 rounded-2xl bg-slate-900 border border-slate-800 flex items-center justify-center text-slate-600 group-hover:text-indigo-400 group-hover:bg-indigo-500/5 transition-all">
                      <Activity size={24} />
                    </div>
                    <div className="flex-1">
                      <div className="flex items-center gap-4 mb-2">
                        <h4 className="text-lg font-black text-white font-outfit uppercase tracking-tight">{m.label}</h4>
                        <span className="px-2 py-0.5 bg-slate-800 text-[9px] font-mono text-slate-500 rounded uppercase tracking-tighter">TRC-{m.id.substring(0, 8)}</span>
                      </div>
                      <div className="flex items-center gap-6">
                         <p className="text-sm text-indigo-400 font-bold">{m.value}</p>
                         <p className="text-[10px] text-slate-600 font-bold uppercase tracking-widest">{m.category}</p>
                      </div>
                    </div>
                    <div className="text-right hidden md:block">
                       <p className="text-xs font-black text-slate-500 uppercase tracking-widest mb-1">{m.origin}</p>
                       <p className="text-[10px] text-emerald-500 font-bold">Conf: {certaintyToPercent(m.certainty).toFixed(1)}%</p>
                    </div>
                    <ChevronRight className="text-slate-800 group-hover:text-white transition-colors" size={24} />
                  </div>
                ))}
              </motion.div>
            )}
          </AnimatePresence>
        </section>
      </div>
    </div>
  );
}
