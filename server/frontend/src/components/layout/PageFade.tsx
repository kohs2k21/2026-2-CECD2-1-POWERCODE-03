import { useEffect, useId, useLayoutEffect, useRef, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { motionTransition } from "@/lib/motion";

/**
 * 화면 전환(ui.md "움직임" 화면 전환): transitionKey(경로)가 바뀌면 이전 화면이 사라지고(fast 120ms ease-in) 새 화면이 나타난다(base 180ms ease-out).
 * 투명도만(위치 이동 없음). 같은 화면 안 쿼리·해시 변경은 부르는 쪽이 키에 넣지 않는다.
 * 사이드바가 있는 화면은 사이드바를 두고 본문에만 건다(AppLayout = 주요 화면 단위, 이상 분석·탐지 설정·설정 = 본문 경로 단위).
 * 처음 그릴 때는 효과 없음. 동작 줄이기면 길이 0. DOM이 없는 환경(시험 렌더러·서버 렌더)은 효과 없이 바로 바꾼다(퇴장이 끝나지 않으므로).
 * 이전 화면이 사라지는 동안의 스크롤 처리(맨 위로·영역 이동)는 afterPageSwap으로 새 화면이 들어올 때로 미룬다(사라지는 화면이 튀지 않게).
 */
const canAnimate = () => typeof document !== "undefined" && document.documentElement != null && typeof window !== "undefined" && typeof window.requestAnimationFrame === "function";

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

export const PageFade = ({ transitionKey, children }: { transitionKey: string; children: ReactNode }) => {
  const id = useId();
  const reduce = Boolean(useReducedMotion());
  const animate = canAnimate();
  const previous = useRef(transitionKey);
  // 자식(새 화면)보다 먼저 표시해 둔다: 같은 커밋의 부모 쪽 스크롤 처리(layout effect)가 afterPageSwap으로 기다리게.
  useLayoutEffect(() => {
    if (previous.current === transitionKey) return;
    previous.current = transitionKey;
    if (!animate || reduce) return;
    swapping.add(id);
  }, [animate, id, reduce, transitionKey]);
  useEffect(
    () => () => {
      swapping.delete(id);
      flush();
    },
    [id],
  );
  if (!animate) return <>{children}</>;
  return (
    <AnimatePresence
      mode="wait"
      initial={false}
      onExitComplete={() => {
        swapping.delete(id);
        flush();
      }}
    >
      <motion.div
        key={transitionKey}
        data-page-fade=""
        initial={{ opacity: 0 }}
        animate={{ opacity: 1, transition: reduce ? { duration: 0 } : motionTransition("base", "out") }}
        exit={{ opacity: 0, transition: reduce ? { duration: 0 } : motionTransition("fast", "in") }}
      >
        {children}
      </motion.div>
    </AnimatePresence>
  );
};
