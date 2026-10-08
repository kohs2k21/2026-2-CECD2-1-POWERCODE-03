import { useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { createTabs } from "../../app/routePaths";

export const DetectionPage = ({ title }: { title: string }) => (
  <section className="detection-page">
    <h1>{title}</h1>
  </section>
);
export const CreatePage = () => {
  const [params, setParams] = useSearchParams();
  const selected = params.get("tab");
  const activeTab =
    createTabs.find((tab) => tab.id === selected) ?? createTabs[0];
  useEffect(() => {
    if (!createTabs.some((tab) => tab.id === selected)) {
      const next = new URLSearchParams(params);
      next.set("tab", "data-features");
      setParams(next, { replace: true });
    }
  }, [selected, params, setParams]);
  return (
    <section className="detection-page">
      <h1>모델·룰 만들기</h1>
      <nav className="create-tabs" aria-label="탐지 구성 편집 영역">
        {createTabs.map((tab) => {
          const next = new URLSearchParams(params);
          next.set("tab", tab.id);
          return (
            <Link
              className="ui-nav-link"
              key={tab.id}
              to={{ search: "?" + next.toString() }}
              aria-current={activeTab.id === tab.id ? "page" : undefined}
            >
              {tab.label}
            </Link>
          );
        })}
      </nav>
      <section
        className="creation-section"
        aria-labelledby="creation-section-title"
      >
        <h2 id="creation-section-title">{activeTab.label}</h2>
      </section>
    </section>
  );
};
