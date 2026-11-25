import { useCallback, useEffect, useRef, useState } from "react";
import { useReducedMotion } from "motion/react";
import { MOTION_MS, STREAM_MS } from "@/lib/motion";

/**
 * 흘러나오기(가짜 생성 효과, ui.md "움직임"). 시작 여부(start)는 부르는 쪽이 정한다(지금은 [근거 요약 생성]을 누른 때).
 * - 조각(어절·표 행·블록)마다 시작 시각을 정해 두고, 그 시각이 지난 조각만 그린다. 아직 안 나온 조각은 DOM에 없어 자리를 차지하지 않는다
 * - 시계는 경과 시간 기준(rAF, 없으면 16ms 타이머): 숨은 탭에서 rAF가 멈췄다 돌아오면 지난 조각을 한꺼번에 그리고, 상한이 지났으면 바로 끝낸다
 * - 끝나면(streaming=false) 효과 없는 평소 표시와 같은 DOM이다. 언마운트·판정 전환(부르는 쪽 key)에서는 예약을 취소한다
 * - 동작 줄이기면 시작하지 않는다(즉시 전부 표시)
 */
export type StreamSchedule = { times: number[]; total: number };
type Clock = { now: () => number; next: (run: () => void) => () => void };
const clock: Clock = {
  now: () => globalThis.performance?.now() ?? Date.now(),
  next: (run) => {
    if (typeof globalThis.requestAnimationFrame === "function") {
      const id = globalThis.requestAnimationFrame(run);
      return () => globalThis.cancelAnimationFrame(id);
    }
    const id = setTimeout(run, 16);
    return () => clearTimeout(id);
  },
};

/**
 * 조각 시작 시각 만들기: word(n)·row(n)·block()을 그리는 순서대로 부른다. 반환 값은 그 조각들의 번호.
 * 자연 길이(마지막 시작 + 페이드)가 STREAM_MS.total을 넘으면 모든 시각을 같은 비율로 줄인다.
 */
export const streamPlanner = () => {
  const times: number[] = [];
  let t = 0;
  const take = (count: number, gap: number) =>
    Array.from({ length: count }, () => {
      times.push(t);
      t += gap;
      return times.length - 1;
    });
  return {
    words: (count: number) => take(count, STREAM_MS.word),
    rows: (count: number) => take(count, STREAM_MS.row),
    pause: () => {
      t += STREAM_MS.block;
    },
    finish: (): StreamSchedule => {
      const last = times.length > 0 ? times[times.length - 1] : 0;
      const room = STREAM_MS.total - MOTION_MS.slow;
      const scale = last > room ? room / last : 1;
      const scaled = times.map((time) => Math.round(time * scale));
      return { times: scaled, total: (scaled.length > 0 ? scaled[scaled.length - 1] : 0) + MOTION_MS.slow };
    },
  };
};

export const useTextStream = (start: boolean, schedule: StreamSchedule) => {
  const reduce = Boolean(useReducedMotion());
  const active = start && !reduce && schedule.times.length > 0;
  const plan = useRef(schedule);
  plan.current = schedule;
  const shownAt = (elapsed: number) => plan.current.times.filter((time) => time <= elapsed).length;
  const [shown, setShown] = useState(() => shownAt(0));
  const [done, setDone] = useState(false);
  useEffect(() => {
    if (!active) return;
    const startedAt = clock.now();
    let cancel = () => {};
    const step = () => {
      const elapsed = clock.now() - startedAt;
      if (elapsed >= plan.current.total) {
        setDone(true);
        return;
      }
      setShown(shownAt(elapsed));
      cancel = clock.next(step);
    };
    cancel = clock.next(step);
    return () => cancel();
    // 시작은 마운트(또는 start가 켜진 때) 한 번. 다시 그려져도 시계를 새로 시작하지 않는다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);
  const streaming = active && !done;
  const visible = useCallback((index: number) => !streaming || index < shown, [streaming, shown]);
  return { streaming, visible };
};

/**
 * 이 브라우저 세션에서 [근거 요약 생성]을 누른 판정. sessionStorage(탭을 닫으면 사라짐 — 새로고침·다른 화면 다녀오기에도 다시 누르지 않게)에
 * 최근 OPENED_MAX개만 둔다. 저장소를 못 쓰면(사생활 보호 창·차단) 메모리만 쓴다.
 */
const OPENED_KEY = "esb.evidenceSummary.opened";
const OPENED_MAX = 200;
const openedMemory = new Set<string>();
const readOpened = (): string[] => {
  try {
    const raw = globalThis.sessionStorage?.getItem(OPENED_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((value): value is string => typeof value === "string") : [];
  } catch {
    return [];
  }
};
export const wasSummaryOpened = (id: string) => openedMemory.has(id) || readOpened().includes(id);
const rememberOpened = (id: string) => {
  openedMemory.add(id);
  try {
    const ids = [...readOpened().filter((value) => value !== id), id].slice(-OPENED_MAX);
    globalThis.sessionStorage?.setItem(OPENED_KEY, JSON.stringify(ids));
  } catch {
    // 저장 실패는 메모리 기록으로 대신한다.
  }
};

/**
 * 근거 요약 카드를 펼칠지·효과를 재생할지. 이미 본 판정(이 세션에서 눌렀음, 서버 다듬기 상태가 pending·done)은 버튼 없이 바로 펼치고 효과 없음.
 * 부르는 쪽은 판정 id로 key를 준다(판정이 바뀌면 상태를 새로 잡는다).
 */
export const useEvidenceSummaryOpen = (id: string, polishState: string | null | undefined) => {
  const [initiallyOpen] = useState(() => wasSummaryOpened(id) || polishState === "pending" || polishState === "done");
  const [pressed, setPressed] = useState(false);
  const open = useCallback(() => {
    rememberOpened(id);
    setPressed(true);
  }, [id]);
  return { opened: initiallyOpen || pressed, pressed, open };
};
