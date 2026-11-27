import { Component, Fragment, createRef, useId, type ReactNode } from "react";
import { useNavigationType } from "react-router-dom";
import { useReducedMotion } from "motion/react";
import { MOTION_EASE, MOTION_MS } from "@/lib/motion";

/**
 * 화면 전환(ui.md "움직임" 화면 전환): transitionKey가 바뀌면 이전 화면이 사라지고(fast 120ms ease-in) 새 화면이 나타난다(base 180ms ease-out). 투명도만.
 * 사이드바가 있는 화면은 사이드바를 두고 본문에만 건다(AppLayout = 주요 화면 단위, 이상 분석·탐지 설정 본문 = 경로 단위, 설정 = 항목 단위).
 *
 * 사라지는 화면은 React 트리에 남기지 않는다: 키가 바뀌는 커밋에서 DOM을 바꾸기 직전(getSnapshotBeforeUpdate) 이전 화면 DOM의 정적 사본을 떠
 * 같은 자리(화면 기준 위치·크기, position: fixed)에 겹쳐 놓고 사라지게 한 뒤 지운다. 이전 화면 컴포넌트는 그 커밋에서 바로 내려가므로
 * 사라지는 동안 이전 화면에서 나오는 이동·조회·저장소 변경이 없다. 새 화면은 곧바로 마운트하되 사본이 사라질 때까지 투명, 그 뒤 나타난다.
 * - 사본: inert·aria-hidden·누름 막음, id 속성 제거(중복 id·영역 이동 대상이 되지 않게 — SVG defs 안의 id는 화살촉 등 참조가 끊기지 않게 둔다).
 *   사본 안의 CSS 애니메이션·전환은 멈춘다(붙이는 순간 등장 효과가 처음부터 다시 돌지 않게 — 떠난 순간의 모습 그대로 사라짐).
 *   canvas 내용은 복사되지 않는다(이 앱 화면은 SVG·DOM만 씀)
 * - 사본이 사라지는 중에 또 이동하면 보이는 사본을 그대로 이어서 사라지게 하고(새 사본 없음), 새 화면이 나타나는 중이면 지금 모습을 사본으로 뜬다
 * - 주소 교체(replace) 이동(옛 주소 넘김·정규화·작업 ID → 판정 ID 등)·처음 그릴 때·동작 줄이기·DOM이 없는 환경은 효과 없이 바로 바꾼다
 * - afterPageSwap: 같은 커밋에 시작된 전환의 사본이 사라진 뒤 실행(보이는 이동인 목차 부드러운 스크롤용). 맨 위로 올리기는 미루지 않는다 —
 *   사본은 화면 기준 고정이라 문서가 스크롤돼도 튀지 않고, 새 화면의 작업 영역 스크롤이 그 뒤에 이긴다
 * - 안쪽 전환이 진행 중일 때 바깥 전환이 오면 안쪽 사본은 지우지 않고 끝까지 사라지게 둔다
 */
const COPY = "data-page-fade-copy";
// 사본 안의 애니메이션·전환 정지(문서에 한 번 넣는다).
const ensureCopyStyle = () => {
  if (document.getElementById("page-fade-copy-style")) return;
  const style = document.createElement("style");
  style.id = "page-fade-copy-style";
  style.textContent = `[${COPY}], [${COPY}] * { animation: none !important; transition: none !important; }`;
  document.head.appendChild(style);
};
const canAnimate = () =>
  typeof document !== "undefined" &&
  document.documentElement != null &&
  typeof window !== "undefined" &&
  typeof window.requestAnimationFrame === "function" &&
  typeof Element !== "undefined" &&
  typeof Element.prototype.animate === "function";

const swapping = new Set<string>();
let waiting: (() => void)[] = [];
const flush = () => {
  if (swapping.size) return;
  const run = waiting;
  waiting = [];
  run.forEach((callback) => callback());
};
/**
 * 진행 중인 화면 전환의 이전 화면 사본이 사라진 뒤(없으면 바로) 실행. 반환값으로 취소.
 * 같은 커밋의 바깥 전환(부모가 더 늦게 커밋 단계를 마침)도 잡도록 판단은 이 커밋이 끝난 직후(마이크로태스크)에 한다.
 */
export const afterPageSwap = (callback: () => void) => {
  let cancelled = false;
  let queued = false;
  queueMicrotask(() => {
    if (cancelled) return;
    if (!swapping.size) return callback();
    queued = true;
    waiting.push(callback);
  });
  return () => {
    cancelled = true;
    if (queued) waiting = waiting.filter((item) => item !== callback);
  };
};

const easing = (curve: readonly number[]) => `cubic-bezier(${curve.join(", ")})`;

type Props = { transitionKey: string; instant: boolean; swapId: string; children: ReactNode };
type Snapshot = { overlay: HTMLElement; opacity: number } | null;

class SnapshotFade extends Component<Props> {
  private host = createRef<HTMLDivElement>();
  private overlay: HTMLElement | null = null;
  private fadeOut: Animation | null = null;
  private fadeIn: Animation | null = null;

  getSnapshotBeforeUpdate(prev: Props): Snapshot {
    if (prev.transitionKey === this.props.transitionKey || this.props.instant || !canAnimate()) return null;
    const host = this.host.current;
    if (!host) return null;
    // 사본이 아직 사라지는 중이면 보이는 것은 그 사본이다 — 새로 뜨지 않고 이어서 사라지게 한다.
    if (this.overlay) return { overlay: this.overlay, opacity: Number(getComputedStyle(this.overlay).opacity) || 0 };
    const rect = host.getBoundingClientRect();
    const opacity = this.fadeIn ? Number(getComputedStyle(host).opacity) || 0 : 1;
    const overlay = host.cloneNode(true) as HTMLElement;
    overlay.removeAttribute("id");
    overlay.removeAttribute("data-page-fade");
    overlay.querySelectorAll("[data-page-fade]").forEach((node) => node.removeAttribute("data-page-fade"));
    // SVG defs 안의 id(화살촉 marker·그라데이션)는 사본 안 참조(marker-end 등)를 위해 둔다.
    overlay.querySelectorAll("[id]").forEach((node) => {
      if (!node.closest("defs")) node.removeAttribute("id");
    });
    overlay.setAttribute("aria-hidden", "true");
    overlay.setAttribute("inert", "");
    overlay.setAttribute(COPY, "");
    Object.assign(overlay.style, {
      position: "fixed",
      left: `${rect.left}px`,
      top: `${rect.top}px`,
      width: `${rect.width}px`,
      height: `${rect.height}px`,
      margin: "0",
      opacity: String(opacity),
      pointerEvents: "none",
      overflow: "hidden",
    });
    return { overlay, opacity };
  }

  componentDidUpdate(_prev: Props, _state: unknown, snapshot: Snapshot) {
    const host = this.host.current;
    if (!snapshot || !host) return;
    // 커밋 단계(그리기 전): 새 화면을 투명하게 두고 사본을 겹친다 — 새 화면이 한 프레임도 먼저 비치지 않는다.
    this.fadeIn?.cancel();
    this.fadeIn = null;
    host.style.opacity = "0";
    const { overlay, opacity } = snapshot;
    if (overlay !== this.overlay) {
      this.overlay = overlay;
      ensureCopyStyle();
      document.body.appendChild(overlay);
    }
    swapping.add(this.props.swapId);
    this.fadeOut?.cancel();
    const fadeOut = overlay.animate([{ opacity }, { opacity: 0 }], {
      duration: MOTION_MS.fast * opacity,
      easing: easing(MOTION_EASE.in),
      fill: "forwards",
    });
    this.fadeOut = fadeOut;
    fadeOut.onfinish = () => {
      if (this.fadeOut !== fadeOut) return;
      this.removeOverlay();
      this.done();
      const fadeIn = host.animate([{ opacity: 0 }, { opacity: 1 }], { duration: MOTION_MS.base, easing: easing(MOTION_EASE.out) });
      host.style.opacity = "";
      this.fadeIn = fadeIn;
      fadeIn.onfinish = () => {
        if (this.fadeIn === fadeIn) this.fadeIn = null;
      };
    };
  }

  componentWillUnmount() {
    this.fadeIn?.cancel();
    // 사라지는 중인 사본은 끝까지 사라지게 둔다(바깥 전환으로 이 상자가 내려가도 안쪽 사본이 갑자기 꺼지지 않게).
    const overlay = this.overlay;
    const fadeOut = this.fadeOut;
    if (overlay && fadeOut) fadeOut.onfinish = () => overlay.remove();
    else overlay?.remove();
    this.overlay = null;
    this.fadeOut = null;
    this.done();
  }

  private removeOverlay() {
    this.overlay?.remove();
    this.overlay = null;
    this.fadeOut = null;
  }

  private done() {
    if (swapping.delete(this.props.swapId)) flush();
  }

  render() {
    // 키가 바뀌면 이전 화면 컴포넌트는 이 커밋에서 내려가고 새 화면이 마운트된다(사본만 남아 사라진다).
    return (
      <div ref={this.host} data-page-fade="">
        <Fragment key={this.props.transitionKey}>{this.props.children}</Fragment>
      </div>
    );
  }
}

export const PageFade = ({ transitionKey, children }: { transitionKey: string; children: ReactNode }) => {
  const swapId = useId();
  const reduce = Boolean(useReducedMotion());
  const navigationType = useNavigationType();
  return (
    <SnapshotFade transitionKey={transitionKey} instant={reduce || navigationType === "REPLACE"} swapId={swapId}>
      {children}
    </SnapshotFade>
  );
};
