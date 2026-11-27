import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLocation, useNavigationType } from "react-router-dom";
import { useReducedMotion } from "motion/react";
import { afterPageSwap } from "@/components/layout/PageFade";
import { MOTION_EASE, SCROLL_MS, cubicBezier, scrollDuration } from "@/lib/motion";

/** 해시 대상이 데이터 조회 뒤에 생기는 화면을 기다리는 최대 시간(ui.md "움직임" 스크롤, lib/motion.ts SCROLL_MS). */
export const SECTION_WAIT_MS = SCROLL_MS.wait;

const hasDom = () => typeof window !== "undefined" && typeof document !== "undefined" && typeof window.scrollTo === "function";

export const hashId = (hash: string) => {
  if (!hash || hash === "#") return null;
  try {
    return decodeURIComponent(hash.slice(1));
  } catch {
    return hash.slice(1);
  }
};

/** 대상보다 위에 아직 조회 중인 영역(최초 로딩 표시·다시 조회 대기 표시)이 있으면 스크롤한 뒤 대상이 밀려나므로 끝날 때까지 기다린다. */
const loadingAbove = (target: Element) =>
  [...(document.getElementById("workspace-content") ?? document).querySelectorAll('[data-slot="loading-state"], [data-pending]')].some(
    (node) => !target.contains(node) && (target.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_PRECEDING) !== 0,
  );

const ease = cubicBezier(...MOTION_EASE.standard);
/** 사용자가 직접 움직이면(휠·터치·키·누름) 그 자리에서 멈춘다. */
const USER_INPUT = ["wheel", "touchstart", "keydown", "pointerdown"] as const;

/**
 * 해시 영역으로 부드럽게 스크롤(ui.md "움직임" 스크롤): 대상이 생기고 그 위 조회가 끝나면 출발한다. 시간 안에 대상이 없으면(조건부 영역) 아무것도 하지 않는다.
 * - 길이는 거리에 따라 SCROLL_MS.min~max(scrollDuration), 곡선 standard. 매 프레임 목적지를 다시 잰다(이동 중 위쪽 내용이 늘어도 목적지에 도착)
 * - 도착 뒤 SCROLL_MS.settle 동안 목적지가 움직이면(늦게 생긴 위쪽 내용) 바로 맞춘다
 * - 기다리거나 움직이는 동안 사용자가 직접 스크롤·키 입력·누르기를 하면 그 자리에서 멈춘다(onCancel)
 * - 고정 머리글·고정 줄 높이는 문서 scroll-padding-top이 피한다. 동작 줄이기·프레임 함수가 없는 환경은 즉시(scrollIntoView)
 */
export const scrollToSection = (id: string, { reduce = false, onCancel }: { reduce?: boolean; onCancel?: () => void } = {}) => {
  const started = Date.now();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let frame = 0;
  let finished = false;
  const animated = !reduce && typeof window.requestAnimationFrame === "function";
  const cleanup = () => {
    finished = true;
    clearTimeout(timer);
    if (frame) window.cancelAnimationFrame(frame);
    for (const name of USER_INPUT) window.removeEventListener(name, cancel);
  };
  function cancel() {
    if (finished) return;
    cleanup();
    onCancel?.();
  }
  const root = document.documentElement;
  const goalOf = (target: Element) => {
    const offset = Number.parseFloat(getComputedStyle(root).scrollPaddingTop) || 0;
    const raw = target.getBoundingClientRect().top + window.scrollY - offset;
    return Math.max(0, Math.min(raw, root.scrollHeight - window.innerHeight));
  };
  const settle = (target: Element, until: number) => {
    const step = () => {
      frame = 0;
      const goal = goalOf(target);
      if (Math.abs(window.scrollY - goal) > 1) window.scrollTo(0, goal);
      if (Date.now() < until) frame = window.requestAnimationFrame(step);
      else cleanup();
    };
    frame = window.requestAnimationFrame(step);
  };
  const move = (target: Element) => {
    if (!animated || typeof target.getBoundingClientRect !== "function") {
      cleanup();
      target.scrollIntoView?.({ block: "start" });
      return;
    }
    const from = window.scrollY;
    const distance = goalOf(target) - from;
    if (Math.abs(distance) < 2) return settle(target, Date.now() + SCROLL_MS.settle);
    const duration = scrollDuration(distance);
    const t0 = Date.now();
    const step = () => {
      frame = 0;
      const p = Math.min(1, (Date.now() - t0) / duration);
      window.scrollTo(0, from + (goalOf(target) - from) * ease(p));
      if (p < 1) frame = window.requestAnimationFrame(step);
      else settle(target, Date.now() + SCROLL_MS.settle);
    };
    frame = window.requestAnimationFrame(step);
  };
  const attempt = () => {
    const target = document.getElementById(id);
    const timedOut = Date.now() - started >= SECTION_WAIT_MS;
    if (target && (timedOut || !loadingAbove(target))) return move(target);
    if (timedOut) return cleanup();
    timer = setTimeout(attempt, SCROLL_MS.poll);
  };
  for (const name of USER_INPUT) window.addEventListener(name, cancel, { passive: true });
  attempt();
  return cleanup;
};

/**
 * 탐지 설정 화면 이동 시 스크롤(라우터는 스크롤을 건드리지 않아 이전 화면 위치가 남는다 — 하위 화면이 중간부터 보이던 원인).
 * - 해시(#영역 id)가 있으면 그 영역으로 부드럽게(목차 클릭·다른 화면에서 목차 클릭 후 이동·해시 주소로 첫 진입). 다른 화면에서 온 이동이면 먼저 맨 위로
 * - 경로가 바뀌었거나 해시만 지운 이동이면 맨 위로. 쿼리만 바뀐 이동(탭·단계)은 그대로 둔다
 * - 브라우저 뒤로/앞으로(POP)는 건드리지 않는다: 브라우저 기본 위치 복원을 따른다
 * - 화면 전환(PageFade) 중이면 이전 화면이 사라진 뒤(새 화면이 들어올 때) 처리한다 — 사라지는 화면이 튀지 않게
 * 반환값 pinned: 목차로 이동 중·이동한 영역(목차 현재 표시 고정 — 지나가는 영역으로 표시가 튀지 않게). 사용자가 직접 스크롤하면 풀린다.
 */
export const useDetectionScroll = () => {
  const location = useLocation();
  const navigationType = useNavigationType();
  const reduce = Boolean(useReducedMotion());
  const previous = useRef<{ pathname: string; search: string; hash: string } | null>(null);
  const [pinned, setPinned] = useState<string | null>(null);
  useLayoutEffect(() => {
    const before = previous.current;
    previous.current = { pathname: location.pathname, search: location.search, hash: location.hash };
    if (!hasDom()) return;
    const target = hashId(location.hash);
    if (navigationType === "POP" && before) return;
    const pathChanged = !before || before.pathname !== location.pathname;
    let stop = () => {};
    const run = () => {
      if (target) {
        // 다른 화면에서 온 해시 이동(하위 화면 → 첫 화면 목차)은 이전 위치를 먼저 지운다. 대상을 기다리는 동안 이전 화면 위치에 머물지 않게 한다.
        if (before && pathChanged) window.scrollTo(0, 0);
        setPinned(target);
        stop = scrollToSection(target, { reduce, onCancel: () => setPinned(null) });
        return;
      }
      setPinned(null);
      if (navigationType === "POP") return;
      const hashCleared = !!before && !!before.hash && before.search === location.search;
      if (pathChanged || hashCleared) window.scrollTo(0, 0);
    };
    const unwait = afterPageSwap(run);
    return () => {
      unwait();
      stop();
    };
    // reduce는 이동 시작 때 값만 쓴다(설정이 바뀌어도 진행 중인 이동을 다시 걸지 않음).
  }, [location.key, location.pathname, location.search, location.hash, navigationType]); // eslint-disable-line react-hooks/exhaustive-deps
  // 고정은 사용자가 직접 스크롤하면 풀린다(이동이 끝난 뒤에도 — 짧은 마지막 영역을 눌렀을 때 그 항목이 계속 현재로 보이게).
  useEffect(() => {
    if (!pinned || !hasDom()) return;
    const release = () => setPinned(null);
    for (const name of USER_INPUT) window.addEventListener(name, release, { passive: true });
    return () => {
      for (const name of USER_INPUT) window.removeEventListener(name, release);
    };
  }, [pinned]);
  return pinned;
};

/**
 * 목차 현재 영역: pinned(목차로 이동 중·이동한 영역)가 화면에 있으면 그 영역.
 * 아니면 스크롤 위치 기준 — 머리글·고정 줄 아래 선(scroll-padding-top)을 지난 마지막 영역, 아무것도 안 지났으면 첫 영역.
 * 페이지 끝까지 내려가 더 움직일 수 없으면 화면에 보이는 해시 영역 또는 마지막 영역.
 * sections가 null이면(하위 화면) 표시하지 않는다. sections는 고정 목록(detectionToc)을 넘겨 매 렌더 다시 구독하지 않게 한다.
 */
export const useActiveSection = (sections: readonly { id: string }[] | null, hashTarget: string | null, pinned: string | null = null) => {
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
      if (pinned && present.some((node) => node.id === pinned)) return setActive(pinned);
      const root = document.documentElement;
      const line = (Number.parseFloat(getComputedStyle(root).scrollPaddingTop) || 0) + 1;
      const atBottom = window.scrollY + window.innerHeight >= root.scrollHeight - 2;
      if (atBottom && window.scrollY > 0) {
        const hashed = hashTarget ? present.find((node) => node.id === hashTarget) : undefined;
        const top = hashed?.getBoundingClientRect().top;
        return setActive(hashed && top !== undefined && top >= 0 && top < window.innerHeight ? hashed.id : present[present.length - 1].id);
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
  }, [sections, hashTarget, pinned]);
  return active;
};
