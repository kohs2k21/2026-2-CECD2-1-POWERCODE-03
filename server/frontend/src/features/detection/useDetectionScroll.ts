import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLocation, useNavigationType } from "react-router-dom";

/** 해시 대상이 데이터 조회 뒤에 생기는 화면을 기다리는 최대 시간·간격. */
export const SECTION_WAIT_MS = 8000;
const SECTION_POLL_MS = 100;

const hasDom = () => typeof window !== "undefined" && typeof document !== "undefined" && typeof window.scrollTo === "function";

export const hashId = (hash: string) => {
  if (!hash || hash === "#") return null;
  try {
    return decodeURIComponent(hash.slice(1));
  } catch {
    return hash.slice(1);
  }
};

/** 대상보다 위에 아직 조회 중인 영역이 있으면 스크롤한 뒤 대상이 밀려나므로 끝날 때까지 기다린다. */
const loadingAbove = (target: Element) =>
  [...(document.getElementById("workspace-content") ?? document).querySelectorAll('[data-slot="loading-state"]')].some(
    (node) => !target.contains(node) && (target.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_PRECEDING) !== 0,
  );

/**
 * 해시 영역으로 스크롤: 대상이 생기고 그 위 조회가 끝나면 한 번 스크롤한다. 시간 안에 대상이 없으면(조건부 영역) 아무것도 하지 않는다.
 * 기다리는 동안 사용자가 직접 스크롤·키 입력하면 멈춘다. 고정 머리글·고정 줄 높이는 문서 scroll-padding-top이 피한다.
 */
export const scrollToSection = (id: string, waitMs = SECTION_WAIT_MS) => {
  const started = Date.now();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const stop = () => {
    clearTimeout(timer);
    window.removeEventListener("wheel", stop);
    window.removeEventListener("touchstart", stop);
    window.removeEventListener("keydown", stop);
  };
  const attempt = () => {
    const target = document.getElementById(id);
    const timedOut = Date.now() - started >= waitMs;
    if (target && (timedOut || !loadingAbove(target))) {
      stop();
      target.scrollIntoView?.({ block: "start" });
      return;
    }
    if (timedOut) return stop();
    timer = setTimeout(attempt, SECTION_POLL_MS);
  };
  window.addEventListener("wheel", stop, { passive: true });
  window.addEventListener("touchstart", stop, { passive: true });
  window.addEventListener("keydown", stop);
  attempt();
  return stop;
};

/**
 * 탐지 설정 화면 이동 시 스크롤(라우터는 스크롤을 건드리지 않아 이전 화면 위치가 남는다 — 하위 화면이 중간부터 보이던 원인).
 * - 해시(#영역 id)가 있으면 그 영역으로(목차 클릭·다른 화면에서 목차 클릭 후 이동·해시 주소로 첫 진입). 다른 화면에서 온 이동이면 먼저 맨 위로
 * - 경로가 바뀌었거나 해시만 지운 이동이면 맨 위로. 쿼리만 바뀐 이동(탭·단계)은 그대로 둔다
 * - 브라우저 뒤로/앞으로(POP)는 건드리지 않는다: 브라우저 기본 위치 복원을 따른다
 * 페인트 전(layout effect)에 맨 위로 올려 중간 화면이 비치지 않게 하고, 자식의 작업 영역 스크롤(step 주소, passive effect)이 그 뒤에 이긴다.
 */
export const useDetectionScroll = () => {
  const location = useLocation();
  const navigationType = useNavigationType();
  const previous = useRef<{ pathname: string; search: string; hash: string } | null>(null);
  useLayoutEffect(() => {
    const before = previous.current;
    previous.current = { pathname: location.pathname, search: location.search, hash: location.hash };
    if (!hasDom()) return;
    const target = hashId(location.hash);
    if (navigationType === "POP" && before) return;
    const pathChanged = !before || before.pathname !== location.pathname;
    if (target) {
      // 다른 화면에서 온 해시 이동(하위 화면 → 첫 화면 목차)은 이전 위치를 먼저 지운다. 대상을 기다리는 동안 이전 화면 위치에 머물지 않게 한다.
      if (before && pathChanged) window.scrollTo(0, 0);
      return scrollToSection(target);
    }
    if (navigationType === "POP") return;
    const hashCleared = !!before && !!before.hash && before.search === location.search;
    if (pathChanged || hashCleared) window.scrollTo(0, 0);
  }, [location.key, location.pathname, location.search, location.hash, navigationType]);
};

/**
 * 목차 현재 영역(스크롤 위치 기준): 머리글·고정 줄 아래 선(scroll-padding-top)을 지난 마지막 영역, 아무것도 안 지났으면 첫 영역.
 * 페이지 끝까지 내려가 더 움직일 수 없으면 화면에 보이는 해시 영역(방금 누른 목차) 또는 마지막 영역.
 * sections가 null이면(하위 화면) 표시하지 않는다. sections는 고정 목록(detectionToc)을 넘겨 매 렌더 다시 구독하지 않게 한다.
 */
export const useActiveSection = (sections: readonly { id: string }[] | null, pinnedId: string | null) => {
  const [active, setActive] = useState<string | null>(null);
  useEffect(() => {
    if (!sections || !hasDom() || typeof window.requestAnimationFrame !== "function") {
      setActive(null);
      return;
    }
    let frame = 0;
    const update = () => {
      frame = 0;
      const present = sections.map((section) => document.getElementById(section.id)).filter((node): node is HTMLElement => node !== null);
      if (!present.length) return setActive(null);
      const root = document.documentElement;
      const line = (Number.parseFloat(getComputedStyle(root).scrollPaddingTop) || 0) + 1;
      const atBottom = window.scrollY + window.innerHeight >= root.scrollHeight - 2;
      if (atBottom && window.scrollY > 0) {
        const pinned = pinnedId ? present.find((node) => node.id === pinnedId) : undefined;
        const top = pinned?.getBoundingClientRect().top;
        return setActive(pinned && top !== undefined && top >= 0 && top < window.innerHeight ? pinned.id : present[present.length - 1].id);
      }
      let current = present[0].id;
      for (const node of present) if (node.getBoundingClientRect().top <= line) current = node.id;
      setActive(current);
    };
    const schedule = () => {
      if (!frame) frame = window.requestAnimationFrame(update);
    };
    schedule();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    // 조회가 끝나 영역이 생기거나 높이가 바뀌어도 다시 계산한다.
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(schedule);
    observer?.observe(document.body);
    return () => {
      if (frame) window.cancelAnimationFrame(frame);
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      observer?.disconnect();
    };
  }, [sections, pinnedId]);
  return active;
};
