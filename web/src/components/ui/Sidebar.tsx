import { cn } from "@/lib/utils";
import { ListChecks, BarChart3, Bot, Bug, Cog, Star } from "lucide-react";
import { Badge } from "./badge";

interface SidebarProps {
  activeTab: string;
  setActiveTab: (tab: string) => void;
  appVersion: string;
  skillCount?: number;
}

const navItems = [
  { id: "overview", label: "Overview", icon: ListChecks },
  { id: "set-up", label: "Setup", icon: Cog },
  { id: "independent", label: "Automation", icon: Bot },
  { id: "skills", label: "Skills", icon: Star },
  { id: "statistics", label: "Statistics", icon: BarChart3 },
  { id: "debug", label: "Debug", icon: Bug },
];

export function Sidebar({ activeTab, setActiveTab, appVersion, skillCount }: SidebarProps) {
  return (
    <div className="w-64 h-screen sticky top-0 flex flex-col">
      <div className="p-3 py-4 absolute">
        <h1 className="text-2xl font-bold text-primary tracking-tight whitespace-nowrap">Mirako Machine</h1>
        <span className="text-sm block w-full text-right font-bold text-slate-400 -mt-2">v{appVersion || "Loading..."}</span>
      </div>
      <nav className="flex-1 content-center px-3 space-y-3">
        {navItems.map((item) => (
          <button
            key={item.id}
            onClick={() => setActiveTab(item.id)}
            className={cn(
              "uma-btn w-full grid grid-cols-[1fr_auto_1fr] items-center gap-3 px-3 py-3 rounded-md transition-colors font-medium",
              activeTab === item.id
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-accent hover:text-accent-foreground cursor-pointer "
            )}
          >
            <item.icon className="w-4 h-4 justify-self-start" />
            {item.label}
            {item.id === "skills" && (skillCount ?? 0) > 0 && (
              <Badge variant="default" className={cn("text-xs px-2 justify-self-end", activeTab === item.id && "bg-card text-card-foreground")}>
                {skillCount}
              </Badge>
            )}
          </button>
        ))}
      </nav>
    </div>
  );
}