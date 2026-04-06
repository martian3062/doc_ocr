import type { Metadata } from "next";
import { Inter, Outfit } from "next/font/google";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";
import { ThreeBackground } from "@/components/ThreeBackground";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });
const outfit = Outfit({ subsets: ["latin"], variable: "--font-outfit" });

export const metadata: Metadata = {
  title: "Evolet OCR | Clinical Intelligence",
  description: "Advanced medical report OCR and clinical entity mapping pipeline.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className={`${inter.variable} ${outfit.variable} font-sans bg-[#06060a] text-slate-200 overflow-x-hidden`}>
        <ThreeBackground />
        <div className="flex min-h-screen">
          <Sidebar />
          <main className="flex-1 transition-all duration-300 relative">
            <div className="absolute inset-0 mesh-bg opacity-30 -z-10" />
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
