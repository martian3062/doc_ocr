"use client";

import React, { useEffect, useRef } from 'react';
import * as d3 from 'd3';

interface Node extends d3.SimulationNodeDatum {
  id: string;
  label: string;
  type: 'category' | 'mention' | 'artifact';
  val: number;
  category?: string;
  value?: string;
  page_num?: number;
  backend?: string;
  x?: number;
  y?: number;
}

interface Link extends d3.SimulationLinkDatum<Node> {
  source: string | Node;
  target: string | Node;
  type: string;
}

interface KnowledgeGraphProps {
  data: {
    nodes: Node[];
    links: Link[];
  };
}

const CATEGORY_COLORS: Record<string, string> = {
  'gene': '#818cf8',
  'variant': '#f472b6',
  'drug': '#fbbf24',
  'treatment': '#34d399',
  'disease': '#fb7185',
  'marker': '#a78bfa',
  'handwriting': '#22d3ee',
  'body': '#38bdf8',
  'table': '#f97316',
  'stamp': '#ef4444',
  'embedded_figure': '#10b981',
  'default': '#6366f1'
};

export function KnowledgeGraph({ data }: KnowledgeGraphProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<SVGGElement>(null);

  useEffect(() => {
    if (!svgRef.current || !containerRef.current || !data || !data.nodes.length) return;

    const width = 1000;
    const height = 800;

    const svg = d3.select(svgRef.current);
    const container = d3.select(containerRef.current);
    container.selectAll("*").remove();

    // Define Filters & Gradients
    const defs = svg.select("defs").empty() ? svg.append("defs") : svg.select("defs");
    defs.html("");

    // Glow Filter
    const filter = defs.append("filter")
      .attr("id", "glow")
      .attr("x", "-50%")
      .attr("y", "-50%")
      .attr("width", "200%")
      .attr("height", "200%");
    
    filter.append("feGaussianBlur")
      .attr("stdDeviation", "3.5")
      .attr("result", "blur");
    
    filter.append("feComposite")
      .attr("in", "SourceGraphic")
      .attr("in2", "blur")
      .attr("operator", "over");

    // Simulation Setup
    const simulation = d3.forceSimulation<Node>(data.nodes)
      .force("link", d3.forceLink<Node, Link>(data.links).id(d => d.id).distance(140))
      .force("charge", d3.forceManyBody().strength(-600))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collision", d3.forceCollide<Node>().radius(d => d.val + 50));

    // Links with Gradient approach (simplified for now with single color per link)
    const link = container.append("g")
      .selectAll("line")
      .data(data.links)
      .join("line")
      .attr("stroke", d => {
         const targetNode = data.nodes.find(n => n.id === (typeof d.target === 'string' ? d.target : d.target.id));
         return CATEGORY_COLORS[targetNode?.category || 'default'] || CATEGORY_COLORS.default;
      })
      .attr("stroke-opacity", 0.2)
      .attr("stroke-width", 1.5)
      .attr("stroke-dasharray", "4,4");

    // Nodes
    const node = container.append("g")
      .selectAll("g")
      .data(data.nodes)
      .join("g")
      .attr("class", "node-group")
      .call(d3.drag<SVGGElement, Node>()
        .on("start", dragstarted)
        .on("drag", dragged)
        .on("end", dragended) as any);

    // Node Background Glow
    node.append("circle")
      .attr("r", d => d.val + 4)
      .attr("fill", d => CATEGORY_COLORS[d.category || (d.type === 'category' ? d.id.replace('cat_', '') : 'default')] || CATEGORY_COLORS.default)
      .attr("opacity", 0.15)
      .style("filter", "url(#glow)");

    // Main Node Circle
    node.append("circle")
      .attr("r", d => d.type === "artifact" ? Math.max(4, d.val - 1) : d.val)
      .attr("fill", "#0a0a0f")
      .attr("stroke", d => CATEGORY_COLORS[d.category || (d.type === 'category' ? d.id.replace('cat_', '') : 'default')] || CATEGORY_COLORS.default)
      .attr("stroke-width", d => d.type === "artifact" ? 1.5 : 2.5)
      .attr("class", "cursor-pointer transition-all duration-300 hover:stroke-white")
      .style("filter", "none");

    // Inner Dot for Categories
    node.filter(d => d.type === 'category')
      .append("circle")
      .attr("r", 3)
      .attr("fill", d => CATEGORY_COLORS[d.id.replace('cat_', '')] || CATEGORY_COLORS.default);

    node.filter(d => d.type === "artifact")
      .append("rect")
      .attr("x", -4)
      .attr("y", -4)
      .attr("width", 8)
      .attr("height", 8)
      .attr("rx", 2)
      .attr("fill", d => CATEGORY_COLORS[d.category || "default"] || CATEGORY_COLORS.default);

    // Labels
    const label = node.append("g")
        .attr("class", "label-group")
        .style("pointer-events", "none");

    label.append("rect")
        .attr("x", 12)
        .attr("y", -12)
        .attr("rx", 8)
        .attr("ry", 8)
        .attr("width", d => d.label.length * 7.5 + 20)
        .attr("height", 24)
        .attr("fill", "rgba(10, 10, 15, 0.9)")
        .attr("stroke", "rgba(255, 255, 255, 0.08)")
        .style("backdrop-filter", "blur(8px)");

    label.append("text")
      .text(d => d.type === "artifact" && d.page_num ? `${d.label} · p${d.page_num}` : d.label)
      .attr("x", 22)
      .attr("y", 4)
      .attr("fill", "#fff")
      .style("font-size", "11px")
      .style("font-weight", "800")
      .style("text-transform", "uppercase")
      .style("letter-spacing", "0.05em")
      .style("font-family", "Outfit");

    simulation.on("tick", () => {
      link
        .attr("x1", d => (d.source as Node).x!)
        .attr("y1", d => (d.source as Node).y!)
        .attr("x2", d => (d.target as Node).x!)
        .attr("y2", d => (d.target as Node).y!);

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

    // Zoom behavior
    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .extent([[0, 0], [width, height]])
      .scaleExtent([0.1, 4])
      .on("zoom", ({ transform }) => {
        container.attr("transform", transform);
      });

    svg.call(zoom);

  }, [data]);

  return (
    <div className="w-full h-full bg-gradient-to-br from-[#06060a] to-[#010103] rounded-[3rem] overflow-hidden border border-slate-900/50 shadow-2xl relative group">
      <div className="absolute inset-0 mesh-bg opacity-10 pointer-events-none group-hover:opacity-20 transition-opacity duration-1000" />
      <svg
        ref={svgRef}
        viewBox="0 0 1000 800"
        className="w-full h-full cursor-grab active:cursor-grabbing relative z-10"
      >
        <rect width="100%" height="100%" fill="transparent" />
        <g ref={containerRef} />
      </svg>
      
      {/* HUD Elements */}
      <div className="absolute bottom-8 right-8 z-20 flex gap-4">
         <div className="bg-[#0a0a0f]/80 backdrop-blur-md border border-slate-800/60 p-4 rounded-2xl flex gap-6 items-center shadow-2xl">
            <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Node Density</span>
            <div className="flex gap-1.5">
               {[1,2,3,4,5].map(i => <div key={i} className={cn("w-1 h-3 rounded-full", i <= 3 ? "bg-indigo-500" : "bg-slate-800")} />)}
            </div>
         </div>
      </div>
    </div>
  );
}

function cn(...classes: string[]) {
  return classes.filter(Boolean).join(' ');
}
