import { LayoutDashboard, Network, BookOpen, Lightbulb, SquareFunction, FlaskConical, Table2, ChartNoAxesCombined, FileText, Folder, ListTree } from "lucide-react";

export const navigation = [
  ["overview", "研究概览", "Overview", LayoutDashboard],
  ["workspace", "研究图", "Graph", Network],
  ["library", "论文库", "Library", BookOpen],
  ["ideas", "研究想法", "Ideas", Lightbulb],
  ["theory", "理论工作台", "Theory", SquareFunction],
  ["experiments", "实验", "Experiments", FlaskConical],
  ["runs", "运行", "Runs", ListTree],
  ["data", "数据分析", "Data & analysis", Table2],
  ["figures", "图表工作室", "Figure studio", ChartNoAxesCombined],
  ["paper", "论文", "Paper", FileText],
  ["files", "项目文件", "Files", Folder],
] as const;

export const navigationGroups = [
  { path: "workspace", zh: "工作台", en: "Workspace", icon: Network, children: ["overview", "workspace", "ideas", "theory"] },
  { path: "library", zh: "论文库", en: "Library", icon: BookOpen, children: ["library"] },
  { path: "experiments", zh: "实验与结果", en: "Experiments & Results", icon: FlaskConical, children: ["experiments", "runs", "data", "figures"] },
  { path: "paper", zh: "论文", en: "Paper", icon: FileText, children: ["paper"] },
  { path: "files", zh: "文件", en: "Files", icon: Folder, children: ["files"] },
];

export function groupForPath(pathname: string) {
  const page = pathname.split("/").at(-1) || "";
  return navigationGroups.find((group) => group.children.includes(page));
}
