"use client";

/* eslint-disable @typescript-eslint/no-explicit-any */

import React, { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  ArrowLeft,
  CheckCircle2,
  Database,
  Download,
  ExternalLink,
  FileText,
  GitBranch,
  Layers,
  ShieldCheck,
  Tag,
} from "lucide-react";
import { getPatientDetail } from "@/lib/api";
import { cn } from "@/lib/utils";

type SectionItem = {
  id?: string | number;
  label?: string;
  value?: string;
  normalized_value?: string;
  category?: string;
  evidence_quote?: string;
  source_pages?: number[];
  certainty?: string | number;
  attributes?: Record<string, unknown>;
};

type Section = {
  title?: string;
  count?: number;
  items?: SectionItem[];
};

function asArray<T>(value: T[] | undefined | null): T[] {
  return Array.isArray(value) ? value : [];
}

function humanize(value: string | undefined) {
  return (value || "unknown")
    .replace(/_/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function confidence(value: unknown) {
  if (typeof value === "number") return value > 1 ? Math.round(value) : Math.round(value * 100);
  if (typeof value === "string") {
    const parsed = Number(value);
    if (!Number.isNaN(parsed)) return parsed > 1 ? Math.round(parsed) : Math.round(parsed * 100);
    return { confirmed: 98, high: 92, medium: 75, low: 55, unknown: 50 }[value.toLowerCase()] ?? 70;
  }
  return 70;
}

function displayValue(item: SectionItem) {
  return item.normalized_value || item.value || "Not extracted";
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="border-b border-slate-800/70 py-3 last:border-b-0">
      <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">{label}</p>
      <div className="mt-1 text-sm font-bold leading-relaxed text-slate-100">{value || "Not found"}</div>
    </div>
  );
}

function SchemaItem({ item }: { item: SectionItem }) {
  const pages = asArray(item.source_pages);
  return (
    <article className="rounded-lg border border-slate-800/70 bg-slate-950/35 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[10px] font-black uppercase tracking-widest text-indigo-300">{humanize(item.label)}</p>
          <p className="mt-2 break-words text-sm font-bold leading-relaxed text-white">{displayValue(item)}</p>
        </div>
        <span className="shrink-0 rounded-md border border-emerald-500/20 bg-emerald-500/10 px-2.5 py-1 text-[10px] font-black text-emerald-200">
          {confidence(item.certainty)}%
        </span>
      </div>
      {item.evidence_quote && (
        <p className="mt-3 line-clamp-3 border-l-2 border-indigo-500/60 pl-3 text-xs italic leading-relaxed text-slate-400">
          &quot;{item.evidence_quote}&quot;
        </p>
      )}
      {pages.length > 0 && (
        <p className="mt-3 text-[10px] font-black uppercase tracking-widest text-slate-500">Page {pages.join(", ")}</p>
      )}
    </article>
  );
}

function SchemaSection({ name, section }: { name: string; section: Section }) {
  const items = asArray(section.items);
  if (!items.length) return null;
  return (
    <section className="border-t border-slate-800/70 py-6 first:border-t-0 first:pt-0">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Schema section</p>
          <h2 className="mt-1 font-outfit text-xl font-black text-white">{section.title || humanize(name)}</h2>
        </div>
        <span className="rounded-md border border-indigo-500/20 bg-indigo-500/10 px-3 py-1 text-xs font-black text-indigo-200">
          {section.count ?? items.length}
        </span>
      </div>
      <div className="grid gap-3 xl:grid-cols-2">
        {items.map((item, index) => (
          <SchemaItem key={`${name}-${item.id || item.label || index}`} item={item} />
        ))}
      </div>
    </section>
  );
}

function SchemaTree({ sections }: { sections: [string, Section][] }) {
  if (!sections.length) {
    return <p className="text-sm font-bold text-slate-500">No schema tree available yet.</p>;
  }
  return (
    <div className="space-y-4">
      {sections.map(([name, section]) => {
        const items = asArray(section.items);
        return (
          <div key={name} className="relative pl-5">
            <div className="absolute bottom-2 left-1 top-2 w-px bg-slate-800" />
            <div className="relative">
              <div className="absolute -left-5 top-2 h-px w-4 bg-slate-800" />
              <p className="rounded-md border border-slate-800 bg-slate-950/40 px-3 py-2 text-sm font-black text-white">
                {section.title || humanize(name)}
                <span className="ml-2 text-xs font-bold text-slate-500">{items.length}</span>
              </p>
            </div>
            <div className="mt-2 space-y-2 pl-5">
              {items.slice(0, 10).map((item, index) => (
                <div key={`${name}-tree-${item.id || index}`} className="relative">
                  <div className="absolute -left-5 top-3 h-px w-4 bg-slate-800" />
                  <p className="rounded-md bg-slate-950/30 px-3 py-2 text-xs leading-relaxed text-slate-300">
                    <span className="font-black text-indigo-200">{humanize(item.label)}:</span> {displayValue(item)}
                  </p>
                </div>
              ))}
              {items.length > 10 && <p className="text-xs font-bold text-slate-500">+{items.length - 10} more fields</p>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function PatientDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
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
  const mentions = asArray<SectionItem>(data?.mentions);
  const sections = (grouped?.sections || {}) as Record<string, Section>;
  const documentsWithPdf = documents.filter((doc) => doc.pdf_url);
  const selectedDocument = documents.find((doc) => doc.id === selectedDocumentId) || documentsWithPdf[0] || documents[0];

  const sectionList = useMemo(
    () => Object.entries(sections).filter(([, section]) => asArray(section.items).length > 0),
    [sections],
  );
  const schemaItems = useMemo(
    () =>
      sectionList.flatMap(([name, section]) =>
        asArray(section.items).map((item) => ({ ...item, category: item.category || name })),
      ),
    [sectionList],
  );

  if (loading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="h-14 w-14 animate-spin rounded-full border-4 border-indigo-500/20 border-t-indigo-500" />
      </div>
    );
  }

  return (
    <main className="mx-auto min-h-screen max-w-[1760px] space-y-6 p-5 lg:p-8">
      <nav className="flex flex-wrap items-center justify-between gap-4">
        <Link
          href="/patients"
          className="flex items-center gap-2 rounded-lg border border-slate-800 bg-slate-950/50 px-4 py-2 text-sm font-bold text-slate-400 hover:text-white"
        >
          <ArrowLeft size={18} /> Patients
        </Link>
        <button className="flex items-center gap-2 rounded-lg border border-slate-800 bg-slate-950/50 px-4 py-2 text-sm font-bold text-slate-300 hover:text-white">
          <Download size={16} /> Export JSON
        </button>
      </nav>

      <header className="border-b border-slate-800/70 pb-6">
        <div className="flex flex-col gap-5 xl:flex-row xl:items-end xl:justify-between">
          <div>
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <span className="rounded-md border border-indigo-500/20 bg-indigo-500/10 px-3 py-1 text-[10px] font-black uppercase tracking-widest text-indigo-300">
                Adaptive Schema Record
              </span>
              <span className="rounded-md border border-emerald-500/20 bg-emerald-500/10 px-3 py-1 text-[10px] font-black uppercase tracking-widest text-emerald-300">
                {validation?.status || "validated"}
              </span>
            </div>
            <h1 className="font-outfit text-4xl font-black tracking-tight text-white">{patient?.display_name || "Unknown record"}</h1>
            <p className="mt-3 flex items-center gap-2 text-sm font-bold text-slate-400">
              <Tag size={15} className="text-indigo-400" /> {patient?.code || "NO_ID"}
            </p>
          </div>
          <div className="grid gap-3 sm:grid-cols-4">
            <Field label="Schema" value={grouped?.schema_version || "adaptive"} />
            <Field label="Sections" value={sectionList.length} />
            <Field label="Fields" value={schemaItems.length || mentions.length} />
            <Field label="Confidence" value={`${Math.round((validation?.confidence || 0.88) * 100)}%`} />
          </div>
        </div>
      </header>

      <div className="grid gap-6 xl:grid-cols-[430px_1fr]">
        <aside className="space-y-4 xl:sticky xl:top-6 xl:self-start">
          <section className="overflow-hidden rounded-lg border border-slate-800/70 bg-[#11111d]/75">
            <div className="flex items-center justify-between gap-3 border-b border-slate-800/70 p-4">
              <div>
                <h2 className="flex items-center gap-2 text-sm font-black uppercase tracking-widest text-slate-300">
                  <FileText size={16} /> PDF View
                </h2>
                <p className="mt-1 line-clamp-1 text-xs font-bold text-slate-500">{selectedDocument?.filename || "No PDF selected"}</p>
              </div>
              {selectedDocument?.pdf_url && (
                <a
                  href={selectedDocument.pdf_url}
                  target="_blank"
                  rel="noreferrer"
                  className="rounded-md border border-slate-800 bg-slate-950/50 p-2 text-slate-300 hover:text-white"
                  title="Open PDF"
                >
                  <ExternalLink size={16} />
                </a>
              )}
            </div>
            <div className="h-[980px] bg-slate-950">
              {selectedDocument?.pdf_url ? (
                <iframe src={`${selectedDocument.pdf_url}#view=FitH`} title={selectedDocument.filename} className="h-full w-full bg-white" />
              ) : (
                <div className="flex h-full items-center justify-center text-sm font-bold text-slate-500">No PDF linked.</div>
              )}
            </div>
          </section>

          {documents.length > 1 && (
            <section className="rounded-lg border border-slate-800/70 bg-[#11111d]/75 p-4">
              <h2 className="mb-3 text-xs font-black uppercase tracking-widest text-slate-400">Source files</h2>
              <div className="space-y-2">
                {documents.map((doc) => (
                  <button
                    key={doc.id}
                    type="button"
                    onClick={() => setSelectedDocumentId(doc.id)}
                    className={cn(
                      "w-full rounded-md border border-slate-800 bg-slate-950/35 p-3 text-left text-xs font-bold text-slate-300 transition hover:border-indigo-500/40",
                      selectedDocument?.id === doc.id && "border-indigo-500/50 bg-indigo-500/10 text-white",
                    )}
                  >
                    <span className="line-clamp-2">{doc.filename}</span>
                    <span className="mt-1 block text-[10px] uppercase tracking-widest text-slate-500">{doc.page_count || "?"} pages</span>
                  </button>
                ))}
              </div>
            </section>
          )}
        </aside>

        <section className="space-y-6">
          <section className="rounded-lg border border-slate-800/70 bg-[#11111d]/75 p-5">
            <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="flex items-center gap-2 font-outfit text-2xl font-black text-white">
                  <Database className="text-indigo-300" /> Extracted Schema
                </h2>
                <p className="mt-1 text-sm font-bold text-slate-500">Only structured schema fields are shown here.</p>
              </div>
              <span className="rounded-md border border-slate-800 bg-slate-950/40 px-3 py-2 text-xs font-black text-slate-300">
                {stats?.mentions_after_merge ?? mentions.length} mentions
              </span>
            </div>
            {sectionList.length > 0 ? (
              sectionList.map(([name, section]) => <SchemaSection key={name} name={name} section={section} />)
            ) : (
              <div className="grid gap-3 xl:grid-cols-2">
                {mentions.map((item, index) => (
                  <SchemaItem key={item.id || index} item={item} />
                ))}
              </div>
            )}
          </section>

          <section className="grid gap-6 2xl:grid-cols-[0.95fr_1.05fr]">
            <div className="rounded-lg border border-slate-800/70 bg-[#11111d]/75 p-5">
              <h2 className="mb-5 flex items-center gap-2 font-outfit text-xl font-black text-white">
                <GitBranch className="text-cyan-300" /> Schema Tree
              </h2>
              <SchemaTree sections={sectionList} />
            </div>

            <div className="rounded-lg border border-slate-800/70 bg-[#11111d]/75 p-5">
              <h2 className="mb-5 flex items-center gap-2 font-outfit text-xl font-black text-white">
                <ShieldCheck className="text-emerald-300" /> Quality
              </h2>
              <div className="grid gap-2 sm:grid-cols-2">
                <Field label="Validation" value={validation?.status || "completed"} />
                <Field label="Backend" value={validation?.backend || "heuristic"} />
                <Field label="Model" value={validation?.model_id || "rules"} />
                <Field label="Raw sections" value={sectionList.length} />
                <Field label="Correction status" value={grouped?.quality_checks?.spell_check?.status || "completed"} />
                <Field label="Flags" value={asArray<string>(validation?.flags).length || "none"} />
              </div>
              <div className="mt-5 rounded-lg border border-emerald-500/20 bg-emerald-500/10 p-4">
                <p className="flex items-center gap-2 text-sm font-black text-emerald-100">
                  <CheckCircle2 size={17} /> Schema display is evidence-first.
                </p>
                <p className="mt-2 text-xs font-bold leading-relaxed text-emerald-200/75">
                  Overview and separate clinical diagnosis blocks were removed from this page. The record now shows schema fields, tree structure, quality, and source PDF only.
                </p>
              </div>
            </div>
          </section>
        </section>
      </div>
    </main>
  );
}
