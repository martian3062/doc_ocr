"use client";

import React from "react";
import { Settings, SlidersHorizontal, ShieldCheck } from "lucide-react";

export default function SettingsPage() {
  return (
    <div className="p-8 lg:p-12 space-y-8 max-w-[1200px] mx-auto min-h-screen">
      <header className="flex flex-col gap-3">
        <h1 className="text-4xl font-black text-white font-outfit tracking-tight flex items-center gap-4">
          <div className="p-3 rounded-2xl bg-indigo-500/10 border border-indigo-500/20">
            <Settings className="text-indigo-400" size={30} />
          </div>
          Settings
        </h1>
        <p className="text-slate-400 font-medium">
          Configuration controls are not exposed in the frontend yet, but this route is now live.
        </p>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="p-8 rounded-[2.5rem] bg-[#11111d]/60 border border-slate-800/50 backdrop-blur-xl">
          <SlidersHorizontal className="text-indigo-400 mb-4" size={24} />
          <h2 className="text-xl font-bold text-white font-outfit mb-3">Frontend Settings</h2>
          <p className="text-slate-400 leading-relaxed">
            Theme, navigation, and workflow preferences can be added here once the product settings model is finalized.
          </p>
        </div>

        <div className="p-8 rounded-[2.5rem] bg-[#11111d]/60 border border-slate-800/50 backdrop-blur-xl">
          <ShieldCheck className="text-emerald-400 mb-4" size={24} />
          <h2 className="text-xl font-bold text-white font-outfit mb-3">System Configuration</h2>
          <p className="text-slate-400 leading-relaxed">
            Backend and deployment settings are currently managed on the VM and in Docker rather than from this page.
          </p>
        </div>
      </div>
    </div>
  );
}
