import { useEffect, useState } from "react";

/** #100 이전 TypewriterText와 같은 간격(12ms에 한 글자). 긴 결과는 한 번에 여러 글자씩 보여 전체가 이 시간을 넘지 않게 한다. */
export const TYPEWRITER_TICK_MS = 12;
export const TYPEWRITER_MAX_MS = 2_400;

/**
 * 글자 수 `length`를 앞에서부터 차례로 드러낸다. 반환값은 지금 보이는 글자 수, `active`가 false면 처음부터 전체(애니메이션 없음).
 * 마운트할 때 `active`를 한 번만 읽는다(재렌더·재조회로 다시 시작하지 않음).
 */
export const useTypewriter = (length: number, active: boolean) => {
  const [started] = useState(active);
  const [count, setShown] = useState(0);
  const shown = started ? Math.min(count, length) : length;
  const done = shown >= length;
  useEffect(() => {
    if (!started || done) return;
    const step = Math.max(1, Math.ceil(length / (TYPEWRITER_MAX_MS / TYPEWRITER_TICK_MS)));
    const timer = setInterval(() => setShown((current) => Math.min(length, current + step)), TYPEWRITER_TICK_MS);
    return () => clearInterval(timer);
  }, [started, done, length]);
  return { shown, done };
};
