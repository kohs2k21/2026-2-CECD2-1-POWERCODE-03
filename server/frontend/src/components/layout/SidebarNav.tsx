import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Button } from "../ui/button";

export type SidebarNavItem<TId extends string> = {
  id: TId;
  label: string;
  count?: number;
  icon: ReactNode;
  className?: string;
  href?: string;
};

export type SidebarNavGroup<TId extends string> = {
  title: string;
  items: SidebarNavItem<TId>[];
};

type SidebarNavProps<TId extends string> = {
  activeId: TId;
  groups: SidebarNavGroup<TId>[];
  onSelect: (id: TId) => void;
};

export const SidebarNav = <TId extends string>({
  activeId,
  groups,
  onSelect,
}: SidebarNavProps<TId>) => (
  <aside className="section-sidebar">
    {groups.map((group) => (
      <section key={group.title} className="section-sidebar__group">
        <h2>{group.title}</h2>
        {group.items.map((item) => (
          <Button
            variant="ghost"
            asChild={Boolean(item.href)}
            key={item.id}
            className={`section-sidebar-button ${item.className ?? ""} ${activeId === item.id ? "section-sidebar-button--active" : ""}`}
            type="button"
            aria-current={activeId === item.id ? "page" : undefined}
            onClick={item.href ? undefined : () => onSelect(item.id)}
          >
            {item.href ? (
              <Link to={item.href}>
                <span className="section-sidebar-button__icon">
                  {item.icon}
                </span>
                <span>{item.label}</span>
                {typeof item.count === "number" && (
                  <strong>{item.count}</strong>
                )}
              </Link>
            ) : (
              <>
                <span className="section-sidebar-button__icon">
                  {item.icon}
                </span>
                <span>{item.label}</span>
                {typeof item.count === "number" && (
                  <strong>{item.count}</strong>
                )}
              </>
            )}
          </Button>
        ))}
      </section>
    ))}
  </aside>
);
