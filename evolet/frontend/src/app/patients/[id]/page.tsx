"use client";

/* eslint-disable @typescript-eslint/no-explicit-any */

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  ArrowLeft,
  Activity,
  CheckCircle2,
  Download,
  ExternalLink,
  FileText,
  Layers,
  ShieldCheck,
  Sparkles,
  Stethoscope,
  Tag,
  TextSearch,
} from "lucide-react";
import { getPatientDetail } from "@/lib/api";
import { cn } from "@/lib/utils";

type SectionItem = {
  label?: string;
  value?: string;
  normalized_value?: string;
  category?: string;
  evidence_quote?: string;
  source_pages?: number[];
  certainty?: string | number;
  parsed?: Record<string, unknown>;
};

type Section = {
  title?: string;
  count?: number;
  items?: SectionItem[];
};

const TABS = [
  { id: "overview", label: "Overview", icon: Sparkles },
  { id: "clinical", label: "Clinical", icon: Stethoscope },
  { id: "evidence", label: "Evidence", icon: TextSearch },
  { id: "quality", label: "Quality", icon: ShieldCheck },
  { id: "source", label: "Source PDF", icon: FileText },
] as const;

function humanize(value: string | undefined) {
  return (value || "unknown").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function asArray<T>(value: T[] | undefined | null): T[] {
  return Array.isArray(value) ? value : [];
}

function confidence(value: unknown) {
  if (typeof value === "number") {
    return value > 1 ? Math.round(value) : Math.round(value * 100);
  }
  if (typeof value === "string") {
    const numeric = Number(value);
    if (!Number.isNaN(numeric)) return numeric > 1 ? Math.round(numeric) : Math.round(numeric * 100);
    return { confirmed: 98, high: 92, medium: 75, low: 55, unknown: 50 }[value.toLowerCase()] ?? 50;
  }
  return 50;
}

function compactValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "Not found";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return `${value.length} items`;
  return "Structured";
}

function Field({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="rounded-2xl border border-slate-800/70 bg-slate-950/35 p-4">
      <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">{label}</p>
      <p className="mt-2 text-sm font-bold leading-relaxed text-slate-100">{compactValue(value)}</p>
    </div>
  );
}

function MentionCard({ item }: { item: SectionItem }) {
  return (
    <article className="rounded-2xl border border-slate-800/70 bg-slate-950/35 p-5">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-black uppercase tracking-widest text-indigo-300">
            {humanize(item.category || item.label)}
          </p>
          <h4 className="mt-2 text-base font-black text-white">{item.label || "Extracted item"}</h4>
        </div>
        <span className="rounded-xl border border-emerald-500/20 bg-emerald-500/10 px-3 py-1.5 text-[10px] font-black text-emerald-200">
          {confidence(item.certainty)}%
        </span>
      </div>
      <p className="text-sm font-bold leading-relaxed text-slate-200">{item.normalized_value || item.value || "No value extracted"}</p>
      {item.evidence_quote && (
        <p className="mt-4 line-clamp-4 rounded-xl border-l-2 border-indigo-500/60 bg-indigo-500/5 p-4 text-xs italic leading-relaxed text-slate-400">
          &quot;{item.evidence_quote}&quot;
        </p>
      )}
      {asArray(item.source_pages).length > 0 && (
        <p className="mt-3 text-[10px] font-bold uppercase tracking-widest text-slate-500">
          Pages {asArray(item.source_pages).join(", ")}
        </p>
      )}
    </article>
  );
}

export default function PatientDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<(typeof TABS)[number]["id"]>("overview");
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    getPatientDetail(id)
      .then((res) => setData(res.data))
      .finally(() => setLoading(false));
  }, [id]);

  const patient = data?.patient;
  const finalRecord = data?.final_record;
  const grouped = finalRecord?.grouped || {};
  const stats = finalRecord?.stats || grouped?.stats || {};
  const validation = stats?.validation || grouped?.validation || {};
  const documents = asArray<any>(data?.documents);
  const mentions = asArray<any>(data?.mentions);
  const sections = (grouped?.sections || {}) as Record<string, Section>;
  const majorInfo = grouped?.major_info || {};
  const documentProfile = grouped?.document_profile || {};
  const quality = grouped?.quality_checks || validation || {};
  const allExtractedContent = grouped?.all_extracted_content || {};
  const rawPages = asArray<any>(allExtractedContent?.pages);
  const importantRawLines = asArray<string>(allExtractedContent?.important_raw_lines);
  const selectedDocument = documents.find((doc) => doc.id === selectedDocumentId) || documents[0];

  const sectionList = Object.entries(sections).filter(([, section]) => asArray(section.items).length > 0);
  const allSectionItems = sectionList.flatMap(([key, section]) =>
    asArray(section.items).map((item) => ({ ...item, category: item.category || key })),
  );

  if (loading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="h-14 w-14 animate-spin rounded-full border-4 border-indigo-500/20 border-t-indigo-500" />
      </div>
    );
  }

  return (
    <main className="mx-auto min-h-screen max-w-[1600px] space-y-10 p-6 lg:p-10">
      <nav className="flex flex-wrap items-center justify-between gap-4">
        <Link href="/patients" className="flex items-center gap-2 rounded-xl border border-slate-800 bg-slate-950/50 px-4 py-2 text-sm font-bold text-slate-400 hover:text-white">
          <ArrowLeft size={18} /> Patient Explorer
        </Link>
        <button className="flex items-center gap-2 rounded-xl border border-slate-800 bg-slate-950/50 px-4 py-2 text-sm font-bold text-slate-300 hover:text-white">
          <Download size={16} /> Export JSON
        </button>
      </nav>

      <header className="rounded-[2rem] border border-slate-800/60 bg-[#11111d]/70 p-8">
        <div className="flex flex-col gap-8 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="mb-4 flex flex-wrap items-center gap-3">
              <span className="rounded-xl border border-indigo-500/20 bg-indigo-500/10 px-3 py-1.5 text-[10px] font-black uppercase tracking-widest text-indigo-300">
                Doc OCR Record
              </span>
              <span className="rounded-xl border border-emerald-500/20 bg-emerald-500/10 px-3 py-1.5 text-[10px] font-black uppercase tracking-widest text-emerald-300">
                {validation?.status || "validated"}
              </span>
            </div>
            <h1 className="font-outfit text-4xl font-black tracking-tight text-white">{patient?.display_name || "Unknown record"}</h1>
            <p className="mt-4 flex items-center gap-2 text-sm font-bold text-slate-400">
              <Tag size={15} className="text-indigo-400" /> {patient?.code || "NO_ID"}
            </p>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <Field label="Mentions" value={stats?.mentions_after_merge ?? mentions.length} />
            <Field label="Sections" value={sectionList.length} />
            <Field label="Confidence" value={`${Math.round((validation?.confidence || 0.88) * 100)}%`} />
          </div>
        </div>
      </header>

      <div className="grid gap-8 lg:grid-cols-[300px_1fr]">
        <aside className="space-y-6">
          <section className="rounded-[2rem] border border-slate-800/60 bg-[#11111d]/70 p-5">
            <h2 className="mb-4 flex items-center gap-2 text-xs font-black uppercase tracking-widest text-slate-400">
              <FileText size={16} /> Source PDFs
            </h2>
            <div className="space-y-3">
              {documents.map((doc) => (
                <button
                  key={doc.id}
                  type="button"
                  onClick={() => {
                    setSelectedDocumentId(doc.id);
                    setActiveTab("source");
                  }}
                  className={cn(
                    "w-full rounded-2xl border border-slate-800 bg-slate-950/35 p-4 text-left transition hover:border-indigo-500/40",
                    selectedDocument?.id === doc.id && "border-indigo-500/50 bg-indigo-500/10",
                  )}
                >
                  <p className="line-clamp-2 text-xs font-black text-slate-200">{doc.filename}</p>
                  <p className="mt-2 text-[10px] font-bold uppercase tracking-widest text-slate-500">
                    {doc.page_count || "?"} pages
                  </p>
                </button>
              ))}
            </div>
          </section>

          <section className="rounded-[2rem] border border-slate-800/60 bg-[#11111d]/70 p-5">
            <h2 className="mb-4 flex items-center gap-2 text-xs font-black uppercase tracking-widest text-slate-400">
              <Activity size={16} /> Pipeline
            </h2>
            <div className="space-y-3">
              <Field label="Schema" value={grouped?.schema_version || "adaptive"} />
              <Field label="Spell Check" value={quality?.spell_check?.status || "SymSpell + rules"} />
              <Field label="Validator" value={validation?.model_id || validation?.backend || "heuristic"} />
            </div>
          </section>
        </aside>

        <section className="space-y-6">
          <div className="flex flex-wrap gap-2 rounded-[1.5rem] border border-slate-800/60 bg-[#11111d]/80 p-2">
            {TABS.map(({ id: tabId, label, icon: Icon }) => (
              <button
                key={tabId}
                type="button"
                onClick={() => setActiveTab(tabId)}
                className={cn(
                  "flex items-center gap-2 rounded-2xl px-5 py-3 text-xs font-black uppercase tracking-widest transition",
                  activeTab === tabId ? "bg-indigo-600 text-white shadow-lg shadow-indigo-600/30" : "text-slate-500 hover:text-slate-200",
                )}
              >
                <Icon size={15} /> {label}
              </button>
            ))}
          </div>

          {activeTab === "overview" && (
            <div className="space-y-6">
              <section className="rounded-[2rem] border border-slate-800/60 bg-[#11111d]/70 p-7">
                <h2 className="mb-5 flex items-center gap-3 font-outfit text-xl font-black text-white">
                  <Sparkles className="text-indigo-400" /> Major Information
                </h2>
                <div className="grid gap-4 md:grid-cols-2">
                  <Field label="Primary finding" value={majorInfo?.primary_finding} />
                  <Field label="Current treatment" value={majorInfo?.current_treatment} />
                  <Field label="Investigations" value={majorInfo?.investigation_summary} />
                  <Field label="Plan / follow up" value={majorInfo?.plan_or_follow_up} />
                  <Field label="Extracted text" value={`${allExtractedContent?.source_text_word_count || 0} words`} />
                  <Field label="Full text stored" value={allExtractedContent?.stored_full_text ? "Yes" : "No"} />
                </div>
              </section>
              <section className="rounded-[2rem] border border-slate-800/60 bg-[#11111d]/70 p-7">
                <h2 className="mb-5 flex items-center gap-3 font-outfit text-xl font-black text-white">
                  <Layers className="text-cyan-400" /> Document Profile
                </h2>
                <div className="grid gap-4 md:grid-cols-3">
                  <Field label="Document type" value={documentProfile?.document_type} />
                  <Field label="Clinical domain" value={documentProfile?.clinical_domain} />
                  <Field label="Detected categories" value={grouped?.document_summary?.categories?.length || sectionList.length} />
                </div>
              </section>
            </div>
          )}

          {activeTab === "clinical" && (
            <div className="space-y-6">
              {sectionList.map(([key, section]) => (
                <section key={key} className="rounded-[2rem] border border-slate-800/60 bg-[#11111d]/70 p-7">
                  <div className="mb-5 flex items-center justify-between gap-3">
                    <h2 className="font-outfit text-xl font-black text-white">{section.title || humanize(key)}</h2>
                    <span className="rounded-xl border border-indigo-500/20 bg-indigo-500/10 px-3 py-1 text-[10px] font-black text-indigo-300">
                      {section.count ?? asArray(section.items).length}
                    </span>
                  </div>
                  <div className="grid gap-4 xl:grid-cols-2">
                    {asArray(section.items).map((item, index) => (
                      <MentionCard key={`${key}-${index}`} item={{ ...item, category: key }} />
                    ))}
                  </div>
                </section>
              ))}
              {sectionList.length === 0 && (
                <section className="rounded-[2rem] border border-slate-800/60 bg-[#11111d]/70 p-7">
                  <h2 className="mb-5 font-outfit text-xl font-black text-white">Full Extracted Text</h2>
                  <div className="space-y-4">
                    {rawPages.slice(0, 8).map((page, index) => (
                      <article key={`${page.document || "page"}-${page.page_num || index}`} className="rounded-2xl border border-slate-800 bg-slate-950/35 p-5">
                        <p className="mb-3 text-[10px] font-black uppercase tracking-widest text-slate-500">
                          Page {page.page_num || index + 1} · {page.word_count || 0} words
                        </p>
                        <pre className="max-h-64 whitespace-pre-wrap text-xs leading-relaxed text-slate-300">{page.text || "No text extracted."}</pre>
                      </article>
                    ))}
                  </div>
                </section>
              )}
            </div>
          )}

          {activeTab === "evidence" && (
            <section className="rounded-[2rem] border border-slate-800/60 bg-[#11111d]/70 p-7">
              <h2 className="mb-5 flex items-center gap-3 font-outfit text-xl font-black text-white">
                <TextSearch className="text-amber-300" /> Evidence Backed Items
              </h2>
              <div className="grid gap-4 xl:grid-cols-2">
                {(allSectionItems.length ? allSectionItems : mentions).map((item, index) => (
                  <MentionCard key={item.id || index} item={item} />
                ))}
              </div>
              {importantRawLines.length > 0 && (
                <div className="mt-6 rounded-2xl border border-amber-500/20 bg-amber-500/5 p-5">
                  <h3 className="mb-3 text-xs font-black uppercase tracking-widest text-amber-200">Important Raw Lines</h3>
                  <ul className="space-y-2 text-xs leading-relaxed text-slate-300">
                    {importantRawLines.slice(0, 30).map((line, index) => (
                      <li key={`${line}-${index}`}>{line}</li>
                    ))}
                  </ul>
                </div>
              )}
            </section>
          )}

          {activeTab === "quality" && (
            <div className="grid gap-6 xl:grid-cols-2">
              <section className="rounded-[2rem] border border-emerald-500/20 bg-emerald-500/10 p-7">
                <h2 className="mb-5 flex items-center gap-3 font-outfit text-xl font-black text-white">
                  <CheckCircle2 className="text-emerald-300" /> Validation
                </h2>
                <div className="grid gap-4">
                  <Field label="Status" value={validation?.status || "completed"} />
                  <Field label="Backend" value={validation?.backend} />
                  <Field label="Model" value={validation?.model_id} />
                  <Field label="Confidence" value={`${Math.round((validation?.confidence || 0) * 100)}%`} />
                </div>
                <div className="mt-5 flex flex-wrap gap-2">
                  {asArray<string>(validation?.flags).map((flag) => (
                    <span key={flag} className="rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-1 text-[10px] font-black uppercase tracking-widest text-amber-200">
                      {humanize(flag)}
                    </span>
                  ))}
                  {asArray<string>(validation?.flags).length === 0 && (
                    <span className="rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3 py-1 text-[10px] font-black uppercase tracking-widest text-emerald-200">
                      No validation flags
                    </span>
                  )}
                </div>
              </section>
              <section className="rounded-[2rem] border border-slate-800/60 bg-[#11111d]/70 p-7">
                <h2 className="mb-5 flex items-center gap-3 font-outfit text-xl font-black text-white">
                  <ShieldCheck className="text-cyan-300" /> Spell And Text Checks
                </h2>
                <div className="grid gap-4">
                  <Field label="Status" value={quality?.spell_check?.status || "completed"} />
                  <Field label="Libraries" value={quality?.spell_check?.libraries || "SymSpell, RapidFuzz, optional transformer"} />
                  <Field label="Corrected mentions" value={quality?.spell_check?.corrected_mentions ?? stats?.text_corrections_applied ?? 0} />
                  <Field label="Transformer layer" value={quality?.spell_check?.transformer_layer || "available when enabled"} />
                  <Field label="Raw pages stored" value={rawPages.length} />
                  <Field label="Text coverage" value={allExtractedContent?.status || "unknown"} />
                </div>
              </section>
            </div>
          )}

          {activeTab === "source" && (
            <section className="overflow-hidden rounded-[2rem] border border-slate-800/60 bg-[#11111d]/70">
              <div className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-800/60 p-5">
                <div>
                  <h2 className="font-outfit text-xl font-black text-white">Source PDF</h2>
                  <p className="mt-1 text-xs font-bold text-slate-500">{selectedDocument?.filename || "No source selected"}</p>
                </div>
                {selectedDocument?.pdf_url && (
                  <a href={selectedDocument.pdf_url} target="_blank" rel="noreferrer" className="flex items-center gap-2 rounded-xl border border-slate-800 bg-slate-950/50 px-4 py-2 text-xs font-bold text-slate-300 hover:text-white">
                    <ExternalLink size={15} /> Open PDF
                  </a>
                )}
              </div>
              <div className="grid min-h-[720px] lg:grid-cols-[1.1fr_0.9fr]">
                <div className="bg-slate-950">
                  {selectedDocument?.pdf_url ? (
                    <iframe src={`${selectedDocument.pdf_url}#view=FitH`} title={selectedDocument.filename} className="h-full min-h-[720px] w-full bg-white" />
                  ) : (
                    <div className="flex h-full items-center justify-center text-sm font-bold text-slate-500">No PDF linked.</div>
                  )}
                </div>
                <div className="max-h-[720px] space-y-4 overflow-auto border-l border-slate-800/60 p-5">
                  <h3 className="text-sm font-black uppercase tracking-widest text-slate-400">Extracted From Source</h3>
                  {(allSectionItems.length ? allSectionItems : mentions).slice(0, 20).map((item, index) => (
                    <MentionCard key={item.id || index} item={item} />
                  ))}
                  {rawPages.length > 0 && (
                    <div className="space-y-4 pt-4">
                      <h3 className="text-sm font-black uppercase tracking-widest text-slate-400">Full Text By Page</h3>
                      {rawPages.slice(0, 12).map((page, index) => (
                        <article key={`${page.document || "source"}-${page.page_num || index}`} className="rounded-2xl border border-slate-800 bg-slate-950/35 p-4">
                          <p className="mb-3 text-[10px] font-black uppercase tracking-widest text-slate-500">
                            Page {page.page_num || index + 1} · {page.selected_source || "text"}
                          </p>
                          <pre className="max-h-80 whitespace-pre-wrap text-xs leading-relaxed text-slate-300">{page.text || "No text extracted."}</pre>
                        </article>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </section>
          )}
        </section>
      </div>
    </main>
  );
}
