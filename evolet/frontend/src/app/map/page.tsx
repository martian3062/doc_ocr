"use client";

import React, { useEffect, useRef, useState, useMemo } from "react";
import * as d3 from "d3";
import { motion, AnimatePresence } from "framer-motion";
import { 
  Share2, 
  Search, 
  Layers, 
  Info, 
  Maximize2, 
  ZoomIn, 
  ZoomOut,
  ChevronLeft,
  X,
  Database,
  Activity,
  User,
  RefreshCcw
} from "lucide-react";
import { getPatientList, getKnowledgeMap } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Node extends d3.SimulationNodeDatum {
  id: string;
  label: string;
  type: "category" | "mention";
  val: number;
  category?: string;
  value?: string;
}

interface Link extends d3.SimulationLinkDatum<Node> {
  source: string | Node;
  target: string | Node;
  type: string;
}

export default function KnowledgeMapPage() {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [patients, setPatients] = useState<any[]>([]);
  const [selectedPatientId, setSelectedPatientId] = useState<string | null>(null);
  const [data, setData] = useState<{ nodes: Node[], links: Link[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);

  // Fetch initial patient list
  useEffect(() => {
    getPatientList().then(res => {
      setPatients(res.data.patients);
      if (res.data.patients.length > 0) {
        setSelectedPatientId(res.data.patients[0].id);
      }
    });
  }, []);

  // Fetch data for the map
  useEffect(() => {
    if (!selectedPatientId) return;
    setLoading(true);
    getKnowledgeMap(selectedPatientId).then(res => {
      setData(res.data);
      setLoading(false);
    }).catch(err => {
      console.error(err);
      setLoading(false);
    });
  }, [selectedPatientId]);

  // D3 Visualization
  useEffect(() => {
    if (!data || !svgRef.current || !containerRef.current) return;

    const width = containerRef.current.clientWidth;
    const height = containerRef.current.clientHeight;

    const svg = d3.select(svgRef.current)
      .attr("viewBox", [0, 0, width, height])
      .attr("width", width)
      .attr("height", height);

    svg.selectAll("*").remove();

    const g = svg.append("g");

    // Zoom behavior
    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.1, 4])
      .on("zoom", (event) => {
        g.attr("transform", event.transform);
      });

    svg.call(zoom);

    // Forces
    const simulation = d3.forceSimulation<Node>(data.nodes)
      .force("link", d3.forceLink<Node, Link>(data.links).id(d => d.id).distance(100))
      .force("charge", d3.forceManyBody().strength(-300))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collision", d3.forceCollide<Node>().radius(d => (d as Node).val * 3 + 10));

    // Links
    const link = g.append("g")
      .attr("stroke", "#1e1e2d")
      .attr("stroke-opacity", 0.6)
      .selectAll("line")
      .data(data.links)
      .join("line")
      .attr("stroke-width", 1.5);

    // Nodes
    const node = g.append("g")
      .selectAll<SVGGElement, Node>("g")
      .data(data.nodes)
      .join("g")
      .call(d3.drag<SVGGElement, Node>()
        .on("start", dragstarted)
        .on("drag", dragged)
        .on("end", dragended))
      .on("click", (event, d) => {
        setSelectedNode(d);
      });

    // Node circles with glow
    node.append("circle")
      .attr("r", d => d.val * 1.5 + 5)
      .attr("fill", d => d.type === "category" ? "#6366f1" : "rgba(115, 115, 140, 0.2)")
      .attr("stroke", d => d.type === "category" ? "rgba(99, 102, 241, 0.4)" : "rgba(255, 255, 255, 0.1)")
      .attr("stroke-width", d => d.type === "category" ? 4 : 1)
      .attr("class", "transition-all duration-300");

    // Labels
    node.append("text")
      .attr("dy", d => d.val * 1.5 + 20)
      .attr("text-anchor", "middle")
      .attr("fill", d => d.type === "category" ? "#fff" : "#94a3b8")
      .attr("font-size", d => d.type === "category" ? "12px" : "10px")
      .attr("font-weight", d => d.type === "category" ? "bold" : "normal")
      .text(d => d.label);

    simulation.on("tick", () => {
      link
        .attr("x1", d => (d.source as any).x)
        .attr("y1", d => (d.source as any).y)
        .attr("x2", d => (d.target as any).x)
        .attr("y2", d => (d.target as any).y);

      node
        .attr("transform", d => `translate(${d.x},${d.y})`);
    });

    function dragstarted(event: any) {
      if (!event.active) simulation.alphaTarget(0.3).restart();
      event.subject.fx = event.subject.x;
      event.subject.fy = event.subject.y;
    }

    function dragged(event: any) {
      event.subject.fx = event.x;
      event.subject.fy = event.y;
    }

    function dragended(event: any) {
      if (!event.active) simulation.alphaTarget(0);
      event.subject.fx = null;
      event.subject.fy = null;
    }

    return () => {
      simulation.stop();
    };
  }, [data]);

  return (
    <div className="flex h-screen overflow-hidden bg-[#06060a] relative">
      {/* Sidebar Selector */}
      <div className="w-80 border-r border-[#1e1e2d] bg-[#0d0d16]/80 backdrop-blur-xl flex flex-col p-6 z-20">
        <header className="mb-8">
          <div className="flex items-center gap-2 mb-2">
            <Share2 className="text-indigo-400" size={24} />
            <h1 className="text-2xl font-bold font-outfit text-white">Knowledge Map</h1>
          </div>
          <p className="text-xs text-slate-500 uppercase tracking-widest font-bold">Relational Clinical Graph</p>
        </header>

        <div className="relative mb-6">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-600" size={16} />
          <input 
            type="text" 
            placeholder="Select Patient..."
            className="w-full bg-[#11111d] border border-slate-800 rounded-xl py-2 pl-10 pr-4 text-sm text-slate-300 focus:outline-none focus:ring-1 focus:ring-indigo-500/50"
          />
        </div>

        <div className="flex-1 overflow-y-auto space-y-2 custom-scrollbar pr-2">
          {patients.map(p => (
            <button
              key={p.id}
              onClick={() => setSelectedPatientId(p.id)}
              className={cn(
                "w-full flex items-center gap-3 p-3 rounded-xl border transition-all text-left group",
                selectedPatientId === p.id 
                  ? "bg-indigo-500/10 border-indigo-500/40 text-indigo-400 shadow-lg shadow-indigo-500/5" 
                  : "bg-transparent border-transparent text-slate-500 hover:bg-slate-800/40 hover:text-slate-300"
              )}
            >
              <div className={cn(
                "w-8 h-8 rounded-lg flex items-center justify-center shrink-0 transition-colors",
                selectedPatientId === p.id ? "bg-indigo-500 text-white" : "bg-slate-800 text-slate-600 group-hover:text-slate-400"
              )}>
                <User size={16} />
              </div>
              <div className="overflow-hidden">
                <p className="text-sm font-semibold truncate leading-tight">{p.display_name}</p>
                <p className="text-[10px] font-mono opacity-60 truncate">{p.code}</p>
              </div>
            </button>
          ))}
        </div>

        <div className="mt-auto pt-6 border-t border-[#1e1e2d] text-[10px] text-slate-600 flex justify-between">
          <span>{data?.nodes.length || 0} Entities</span>
          <span>{data?.links.length || 0} Relations</span>
        </div>
      </div>

      {/* Main Map Area */}
      <div ref={containerRef} className="flex-1 relative bg-[radial-gradient(#1e1e2d_1px,transparent_1px)] [background-size:32px_32px]">
        {loading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-[#06060a]/50 backdrop-blur-sm">
            <motion.div 
              animate={{ rotate: 360 }}
              transition={{ repeat: Infinity, duration: 1, ease: "linear" }}
              className="w-10 h-10 border-4 border-indigo-500 border-t-transparent rounded-full shadow-lg shadow-indigo-500/20"
            />
          </div>
        )}
        
        <svg ref={svgRef} className="w-full h-full cursor-move" />

        {/* Legend / Overlay */}
        <div className="absolute top-8 right-8 flex flex-col gap-3">
          <div className="glass p-4 rounded-2xl border border-slate-800/60 flex flex-col gap-4 min-w-[140px]">
             <div className="flex items-center gap-2 text-xs text-indigo-400 font-bold uppercase tracking-wider">
               <Layers size={14} /> Legend
             </div>
             <div className="space-y-2">
               <div className="flex items-center gap-2">
                 <div className="w-3 h-3 rounded-full bg-indigo-500" />
                 <span className="text-[11px] text-slate-300">Category</span>
               </div>
               <div className="flex items-center gap-2">
                 <div className="w-3 h-3 rounded-full border border-slate-600 bg-slate-800/40" />
                 <span className="text-[11px] text-slate-300">Entity (Mention)</span>
               </div>
             </div>
          </div>

          <div className="flex gap-2 justify-end">
            <button className="p-3 glass rounded-xl text-slate-400 hover:text-white transition-all"><ZoomIn size={18} /></button>
            <button className="p-3 glass rounded-xl text-slate-400 hover:text-white transition-all"><ZoomOut size={18} /></button>
            <button className="p-3 glass rounded-xl text-slate-400 hover:text-white transition-all" onClick={() => setSelectedNode(null)}><RefreshCcw size={18} /></button>
          </div>
        </div>
      </div>

      {/* Detail Overlay */}
      <AnimatePresence>
        {selectedNode && (
          <motion.div
            initial={{ opacity: 0, x: 100 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 100 }}
            className="absolute top-8 bottom-8 right-8 w-80 glass p-8 rounded-3xl z-30 flex flex-col shadow-2xl overflow-hidden"
          >
            <div className="absolute top-0 left-0 w-full h-1 bg-indigo-500" />
            <button 
              onClick={() => setSelectedNode(null)}
              className="absolute top-4 right-4 p-2 text-slate-500 hover:text-white transition-colors"
            >
              <X size={20} />
            </button>

            <div className="mb-8">
              <div className="inline-flex p-3 rounded-2xl bg-indigo-500/10 text-indigo-400 mb-4">
                {selectedNode.type === 'category' ? <Share2 size={24} /> : <Activity size={24} />}
              </div>
              <h2 className="text-2xl font-bold font-outfit text-white mb-1 leading-tight">{selectedNode.label}</h2>
              <p className="text-xs text-indigo-500 uppercase tracking-widest font-bold">
                {selectedNode.type === 'category' ? 'Clinical Dimension' : 'Extracted Entity'}
              </p>
            </div>

            <div className="space-y-6 flex-1">
              {selectedNode.type === 'mention' && (
                <div className="p-4 rounded-2xl bg-slate-900/50 border border-slate-800/60">
                   <p className="text-[10px] uppercase font-bold text-slate-500 mb-2 tracking-wider">Categorized As</p>
                   <p className="text-sm font-medium text-indigo-400">{selectedNode.category}</p>
                </div>
              )}

              <div className="p-4 rounded-2xl bg-slate-900/50 border border-slate-800/60">
                 <p className="text-[10px] uppercase font-bold text-slate-500 mb-2 tracking-wider">Reference Context</p>
                 <p className="text-xs text-slate-300 italic">"Patient shows persistent symptoms of {selectedNode.label}, consistent with previously observed data patterns in latest reports."</p>
              </div>

              <div className="grid grid-cols-2 gap-3">
                 <div className="p-4 rounded-2xl bg-[#06060a] border border-slate-800/60">
                    <p className="text-[10px] text-slate-500 mb-1">Confidence</p>
                    <p className="font-bold text-white">98.2%</p>
                 </div>
                 <div className="p-4 rounded-2xl bg-[#06060a] border border-slate-800/60">
                    <p className="text-[10px] text-slate-500 mb-1">Citations</p>
                    <p className="font-bold text-white">4 Docs</p>
                 </div>
              </div>
            </div>

            <button className="mt-8 w-full py-4 bg-indigo-600 hover:bg-indigo-500 text-white rounded-2xl font-bold transition-all shadow-lg shadow-indigo-600/20">
              View Detailed Clinical Profile
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
