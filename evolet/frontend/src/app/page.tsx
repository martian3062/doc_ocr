"use client";

import React, { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { gsap } from "gsap";
import { 
  Users, 
  FileText, 
  CheckCircle2, 
  Clock, 
  Activity, 
  Cpu,
  ChevronRight,
  Zap,
  Tag,
  Search,
  LayoutDashboard,
  RefreshCcw
} from "lucide-react";
import { 
  AreaChart, 
  Area, 
  XAxis, 
  YAxis, 
  CartesianGrid, 
  Tooltip as RechartsTooltip, 
  ResponsiveContainer
} from "recharts";
import { ResponsiveRadar } from '@nivo/radar';
import { ResponsiveBar } from '@nivo/bar';
import { getDashboardData } from "@/lib/api";
import { cn, formatDate } from "@/lib/utils";

const stats_data = [
  { name: 'Total Patients', value: '0', icon: Users, color: 'text-blue-400', bg: 'bg-blue-400/10' },
  { name: 'Processed Reports', value: '0', icon: FileText, color: 'text-emerald-400', bg: 'bg-emerald-400/10' },
  { name: 'Extracted Entities', value: '0', icon: Zap, color: 'text-amber-400', bg: 'bg-amber-400/10' },
  { name: 'System Accuracy', value: '98.2%', icon: CheckCircle2, color: 'text-indigo-400', bg: 'bg-indigo-400/10' },
];

const chartData = [
  { name: 'Mon', value: 400 },
  { name: 'Tue', value: 300 },
  { name: 'Wed', value: 600 },
  { name: 'Thu', value: 800 },
  { name: 'Fri', value: 500 },
  { name: 'Sat', value: 900 },
  { name: 'Sun', value: 1100 },
];

export default function Dashboard() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  
  useEffect(() => {
    getDashboardData().then(res => {
      setData(res.data);
      setLoading(false);
      
      // Entrance animations
      gsap.from(".stat-card", {
        y: 30,
        opacity: 0,
        stagger: 0.1,
        duration: 0.8,
        ease: "power3.out"
      });
      
      gsap.from(".main-chart", {
        y: 40,
        opacity: 0,
        duration: 1,
        delay: 0.4,
        ease: "power3.out"
      });
    }).catch(err => {
      console.error(err);
      setLoading(false);
    });
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="relative">
          <div className="w-16 h-16 border-4 border-indigo-500/20 border-t-indigo-500 rounded-full animate-spin"></div>
          <div className="absolute inset-0 flex items-center justify-center">
            <Activity size={24} className="text-indigo-500 animate-pulse" />
          </div>
        </div>
      </div>
    );
  }

  const stats = data?.stats || { total_patients: 0, total_pdfs: 0, total_mentions: 0 };
  const gpu = data?.gpu || { available: false, used_gb: 0, total_gb: 1, utilization_pct: 0 };
  const category_distribution = data?.category_distribution || [
    { category: 'Gene', count: 45 },
    { category: 'Drug', count: 32 },
    { category: 'Disease', count: 28 },
    { category: 'Variant', count: 18 }
  ];
  
  const radarData = [
    { key: "VRAM", value: gpu.total_gb ? (gpu.used_gb / gpu.total_gb) * 100 : 0 },
    { key: "Load", value: gpu.utilization_pct || 0 },
    { key: "Temp", value: 0 },
    { key: "Power", value: 45 }, 
    { key: "IO", value: 20 },
  ];

  const current_stats = [
    { ...stats_data[0], value: stats.total_patients.toString() },
    { ...stats_data[1], value: stats.total_pdfs.toString() },
    { ...stats_data[2], value: stats.total_mentions.toString() },
    { ...stats_data[3] },
  ];

  return (
    <div className="p-8 lg:p-12 space-y-12 max-w-[1600px] mx-auto">
      {/* Header */}
      <header className="flex flex-col md:flex-row md:items-center justify-between gap-6">
        <div>
          <h1 className="text-4xl font-black text-white font-outfit tracking-tight mb-2 flex items-center gap-4">
             <div className="p-3 rounded-2xl bg-indigo-500/10 border border-indigo-500/20">
                <LayoutDashboard className="text-indigo-400" size={32} />
             </div>
             Evolet Intelligence
          </h1>
          <p className="text-slate-400 font-medium">Real-time clinical entity extraction & analytics</p>
        </div>
        
        <div className="flex items-center gap-4">
           <div className="relative group">
              <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500 group-hover:text-indigo-400 transition-colors" size={18} />
              <input 
                type="text" 
                placeholder="Search records..." 
                className="bg-slate-900/50 border border-slate-800 rounded-2xl py-3 pl-12 pr-6 text-sm text-white placeholder:text-slate-600 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 w-64 backdrop-blur-md transition-all"
              />
           </div>
           <button className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white px-6 py-3 rounded-2xl font-bold shadow-lg shadow-indigo-600/20 transition-all active:scale-95">
              <Zap size={18} />
              New Run
           </button>
        </div>
      </header>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-6">
        {current_stats.map((stat, i) => (
          <motion.div 
            key={i}
            whileHover={{ y: -5 }}
            className="stat-card p-8 rounded-[2.5rem] bg-[#11111d]/50 backdrop-blur-xl border border-slate-800/40 group relative overflow-hidden"
          >
            <div className={cn("absolute top-0 right-0 w-32 h-32 opacity-10 blur-3xl rounded-full -mr-16 -mt-16 transition-all group-hover:opacity-20", stat.bg)} />
            <div className="flex items-start justify-between mb-6">
              <div className={cn("p-4 rounded-2xl transition-colors", stat.bg)}>
                <stat.icon className={stat.color} size={24} />
              </div>
              <div className="flex flex-col items-end">
                <span className="text-xs font-bold uppercase tracking-widest text-slate-500 mb-1">Status</span>
                <div className="h-1.5 w-1.5 bg-emerald-500 rounded-full animate-pulse shadow-[0_0_8px_rgba(16,185,129,0.6)]" />
              </div>
            </div>
            <h3 className="text-3xl font-black text-white font-outfit mb-1">{stat.value}</h3>
            <p className="text-slate-400 font-bold text-xs uppercase tracking-wider">{stat.name}</p>
          </motion.div>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Main Chart */}
        <div className="main-chart lg:col-span-2 p-10 rounded-[3rem] bg-[#11111d]/50 backdrop-blur-xl border border-slate-800/40 relative overflow-hidden">
          <div className="flex items-center justify-between mb-8">
             <div>
                <h2 className="text-xl font-bold text-white font-outfit mb-1">Extraction Velocity</h2>
                <p className="text-xs text-slate-500 font-bold uppercase tracking-widest">7 Day Activity</p>
             </div>
             <div className="flex items-center gap-4 bg-slate-900/50 p-1.5 rounded-xl border border-slate-800/40">
                <button className="px-4 py-1.5 rounded-lg bg-indigo-500 text-xs font-bold text-white">Reports</button>
                <button className="px-4 py-1.5 rounded-lg text-xs font-bold text-slate-400 hover:text-white transition-colors">Entities</button>
             </div>
          </div>
          <div className="h-[300px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData}>
                <defs>
                  <linearGradient id="colorValue" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3}/>
                    <stop offset="95%" stopColor="#6366f1" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#1e1e2d" />
                <XAxis dataKey="name" axisLine={false} tickLine={false} tick={{fill: '#64748b', fontSize: 12, fontWeight: 'bold'}} dy={10} />
                <YAxis hide />
                <RechartsTooltip 
                  contentStyle={{ backgroundColor: '#0f172a', border: '1px solid #1e293b', borderRadius: '12px' }}
                  itemStyle={{ color: '#fff', fontWeight: 'bold' }}
                />
                <Area 
                  type="monotone" 
                  dataKey="value" 
                  stroke="#6366f1" 
                  strokeWidth={4} 
                  fillOpacity={1} 
                  fill="url(#colorValue)" 
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* System Health Radar */}
        <div className="main-chart p-10 rounded-[3rem] bg-[#11111d]/50 backdrop-blur-xl border border-slate-800/40 flex flex-col">
          <h2 className="text-xl font-bold text-white font-outfit mb-8 flex items-center gap-3">
             <Cpu size={20} className="text-emerald-400" /> Pipeline Health
          </h2>
          <div className="flex-1 min-h-[300px]">
            <ResponsiveRadar
                data={radarData}
                keys={['value']}
                indexBy="key"
                maxValue="auto"
                margin={{ top: 40, right: 80, bottom: 40, left: 80 }}
                borderColor={{ from: 'color' }}
                gridLabelOffset={36}
                dotSize={10}
                dotColor={{ theme: 'background' }}
                dotBorderWidth={2}
                colors={['#10b981']}
                blendMode="multiply"
                motionConfig="wobbly"
                legends={[]}
                theme={{
                   dots: { text: { fill: '#fff', fontSize: 10 } },
                   axis: { ticks: { text: { fill: '#64748b', fontSize: 10, fontWeight: 'bold' } } },
                   grid: { line: { stroke: '#1e1e2d', strokeWidth: 1 } }
                }}
            />
          </div>
        </div>
      </div>

      {/* New Row: Category Distribution & Recent Activity */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
         <div className="lg:col-span-1 p-10 rounded-[3rem] bg-[#11111d]/50 backdrop-blur-xl border border-slate-800/40 flex flex-col min-h-[400px]">
            <h2 className="text-xl font-bold text-white font-outfit mb-8 flex items-center gap-3">
               <Tag size={20} className="text-amber-400" /> Entity Distribution
            </h2>
            <div className="flex-1">
               <ResponsiveBar
                  data={category_distribution}
                  keys={['count']}
                  indexBy="category"
                  margin={{ top: 20, right: 30, bottom: 50, left: 100 }}
                  padding={0.3}
                  layout="horizontal"
                  colors={['#f59e0b']}
                  borderRadius={8}
                  axisLeft={{
                     tickSize: 0,
                     tickPadding: 16,
                  }}
                  axisBottom={{
                     tickSize: 0,
                     tickPadding: 16,
                  }}
                  theme={{
                     axis: {
                        ticks: { text: { fill: '#64748b', fontSize: 11, fontWeight: 'bold' } }
                     },
                     grid: { line: { stroke: '#1e1e2d' } }
                  }}
                  enableGridY={false}
                  labelSkipWidth={12}
                  labelSkipHeight={12}
                  labelTextColor="#000"
               />
            </div>
         </div>

         <div className="lg:col-span-2 p-10 rounded-[3rem] bg-[#11111d]/50 backdrop-blur-xl border border-slate-800/40">
            <h2 className="text-xl font-bold text-white font-outfit mb-8 flex items-center gap-3">
               <Clock size={20} className="text-indigo-400" /> Recent Extraction Pipelines
            </h2>
            <div className="space-y-4">
               {(data?.recent_runs || []).map((run: any) => (
                  <div key={run.id} className="flex items-center gap-6 p-4 rounded-2xl bg-slate-900/40 border border-slate-800/60 hover:border-indigo-500/30 transition-all">
                     <div className={cn(
                        "w-12 h-12 rounded-xl flex items-center justify-center font-bold",
                        run.status === 'completed' ? "bg-emerald-500/10 text-emerald-400" : "bg-indigo-500/10 text-indigo-400"
                     )}>
                        {run.status === 'completed' ? <CheckCircle2 size={24} /> : <Activity size={24} />}
                     </div>
                     <div className="flex-1">
                        <div className="flex justify-between items-center mb-1">
                           <h4 className="font-bold text-white">{run.name}</h4>
                           <span className="text-[10px] uppercase font-bold tracking-widest text-slate-500">{run.status}</span>
                        </div>
                        <div className="w-full bg-slate-900 rounded-full h-1.5 overflow-hidden">
                           <motion.div 
                              initial={{ width: 0 }}
                              animate={{ width: `${run.progress}%` }}
                              className="bg-indigo-500 h-full"
                           />
                        </div>
                     </div>
                     <div className="text-right">
                        <p className="text-xs font-bold text-slate-300">{run.total_pdfs} Files</p>
                        <p className="text-[10px] text-slate-600">{run.started_at ? formatDate(run.started_at) : 'N/A'}</p>
                     </div>
                     <ChevronRight className="text-slate-700" size={20} />
                  </div>
               ))}
               {(data?.recent_runs || []).length === 0 && (
                  <div className="text-center py-12 border-2 border-dashed border-slate-800/40 rounded-[2rem]">
                     <RefreshCcw className="text-slate-700 mx-auto mb-4" size={32} />
                     <p className="text-slate-500 font-bold">No active pipelines detected</p>
                  </div>
               )}
            </div>
         </div>
      </div>
    </div>
  );
}
