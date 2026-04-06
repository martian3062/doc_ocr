"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { motion } from "framer-motion";
import { ArrowLeft, Maximize2, Move, ZoomIn, Share2, Info } from "lucide-react";
import { getKnowledgeMap, getPatientDetail } from "@/lib/api";
import { KnowledgeGraph } from "@/components/KnowledgeGraph";

export default function KnowledgeMapPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;
  const [data, setData] = useState<any>(null);
  const [patient, setPatient] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!id) {
      return;
    }

    Promise.all([
      getKnowledgeMap(id),
      getPatientDetail(id)
    ]).then(([mapRes, patientRes]) => {
      setData(mapRes.data);
      setPatient(patientRes.data.patient);
      setLoading(false);
    }).catch(err => {
      console.error(err);
      setLoading(false);
    });
  }, [id]);

  if (loading) return (
    <div className="flex items-center justify-center min-h-screen bg-[#0a0a0f]">
      <motion.div animate={{ rotate: 360 }} transition={{ repeat: Infinity, duration: 1, ease: "linear" }} className="w-10 h-10 border-4 border-indigo-500 border-t-transparent rounded-full" />
    </div>
  );

  return (
    <div className="h-screen flex flex-col bg-[#0a0a0f] p-8 overflow-hidden">
      <header className="flex items-center justify-between mb-8 shrink-0">
        <div className="flex items-center gap-6">
          <Link href={`/patients/${id}`} className="p-2 rounded-full bg-slate-900 border border-slate-800 text-slate-400 hover:text-white transition-colors">
            <ArrowLeft size={20} />
          </Link>
          <div>
            <h1 className="text-2xl font-bold font-outfit text-white flex items-center gap-3">
              Clinical Entity Map <span className="text-indigo-500/50">/</span> {patient?.display_name}
            </h1>
            <p className="text-xs text-slate-500 font-mono mt-1">Mapping relationships and extraction evidence.</p>
          </div>
        </div>
        <div className="flex gap-2">
           <button className="p-3 rounded-2xl bg-[#11111d] border border-slate-800 text-slate-400 hover:text-white transition-all"><Maximize2 size={18} /></button>
           <button className="p-3 rounded-2xl bg-[#11111d] border border-slate-800 text-slate-400 hover:text-white transition-all"><Share2 size={18} /></button>
        </div>
      </header>

      <div className="flex-1 relative">
        {/* Controls Overlay */}
        <div className="absolute top-6 right-6 z-20 flex flex-col gap-2">
            <div className="p-2 rounded-2xl bg-slate-900/80 backdrop-blur-md border border-slate-700/50 shadow-2xl space-y-1">
                <button className="p-2 rounded-xl hover:bg-slate-800 text-slate-400 hover:text-white transition-all"><ZoomIn size={18} /></button>
                <div className="h-px bg-slate-800 mx-1" />
                <button className="p-2 rounded-xl hover:bg-slate-800 text-slate-400 hover:text-white transition-all"><Move size={18} /></button>
            </div>
            <button className="p-2 rounded-2xl bg-indigo-600 border border-indigo-500 shadow-xl shadow-indigo-600/20 text-white"><Info size={18} /></button>
        </div>

        {/* Legend */}
        <div className="absolute bottom-6 left-6 z-20 p-4 rounded-2xl bg-slate-900/80 backdrop-blur-md border border-slate-700/50 shadow-2xl">
           <p className="text-[10px] uppercase font-bold text-slate-500 tracking-widest mb-3">Graph Legend</p>
           <div className="space-y-2">
              <div className="flex items-center gap-3">
                 <div className="w-3 h-3 rounded-full bg-indigo-500 shadow-[0_0_8px_#6366f1]" />
                 <span className="text-xs text-slate-300">Clinical Category</span>
              </div>
              <div className="flex items-center gap-3">
                 <div className="w-3 h-3 rounded-full bg-slate-700 border border-slate-500" />
                 <span className="text-xs text-slate-300">Extracted Entity</span>
              </div>
           </div>
        </div>

        <motion.div 
            initial={{ opacity: 0, scale: 0.98 }}
            animate={{ opacity: 1, scale: 1 }}
            className="w-full h-full"
        >
            <KnowledgeGraph data={data} />
        </motion.div>
      </div>
    </div>
  );
}
