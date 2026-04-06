"use client";

import React, { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  Activity,
  Cpu,
  HardDrive,
  MemoryStick,
  Microchip,
  Server,
  Timer,
  Workflow,
} from "lucide-react";
import { getDashboardData } from "@/lib/api";

function formatValue(value: number | null | undefined, suffix = "") {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "N/A";
  }
  return `${value}${suffix}`;
}

export default function InfraPage() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getDashboardData()
      .then((res) => {
        setData(res.data);
        setLoading(false);
      })
      .catch((err) => {
        console.error(err);
        setLoading(false);
      });
  }, []);

  const system = data?.system || {};
  const gpu = data?.gpu || {};

  const cards = [
    {
      label: "Compute Tier",
      value: system.compute_tier || "unknown",
      detail: "Detected execution profile",
      icon: Workflow,
      tone: "text-indigo-400 bg-indigo-500/10 border-indigo-500/20",
    },
    {
      label: "ETA / PDF",
      value: formatValue(system.eta_per_pdf_s, "s"),
      detail: "Current baseline estimate",
      icon: Timer,
      tone: "text-amber-400 bg-amber-500/10 border-amber-500/20",
    },
    {
      label: "CPU Cores",
      value: formatValue(system.cpu_cores),
      detail: system.cpu_model || "Processor info unavailable",
      icon: Cpu,
      tone: "text-emerald-400 bg-emerald-500/10 border-emerald-500/20",
    },
    {
      label: "RAM Usage",
      value: formatValue(system.ram_used_pct, "%"),
      detail: `${formatValue(system.ram_free_gb, " GB")} free of ${formatValue(system.ram_total_gb, " GB")}`,
      icon: MemoryStick,
      tone: "text-sky-400 bg-sky-500/10 border-sky-500/20",
    },
  ];

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-[#0a0a0f]">
        <motion.div
          animate={{ rotate: 360 }}
          transition={{ repeat: Infinity, duration: 1, ease: "linear" }}
          className="w-10 h-10 border-4 border-indigo-500 border-t-transparent rounded-full"
        />
      </div>
    );
  }

  return (
    <div className="p-8 lg:p-12 space-y-8 max-w-[1600px] mx-auto min-h-screen">
      <header className="flex flex-col gap-3">
        <h1 className="text-4xl font-black text-white font-outfit tracking-tight flex items-center gap-4">
          <div className="p-3 rounded-2xl bg-indigo-500/10 border border-indigo-500/20">
            <Server className="text-indigo-400" size={30} />
          </div>
          Infrastructure
        </h1>
        <p className="text-slate-400 font-medium">
          Live hardware visibility for the OCR pipeline runtime.
        </p>
      </header>

      <section className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-6">
        {cards.map((card) => (
          <div
            key={card.label}
            className="p-7 rounded-[2rem] bg-[#11111d]/60 border border-slate-800/50 backdrop-blur-xl"
          >
            <div className="flex items-start justify-between mb-5">
              <div className={`p-3 rounded-2xl border ${card.tone}`}>
                <card.icon size={20} />
              </div>
              <span className="text-[10px] uppercase tracking-widest font-bold text-slate-500">
                Live
              </span>
            </div>
            <p className="text-xs uppercase tracking-widest font-bold text-slate-500 mb-2">
              {card.label}
            </p>
            <h2 className="text-2xl font-black text-white font-outfit mb-2 break-words">
              {card.value}
            </h2>
            <p className="text-sm text-slate-400 leading-relaxed">
              {card.detail}
            </p>
          </div>
        ))}
      </section>

      <section className="grid grid-cols-1 xl:grid-cols-2 gap-8">
        <div className="p-8 rounded-[2.5rem] bg-[#11111d]/60 border border-slate-800/50 backdrop-blur-xl">
          <h2 className="text-xl font-bold text-white font-outfit mb-6 flex items-center gap-3">
            <Microchip className="text-indigo-400" size={20} />
            GPU Runtime
          </h2>

          {gpu.available ? (
            <div className="space-y-4">
              <div className="rounded-2xl bg-slate-900/40 border border-slate-800/50 p-5">
                <p className="text-[10px] uppercase tracking-widest font-bold text-slate-500 mb-2">
                  Device
                </p>
                <p className="text-lg font-bold text-white">{gpu.device_name}</p>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="rounded-2xl bg-slate-900/40 border border-slate-800/50 p-5">
                  <p className="text-[10px] uppercase tracking-widest font-bold text-slate-500 mb-2">
                    Total VRAM
                  </p>
                  <p className="text-2xl font-black text-white">{formatValue(gpu.total_gb, " GB")}</p>
                </div>
                <div className="rounded-2xl bg-slate-900/40 border border-slate-800/50 p-5">
                  <p className="text-[10px] uppercase tracking-widest font-bold text-slate-500 mb-2">
                    Used
                  </p>
                  <p className="text-2xl font-black text-white">{formatValue(gpu.used_gb, " GB")}</p>
                </div>
                <div className="rounded-2xl bg-slate-900/40 border border-slate-800/50 p-5">
                  <p className="text-[10px] uppercase tracking-widest font-bold text-slate-500 mb-2">
                    Utilization
                  </p>
                  <p className="text-2xl font-black text-white">{formatValue(gpu.utilization_pct, "%")}</p>
                </div>
              </div>

              <div>
                <div className="flex justify-between text-xs font-bold text-slate-400 mb-2">
                  <span>VRAM pressure</span>
                  <span>{formatValue(gpu.utilization_pct, "%")}</span>
                </div>
                <div className="w-full h-3 rounded-full bg-slate-900 overflow-hidden border border-slate-800/60">
                  <div
                    className="h-full bg-gradient-to-r from-emerald-500 via-indigo-500 to-rose-500"
                    style={{ width: `${Math.max(0, Math.min(gpu.utilization_pct || 0, 100))}%` }}
                  />
                </div>
              </div>
            </div>
          ) : (
            <div className="rounded-[2rem] border border-dashed border-slate-800/60 p-8 text-center">
              <HardDrive className="mx-auto text-slate-600 mb-4" size={34} />
              <p className="text-slate-400 font-medium">No CUDA device detected on this runtime.</p>
            </div>
          )}
        </div>

        <div className="p-8 rounded-[2.5rem] bg-[#11111d]/60 border border-slate-800/50 backdrop-blur-xl">
          <h2 className="text-xl font-bold text-white font-outfit mb-6 flex items-center gap-3">
            <Activity className="text-emerald-400" size={20} />
            System Snapshot
          </h2>

          <div className="space-y-4">
            <div className="rounded-2xl bg-slate-900/40 border border-slate-800/50 p-5">
              <p className="text-[10px] uppercase tracking-widest font-bold text-slate-500 mb-2">
                Platform
              </p>
              <p className="text-base font-semibold text-white break-words">
                {system.platform || "Unknown platform"}
              </p>
            </div>

            <div className="rounded-2xl bg-slate-900/40 border border-slate-800/50 p-5">
              <p className="text-[10px] uppercase tracking-widest font-bold text-slate-500 mb-2">
                CPU Model
              </p>
              <p className="text-base font-semibold text-white break-words">
                {system.cpu_model || "Unknown CPU"}
              </p>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="rounded-2xl bg-slate-900/40 border border-slate-800/50 p-5">
                <p className="text-[10px] uppercase tracking-widest font-bold text-slate-500 mb-2">
                  Free RAM
                </p>
                <p className="text-xl font-black text-white">{formatValue(system.ram_free_gb, " GB")}</p>
              </div>
              <div className="rounded-2xl bg-slate-900/40 border border-slate-800/50 p-5">
                <p className="text-[10px] uppercase tracking-widest font-bold text-slate-500 mb-2">
                  Total RAM
                </p>
                <p className="text-xl font-black text-white">{formatValue(system.ram_total_gb, " GB")}</p>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
