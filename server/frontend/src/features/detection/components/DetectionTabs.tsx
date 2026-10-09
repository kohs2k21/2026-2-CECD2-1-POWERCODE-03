import { useEffect } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";

export type DetectionTabItem = {
  id: string;
  label: string;
  to: string;
  state?: unknown;
};

export const DetectionTabs = ({
  items,
  activeId,
  ariaLabel,
}: {
  items: readonly DetectionTabItem[];
  activeId: string;
  ariaLabel: string;
}) => {
  const location = useLocation();
  return (
    <nav className="create-tabs" aria-label={ariaLabel}>
      {items.map((item) => (
        <Link
          key={item.id}
          to={item.to}
          state={item.state === undefined ? location.state : item.state}
          aria-current={activeId === item.id ? "page" : undefined}
        >
          {item.label}
        </Link>
      ))}
    </nav>
  );
};

// Normalize legacy deep links without removing their selection or filters.
export const useDetectionTab = (ids: readonly string[], fallback: string) => {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const requested = params.get("tab");
  const activeId = requested && ids.includes(requested) ? requested : fallback;
  useEffect(() => {
    if (requested === activeId) return;
    const next = new URLSearchParams(params);
    next.set("tab", activeId);
    setParams(next, { replace: true, state: location.state });
  }, [activeId, requested, params, setParams, location.state]);
  return activeId;
};

export const detectionTabHref = (params: URLSearchParams, id: string) => {
  const next = new URLSearchParams(params);
  next.set("tab", id);
  return `?${next.toString()}`;
};
