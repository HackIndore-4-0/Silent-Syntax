import {
  LayoutDashboard, GitBranch, Timer, Coins, CircleAlert, Wrench, Cpu,
  ClipboardCheck, ShieldCheck, Bot, Zap, RotateCcw, GitCompare,
  FileCheck, FileText, Bug, TrendingDown, Sparkles, PlayCircle,
  Database, FolderKanban, BarChart2, Lightbulb, KeyRound, UserRound, Users,
} from 'lucide-react'

const ICONS = {
  LayoutDashboard, GitBranch, Timer, Coins, CircleAlert, Wrench, Cpu,
  ClipboardCheck, ShieldCheck, Bot, Zap, RotateCcw, GitCompare,
  FileCheck, FileText, Bug, TrendingDown, Sparkles, PlayCircle,
  Database, FolderKanban, BarChart2, Lightbulb, KeyRound, UserRound, Users,
}

export function NavIcon({ name, size = 18 }) {
  const Cmp = ICONS[name]
  if (!Cmp) return null
  return <Cmp size={size} strokeWidth={1.75} />
}
