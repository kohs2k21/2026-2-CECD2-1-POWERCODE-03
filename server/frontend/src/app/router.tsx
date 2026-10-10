import { IconActivity, IconBrain, IconHome } from "@tabler/icons-react";
import type { NavItem, UserRole } from "../types/app";

export const navItems: NavItem[] = [
  {
    id: "home",
    label: "운영 현황",
    description: "관제 요약 및 위젯",
    Icon: IconHome,
  },
  {
    id: "analysis",
    label: "이상 분석",
    description: "의심 로그 분석",
    Icon: IconActivity,
  },
  {
    id: "model",
    label: "탐지 관리",
    description: "실험 및 운영 구성",
    adminOnly: true,
    Icon: IconBrain,
  },
];

export const getVisibleNavItems = (role: UserRole) => {
  return navItems.filter((item) => role === "admin" || !item.adminOnly);
};
