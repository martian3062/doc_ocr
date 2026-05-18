"use client";

import React, { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Search, 
  MoreVertical, 
  ChevronRight, 
  FileText, 
  Activity,
} from "lucide-react";
import { getPatientList } from "@/lib/api";
import { formatDate } from "@/lib/utils";

export default function PatientListPage() {
  const [patients, setPatients] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState("");
  const [error, setError] = useState<string | null>(null);

  const fetchPatients = (q = "") => {
    setLoading(true);
    getPatientList({ q, has_results: "1" }).then(res => {
      setPatients(res.data.patients || []);
      setLoading(false);
      setError(null);
    }).catch(err => {
      console.error(err);
      setError("Failed to connect to clinical registry. Please verify the backend is running.");
      setLoading(false);
    });
  };

  useEffect(() => {
    fetchPatients();
  }, []);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    fetchPatients(searchTerm);
  };

  return (
    <div className="p-8 space-y-8 min-h-screen">
      <header className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold font-outfit text-white">Extracted Results</h1>
          <p className="text-slate-400">Only patients with completed pipeline records are shown here.</p>
        </div>
      </header>

      <div className="flex gap-4">
        <form onSubmit={handleSearch} className="flex-1 relative group">
          <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500 group-focus-within:text-indigo-400 transition-colors" size={20} />
          <input 
            type="text" 
            placeholder="Search by code or name..." 
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full pl-12 pr-4 py-3 bg-[#11111d] border border-slate-800 rounded-2xl focus:outline-none focus:ring-2 focus:ring-indigo-500/50 focus:border-indigo-500/50 transition-all text-slate-200 placeholder:text-slate-600"
          />
        </form>
      </div>
      {error && !loading && (
        <div className="p-8 rounded-[2rem] bg-red-500/5 border border-red-500/20 text-center space-y-4">
          <Activity className="mx-auto text-red-500/50" size={48} />
          <p className="text-red-400 font-medium">{error}</p>
          <button 
            onClick={() => fetchPatients()}
            className="px-6 py-2 bg-slate-800 text-white rounded-xl hover:bg-slate-700 transition-all font-medium"
          >
            Retry Connection
          </button>
        </div>
      )}

      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {[1,2,3,4,5,6].map(i => (
            <div key={i} className="h-48 rounded-3xl bg-[#11111d] animate-pulse border border-slate-800/40" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          <AnimatePresence>
            {patients.map((p, i) => (
              <motion.div
                key={p.id}
                initial={{ opacity: 0, scale: 0.95, y: 10 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                transition={{ delay: i * 0.05 }}
                whileHover={{ y: -5, borderColor: '#6366f1' }}
                className="group relative bg-[#11111d] border border-slate-800/60 rounded-3xl p-6 transition-all shadow-sm hover:shadow-indigo-500/10 cursor-pointer"
              >
                <a href={`/patients/${p.id}`} className="absolute inset-0 z-10" aria-label={`Open ${p.display_name}`} />
                <div className="flex justify-between items-start mb-6">
                  <div className="flex items-center gap-4">
                    <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-slate-800 to-slate-900 border border-slate-700 flex items-center justify-center text-indigo-400 font-bold group-hover:from-indigo-900/40 group-hover:to-indigo-800/40 transition-all">
                      {p.code.substring(0, 2)}
                    </div>
                    <div>
                      <h3 className="font-bold text-white group-hover:text-indigo-400 transition-colors">{p.display_name}</h3>
                      <p className="text-xs font-mono text-slate-500">{p.code}</p>
                    </div>
                  </div>
                  <button className="p-2 text-slate-600 hover:text-slate-400 transition-colors z-20">
                    <MoreVertical size={18} />
                  </button>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="bg-slate-900/40 rounded-2xl p-3 border border-slate-800/50">
                    <div className="flex items-center gap-2 text-slate-500 mb-1">
                      <FileText size={14} />
                      <span className="text-[10px] uppercase font-bold tracking-wider">Reports</span>
                    </div>
                    <span className="text-lg font-bold text-slate-200">{p.doc_count}</span>
                  </div>
                  <div className="bg-slate-900/40 rounded-2xl p-3 border border-slate-800/50">
                    <div className="flex items-center gap-2 text-slate-500 mb-1">
                      <Activity size={14} />
                      <span className="text-[10px] uppercase font-bold tracking-wider">Mentions</span>
                    </div>
                    <span className="text-lg font-bold text-slate-200">{p.mention_count}</span>
                  </div>
                </div>

                <div className="mt-6 flex justify-between items-center text-xs">
                  <span className="text-slate-600">Registered {formatDate(p.created_at)}</span>
                  <div className="flex items-center gap-1 text-indigo-400 group-hover:translate-x-1 transition-transform">
                    Detail <ChevronRight size={14} />
                  </div>
                </div>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      )}
    </div>
  );
}
