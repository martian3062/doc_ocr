"use client";

import React, { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  FileText,
  Search,
  FolderOpen,
  Upload,
  UserRound,
  CalendarDays,
  FileDigit,
} from "lucide-react";
import { getDocumentList } from "@/lib/api";
import { formatDate, formatNumber } from "@/lib/utils";

function formatBytes(bytes: number) {
  if (!bytes) return "N/A";
  const sizes = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < sizes.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${sizes[unit]}`;
}

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState("");

  const fetchDocuments = (q = "") => {
    setLoading(true);
    getDocumentList({ q })
      .then((res) => {
        setDocuments(res.data.documents || []);
        setLoading(false);
      })
      .catch((err) => {
        console.error(err);
        setLoading(false);
      });
  };

  useEffect(() => {
    fetchDocuments();
  }, []);

  return (
    <div className="p-8 lg:p-12 space-y-8 max-w-[1600px] mx-auto min-h-screen">
      <header className="flex flex-col gap-3">
        <h1 className="text-4xl font-black text-white font-outfit tracking-tight flex items-center gap-4">
          <div className="p-3 rounded-2xl bg-indigo-500/10 border border-indigo-500/20">
            <FileText className="text-indigo-400" size={30} />
          </div>
          Documents
        </h1>
        <p className="text-slate-400 font-medium">
          Browse imported and uploaded source files across all patients.
        </p>
      </header>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          fetchDocuments(searchTerm);
        }}
        className="relative max-w-xl"
      >
        <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500" size={18} />
        <input
          type="text"
          placeholder="Search by filename, patient code, or patient name..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          className="w-full pl-12 pr-4 py-3 bg-[#11111d] border border-slate-800 rounded-2xl focus:outline-none focus:ring-2 focus:ring-indigo-500/40 text-slate-200 placeholder:text-slate-600"
        />
      </form>

      <div className="rounded-[2rem] border border-slate-800/60 bg-[#11111d]/60 backdrop-blur-xl overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-800/60 flex items-center justify-between">
          <p className="text-sm font-bold text-white">Source Files</p>
          <p className="text-xs uppercase tracking-widest font-bold text-slate-500">
            {formatNumber(documents.length)} total
          </p>
        </div>

        {loading ? (
          <div className="p-8 space-y-4">
            {[1, 2, 3, 4, 5].map((i) => (
              <div key={i} className="h-24 rounded-2xl bg-slate-900/50 border border-slate-800/40 animate-pulse" />
            ))}
          </div>
        ) : documents.length === 0 ? (
          <div className="p-12 text-center">
            <FileText className="mx-auto text-slate-700 mb-4" size={36} />
            <p className="text-slate-400 font-medium">No documents found for this search.</p>
          </div>
        ) : (
          <div className="divide-y divide-slate-800/60">
            {documents.map((doc, index) => (
              <motion.div
                key={doc.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: index * 0.02 }}
                className="p-6 hover:bg-white/5 transition-colors"
              >
                <div className="flex flex-col xl:flex-row xl:items-center gap-5 xl:gap-8">
                  <div className="flex items-start gap-4 min-w-0 flex-1">
                    <div className="w-12 h-12 rounded-2xl bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center shrink-0">
                      <FileText className="text-indigo-400" size={20} />
                    </div>
                    <div className="min-w-0">
                      <p className="text-white font-bold truncate">{doc.filename}</p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        <span className="px-2.5 py-1 rounded-lg bg-slate-900 border border-slate-800 text-[10px] font-bold uppercase tracking-widest text-slate-400 flex items-center gap-1.5">
                          {doc.source_type === "upload" ? <Upload size={12} /> : <FolderOpen size={12} />}
                          {doc.source_type}
                        </span>
                        <span className="px-2.5 py-1 rounded-lg bg-slate-900 border border-slate-800 text-[10px] font-bold uppercase tracking-widest text-slate-400 flex items-center gap-1.5">
                          <FileDigit size={12} />
                          {doc.page_count || 0} pages
                        </span>
                      </div>
                    </div>
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-3 gap-4 xl:w-[620px]">
                    <a
                      href={`/patients/${doc.patient_id}`}
                      className="rounded-2xl bg-slate-900/40 border border-slate-800/50 p-4 hover:border-indigo-500/30 transition-colors"
                    >
                      <p className="text-[10px] uppercase tracking-widest font-bold text-slate-500 mb-2 flex items-center gap-2">
                        <UserRound size={12} />
                        Patient
                      </p>
                      <p className="text-white font-semibold truncate">{doc.patient_name}</p>
                      <p className="text-xs text-slate-500 font-mono truncate">{doc.patient_code}</p>
                    </a>

                    <div className="rounded-2xl bg-slate-900/40 border border-slate-800/50 p-4">
                      <p className="text-[10px] uppercase tracking-widest font-bold text-slate-500 mb-2 flex items-center gap-2">
                        <CalendarDays size={12} />
                        Imported
                      </p>
                      <p className="text-white font-semibold">{formatDate(doc.created_at)}</p>
                      <p className="text-xs text-slate-500">{formatBytes(doc.file_size_bytes)}</p>
                    </div>

                    <div className="rounded-2xl bg-slate-900/40 border border-slate-800/50 p-4">
                      <p className="text-[10px] uppercase tracking-widest font-bold text-slate-500 mb-2">
                        Document ID
                      </p>
                      <p className="text-white font-mono text-sm truncate">{doc.id.slice(0, 13)}</p>
                    </div>
                  </div>
                </div>
              </motion.div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
