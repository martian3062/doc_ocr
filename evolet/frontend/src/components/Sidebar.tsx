"use client";

import React, { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import { 
  LayoutDashboard, 
  Users, 
  Activity, 
  FileText, 
  ChevronLeft,
  ChevronRight,
  Database,
} from "lucide-react";
import { cn } from "@/lib/utils";

const items = [
  { name: "Overview", href: "/", icon: LayoutDashboard },
  { name: "Patients", href: "/patients", icon: Users },
  { name: "Runs", href: "/runs", icon: Activity },
  { name: "Documents", href: "/documents", icon: FileText },
];

export function Sidebar() {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);

  return (
    <motion.div
      initial={false}
      animate={{ width: collapsed ? 80 : 260 }}
      className="relative z-50 h-screen border-r border-sky-200/70 bg-white/70 flex flex-col transition-[width] ease-in-out duration-300 shadow-[18px_0_70px_rgba(67,181,232,0.16)] backdrop-blur-2xl"
    >
      <div className="flex items-center justify-between p-6 overflow-hidden">
        <AnimatePresence mode="wait">
          {!collapsed && (
            <motion.div
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -10 }}
              className="flex items-center gap-3"
            >
              <div className="w-8 h-8 rounded-lg bg-gradient-to-tr from-white via-sky-200 to-cyan-400 flex items-center justify-center shadow-[0_10px_28px_rgba(56,189,248,0.3)]">
                <Database className="w-5 h-5 text-sky-700" />
              </div>
              <span className="text-xl font-bold font-outfit tracking-tight bg-gradient-to-r from-sky-950 via-sky-500 to-cyan-300 bg-clip-text text-transparent">
                doc-reader
              </span>
            </motion.div>
          )}
        </AnimatePresence>
        
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="p-2 rounded-lg text-sky-600 transition-colors hover:bg-sky-100/80"
        >
          {collapsed ? <ChevronRight size={20} /> : <ChevronLeft size={20} />}
        </button>
      </div>

      <nav className="flex-1 px-4 mt-4 space-y-2 overflow-y-auto custom-scrollbar">
        {items.map((item) => {
          const isActive = pathname === item.href;
          return (
            <Link key={item.name} href={item.href}>
              <div
                className={cn(
                  "flex items-center gap-4 px-4 py-3 rounded-xl transition-all group relative overflow-hidden",
                  isActive 
                    ? "bg-sky-100/80 text-sky-700 shadow-[0_12px_34px_rgba(56,189,248,0.18)]" 
                    : "text-slate-600 hover:text-sky-700 hover:bg-white/75"
                )}
              >
                {isActive && (
                  <motion.div 
                    layoutId="active-indicator"
                    className="absolute left-0 top-0 bottom-0 w-1 bg-sky-400 rounded-r-full"
                  />
                )}
                <item.icon size={22} className={cn("shrink-0", isActive && "text-sky-500")} />
                {!collapsed && (
                  <span className="text-[15px] font-medium transition-opacity duration-200">
                    {item.name}
                  </span>
                )}
                {collapsed && (
                  <div className="absolute left-16 bg-white/90 border border-sky-200 px-3 py-1 rounded text-xs opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-nowrap z-[100] shadow-lg">
                    {item.name}
                  </div>
                )}
              </div>
            </Link>
          );
        })}
      </nav>

      <div className="p-4 border-t border-sky-200/70">
        <div className="flex items-center gap-3 p-3 rounded-xl bg-white/75 border border-sky-200/70 shadow-[0_12px_36px_rgba(56,189,248,0.16)]">
          <div className="w-10 h-10 rounded-full bg-sky-100/80 border border-sky-200 overflow-hidden shrink-0 flex items-center justify-center">
            <Database size={18} className="text-sky-500" />
          </div>
          {!collapsed && (
            <div className="overflow-hidden">
              <p className="text-sm font-semibold text-slate-100 truncate">L4 runtime</p>
              <p className="text-[11px] text-slate-500 truncate">LLM-first validation</p>
            </div>
          )}
        </div>
      </div>
    </motion.div>
  );
}
