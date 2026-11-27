import { useEffect, useId, useLayoutEffect, useRef, type ReactNode } from "react";
import { UNSAFE_LocationContext, useLocation, useNavigationType } from "react-router-dom";
import { AnimatePresence, motion, useIsPresent, useReducedMotion } from "motion/react";
import { MOTION_MS, motionTransition } from "@/lib/motion";

/**
 * 화면 전환(ui.md "움직임" 화면 전환): transitionKey가 바뀌면 이전 화면이 사라지고(fast 120ms ease-in) 새 화면이 나타난다(base 180ms ease-out). 투명도만.
 * 사이드바가 있는 화면은 사이드바를 두고 본문에만 건다(AppLayout = 주요 화면 단위, 이상 분석·탐지 설정 본문 = 경로 단위, 설정 = 항목 단위).
 * - 사라지는 화면은 사라지기 시작한 때의 주소를 그대로 본다(FrozenLocation): 새 주소로 주소 정규화·조회를 하지 않는다. 그동안 inert(누를 수 없음)
 * - 주소 교체(replace) 이동(정규화·옛 주소 넘김·작업 ID → 판정 ID 등)은 효과 없이 바로 바꾼다
 * - 처음 그릴 때·동작 줄이기·DOM이 없는 환경(시험 렌더러·서버 렌더 — 퇴장이 끝나지 않음)은 효과 없이 바로 바꾼다
 * - 이전 화면이 사라지는 동안의 스크롤 처리(맨 위로·영역 이동)는 afterPageSwap으로 새 화면이 들어올 때로 미룬다(사라지는 화면이 튀지 않게)
 */
const canAnimate = () =>
  typeof document !== "undefined" && document.documentElement != null && typeof window !== "undefined" && typeof window.requestAnimationFrame === "function";

const swapping = new Set<string>();
let waiting: (() => void)[] = [];
const flush = () => {
  if (swapping.size) return;
  const run = waiting;
  waiting = [];
  run.forEach((callback) => callback());
};
/** 진행 중인 화면 전환이 끝나 새 화면이 들어올 때(없으면 바로) 실행. 반환값으로 취소. */
export const afterPageSwap = (callback: () => void) => {
  if (!swapping.size) {
    callback();
    return () => undefined;
  }
  waiting.push(callback);
  return () => {
    waiting = waiting.filter((item) => item !== callback);
  };
};
// 전환 완료 신호(퇴장 끝)가 오지 않는 경우(사라지는 중 원래 화면으로 돌아옴 — 퇴장이 취소됨)에도 대기 처리가 멈추지 않게 하는 상한.
const SWAP_LIMIT_MS = MOTION_MS.fast + MOTION_MS.base;

/** 이 화면이 사라지기 시작하면 그때의 주소를 고정해 아래에 준다(사라지는 동안 새 주소를 읽지 않게). 사라지는 동안은 inert. */
const FrozenLocation = ({ children }: { children: ReactNode }) => {
  const location = useLocation();
  const navigationType = useNavigationType();
  const present = useIsPresent();
  const frozen = useRef({ location, navigationType });
  if (present && frozen.current.location !== location) frozen.current = { location, navigationType };
  return (
    <UNSAFE_LocationContext.Provider value={frozen.current}>
      <div inert={!present} className="contents">
        {children}
      </div>
    </UNSAFE_LocationContext.Provider>
  );
};

type Mode = { instant: boolean };
const pageVariants = {
  hidden: ({ instant }: Mode) => ({ opacity: instant ? 1 : 0 }),
  shown: ({ instant }: Mode) => ({ opacity: 1, transition: instant ? { duration: 0 } : motionTransition("base", "out") }),
  leaving: ({ instant }: Mode) => ({ opacity: instant ? 1 : 0, transition: instant ? { duration: 0 } : motionTransition("fast", "in") }),
};

export const PageFade = ({ transitionKey, children }: { transitionKey: string; children: ReactNode }) => {
  const id = useId();
  const reduce = Boolean(useReducedMotion());
  const navigationType = useNavigationType();
  const animate = canAnimate();
  const previous = useRef(transitionKey);
  const instant = reduce || navigationType === "REPLACE";
  const done = () => {
    if (!swapping.delete(id)) return;
    flush();
  };
  // 자식(새 화면)보다 먼저 표시해 둔다: 같은 커밋의 부모 쪽 스크롤 처리(layout effect)가 afterPageSwap으로 기다리게.
  useLayoutEffect(() => {
    if (previous.current === transitionKey) return;
    previous.current = transitionKey;
    if (!animate || instant) return;
    swapping.add(id);
    // 지우지 않는 상한 타이머(done은 여러 번 불려도 한 번만 처리).
    window.setTimeout(done, SWAP_LIMIT_MS);
  }, [animate, id, instant, transitionKey]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => done, []); // eslint-disable-line react-hooks/exhaustive-deps
  if (!animate) return <>{children}</>;
  const mode: Mode = { instant };
  return (
    <AnimatePresence mode="wait" initial={false} custom={mode} onExitComplete={done}>
      <motion.div key={transitionKey} data-page-fade="" custom={mode} variants={pageVariants} initial="hidden" animate="shown" exit="leaving">
        <FrozenLocation>{children}</FrozenLocation>
      </motion.div>
    </AnimatePresence>
  );
};
