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
  Share2, 
  Settings, 
  ChevronLeft,
  ChevronRight,
  Database,
  Cpu
} from "lucide-react";
import { cn } from "@/lib/utils";

const items = [
  { name: "Overview", href: "/", icon: LayoutDashboard },
  { name: "Patients", href: "/patients", icon: Users },
  { name: "Runs", href: "/runs", icon: Activity },
  { name: "Documents", href: "/documents", icon: FileText },
  { name: "Knowledge Map", href: "/map", icon: Share2 },
  { name: "Infrastructure", href: "/infra", icon: Cpu },
  { name: "Settings", href: "/settings", icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);

  return (
    <motion.div
      initial={false}
      animate={{ width: collapsed ? 80 : 260 }}
      className="relative z-50 h-screen border-r border-[#1e1e2d] bg-[#0d0d16] flex flex-col transition-[width] ease-in-out duration-300"
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
              <div className="w-8 h-8 rounded-lg bg-gradient-to-tr from-indigo-500 to-violet-500 flex items-center justify-center">
                <Database className="w-5 h-5 text-white" />
              </div>
              <span className="text-xl font-bold font-outfit tracking-tight bg-gradient-to-r from-white to-slate-400 bg-clip-text text-transparent">
                EVOLET
              </span>
            </motion.div>
          )}
        </AnimatePresence>
        
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="p-2 rounded-lg hover:bg-slate-800 transition-colors text-slate-400"
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
                    ? "bg-indigo-500/10 text-indigo-400" 
                    : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/50"
                )}
              >
                {isActive && (
                  <motion.div 
                    layoutId="active-indicator"
                    className="absolute left-0 top-0 bottom-0 w-1 bg-indigo-500 rounded-r-full"
                  />
                )}
                <item.icon size={22} className={cn("shrink-0", isActive && "text-indigo-500")} />
                {!collapsed && (
                  <span className="text-[15px] font-medium transition-opacity duration-200">
                    {item.name}
                  </span>
                )}
                {collapsed && (
                  <div className="absolute left-16 bg-slate-900 border border-slate-800 px-3 py-1 rounded text-xs opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-nowrap z-[100]">
                    {item.name}
                  </div>
                )}
              </div>
            </Link>
          );
        })}
      </nav>

      <div className="p-4 border-t border-[#1e1e2d]">
        <div className="flex items-center gap-3 p-3 rounded-xl bg-slate-900/50 border border-slate-800/50">
          <div className="w-10 h-10 rounded-full bg-slate-700 overflow-hidden shrink-0" />
          {!collapsed && (
            <div className="overflow-hidden">
              <p className="text-sm font-semibold text-slate-100 truncate">Pardeep</p>
              <p className="text-[11px] text-slate-500 truncate">System Admin</p>
            </div>
          )}
        </div>
      </div>
    </motion.div>
  );
}
