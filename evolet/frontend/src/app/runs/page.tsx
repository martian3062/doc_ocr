"use client";

import React, { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { 
  Activity, 
  Clock, 
  CheckCircle2, 
  XCircle, 
  Play, 
  RotateCcw, 
  Trash2,
  MoreVertical,
  Search,
  ExternalLink
} from "lucide-react";
import { getRunHistory } from "@/lib/api";
import { cn, formatDate } from "@/lib/utils";

export default function RunsPage() {
  const [runs, setRuns] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getRunHistory().then(res => {
      setRuns(res.data.runs);
      setLoading(false);
    }).catch(err => {
      console.error(err);
      setLoading(false);
    });
  }, []);

  if (loading) return (
    <div className="flex items-center justify-center min-h-screen bg-[#0a0a0f]">
      <motion.div animate={{ rotate: 360 }} transition={{ repeat: Infinity, duration: 1, ease: "linear" }} className="w-10 h-10 border-4 border-indigo-500 border-t-transparent rounded-full" />
    </div>
  );

  return (
    <div className="p-8 space-y-8 animate-in fade-in duration-700">
      <header className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold font-outfit text-white">Pipeline Executions</h1>
          <p className="text-slate-400">Track extraction history and processing performance.</p>
        </div>
        <button className="flex items-center gap-2 px-5 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl font-medium transition-all shadow-lg shadow-indigo-600/20 active:scale-95">
          <Play size={18} />
          Execute Batch
        </button>
      </header>

      <div className="overflow-hidden rounded-[2rem] border border-slate-800 bg-[#11111d]">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="bg-slate-900/50 border-b border-slate-800">
              <th className="px-6 py-4 text-[10px] uppercase font-bold text-slate-500 tracking-widest">Execution</th>
              <th className="px-6 py-4 text-[10px] uppercase font-bold text-slate-500 tracking-widest">Status</th>
              <th className="px-6 py-4 text-[10px] uppercase font-bold text-slate-500 tracking-widest">Progress</th>
              <th className="px-6 py-4 text-[10px] uppercase font-bold text-slate-500 tracking-widest">Volume</th>
              <th className="px-6 py-4 text-[10px] uppercase font-bold text-slate-500 tracking-widest">Time</th>
              <th className="px-6 py-4 text-[10px] uppercase font-bold text-slate-500 tracking-widest">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {runs.map((run, i) => (
              <motion.tr 
                key={run.id} 
                initial={{ opacity: 0, x: -10 }} 
                animate={{ opacity: 1, x: 0 }} 
                transition={{ delay: i * 0.05 }}
                className="group hover:bg-white/5 transition-colors cursor-pointer"
              >
                <td className="px-6 py-5">
                  <div className="flex items-center gap-4">
                    <div className={cn(
                      "w-10 h-10 rounded-xl flex items-center justify-center",
                      run.status === 'completed' ? 'bg-emerald-500/10 text-emerald-500' : 'bg-amber-500/10 text-amber-500'
                    )}>
                      <Activity size={20} />
                    </div>
                    <div>
                      <p className="text-sm font-bold text-white group-hover:text-indigo-400 transition-colors">{run.name}</p>
                      <p className="text-[10px] font-mono text-slate-600 mt-0.5">{run.id.substring(0, 13)}</p>
                    </div>
                  </div>
                </td>
                <td className="px-6 py-5">
                   <div className={cn(
                     "inline-flex items-center gap-2 px-3 py-1 rounded-full text-[10px] font-bold uppercase tracking-widest border",
                     run.status === 'completed' ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20' : 'bg-amber-500/10 text-amber-500 border-amber-500/20'
                   )}>
                     {run.status === 'completed' ? <CheckCircle2 size={12} /> : <Clock size={12} />}
                     {run.status}
                   </div>
                </td>
                <td className="px-6 py-5">
                  <div className="w-32">
                    <div className="flex justify-between text-[10px] mb-1">
                      <span className="text-slate-500">Processing</span>
                      <span className="text-slate-200">{run.progress}%</span>
                    </div>
                    <div className="w-full bg-slate-800 rounded-full h-1.5 overflow-hidden">
                      <motion.div 
                        initial={{ width: 0 }}
                        animate={{ width: `${run.progress}%` }}
                        className="bg-indigo-500 h-full"
                      />
                    </div>
                  </div>
                </td>
                <td className="px-6 py-5">
                  <div className="text-sm font-medium text-slate-400">
                    <span className="text-white">{run.processed_pdfs}</span> / {run.total_pdfs} Docs
                    <p className="text-[10px] text-slate-600 font-bold">{run.total_mentions} Entities</p>
                  </div>
                </td>
                <td className="px-6 py-5">
                   <span className="text-xs text-slate-500">{formatDate(run.started_at)}</span>
                </td>
                <td className="px-6 py-5">
                  <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button className="p-2 hover:bg-slate-800 rounded-lg text-slate-400 hover:text-white transition-all"><ExternalLink size={16} /></button>
                    <button className="p-2 hover:bg-slate-800 rounded-lg text-slate-400 hover:text-white transition-all"><MoreVertical size={16} /></button>
                  </div>
                </td>
              </motion.tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
