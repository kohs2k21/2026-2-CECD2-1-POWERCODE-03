import { useRef, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { detectionPages } from "../../app/routePaths";
import { Button } from "../../components/ui/button";

export const DetectionLayout = () => {
  const [expanded, setExpanded] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);
  return (
    <section className="detection-layout">
      <aside
        className="detection-sidebar"
        aria-label="탐지 관리 메뉴"
        onKeyDown={(event) => {
          if (event.key === "Escape" && expanded) {
            event.preventDefault();
            setExpanded(false);
            toggleRef.current?.focus();
          }
        }}
      >
        <Button
          variant="ghost"
          ref={toggleRef}
          className="detection-menu-toggle"
          type="button"
          aria-expanded={expanded}
          aria-controls="detection-menu"
          onClick={() => setExpanded((value) => !value)}
        >
          탐지 관리 메뉴
        </Button>
        <nav
          id="detection-menu"
          className={expanded ? "detection-menu is-expanded" : "detection-menu"}
        >
          {["실험", "운영"].map((group) => (
            <section key={group}>
              <h2>{group}</h2>
              {detectionPages
                .filter((page) => page.group === group)
                .map((page) => (
                  <NavLink
                    className="ui-nav-link"
                    key={page.path}
                    to={
                      page.path === "/detection/create"
                        ? page.path + "?tab=dataset"
                        : page.path
                    }
                    onClick={() => {
                      if (expanded) {
                        setExpanded(false);
                        toggleRef.current?.focus();
                      }
                    }}
                  >
                    {page.label}
                  </NavLink>
                ))}
            </section>
          ))}
        </nav>
      </aside>
      <div className="detection-content">
        <Outlet />
      </div>
    </section>
  );
};
