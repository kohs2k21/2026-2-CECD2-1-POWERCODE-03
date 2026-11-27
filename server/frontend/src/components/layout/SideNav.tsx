import { Fragment, useCallback, useId, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { Link, type To } from "react-router-dom";
import { AnimatePresence, motion, useIsPresent, useReducedMotion, type Variants } from "motion/react";
import { IconMenu2 } from "@tabler/icons-react";
import { Button } from "@/components/ui/button";
import { MOTION_SPRING, motionClasses, motionTransition, springTransition } from "@/lib/motion";
import { cn } from "@/lib/utils";

export type SideNavItem = {
  id: string;
  label: ReactNode;
  to: string;
  icon?: ReactNode;
  count?: number;
  /** 약하게 보이는 항목(예: 이상 분석 위험도 미정 — 와이어프레임 muted·13px). */
  muted?: boolean;
  /** 항목 아래 들여쓴 하위 목록(예: 탐지 설정 현재 메뉴의 화면 영역 목차). 펼칠 항목에만 넘긴다. */
  sub?: SideNavSubItem[];
  /** 하위 목록을 늘 펼친 채로 둔다(펼침·접힘 움직임 없음). 예: 이상 분석 분류함 `완료` 아래 확인 결과별(관리자). */
  subFixed?: boolean;
};
/**
 * 하위 목록 항목: 같은 화면 안 이동이면 replace(뒤로 가기에 쌓지 않음), current는 지금 보이는 영역(aria-current="location").
 * currentType="page"는 하위 항목이 별도 목록(예: 이상 분석 완료 아래 확인 결과별)이라 현재 항목을 aria-current="page"로 표시. count는 항목 오른쪽 건수.
 */
export type SideNavSubItem = { id: string; label: ReactNode; to: To; replace?: boolean; current?: boolean; currentType?: "page" | "location"; count?: number };
/** separated: 위 그룹과 구분선으로 나눈 별도 구역(예: 이상 분석 분류함). */
export type SideNavGroup = { title?: string; items: SideNavItem[]; separated?: boolean };

/**
 * 현재 표시 방식(ui.md "움직임" 메뉴 현재 표시). 한 곳에서 단계별로 고른다.
 * - grow(제자리 피어남): 이동 없이 도착지 표시가 92% → 100%로 피어나고 출발지 표시는 투명해지며 사라진다. 글자색은 표시와 함께 켜진다.
 * - stretch(늘었다 줄기): 표시 하나가 이동 방향 앞 가장자리부터 늘어나 따라가고 뒤 가장자리가 따라와 줄어든다. 글자색은 표시가 도착할 때 켜진다.
 */
export type SideNavIndicator = "grow" | "stretch";
export const SIDE_NAV_INDICATOR: { item: SideNavIndicator; sub: SideNavIndicator } = { item: "grow", sub: "stretch" };

/**
 * 사이드바 한 벌(탐지 설정·이상 분석 분류·설정 항목 공용).
 * - 항목은 URL Link, 현재 항목은 aria-current="page"(ui.md 공통 내부 탐색)
 * - 실폭 264px, 메뉴 최소 높이 48px. 제목(heading)·그룹 제목 13/600/18 흐린 글자(`text-group`), 메뉴 14/21(`text-menu`) 500, 현재 항목 600
 * - 제목 수준: heading이 있으면 heading h2·그룹 h3, 없으면 그룹 h2(이상 분석)
 * - 좁은 화면: 메뉴 버튼으로 펼침, Escape·항목 선택 시 접고 버튼으로 포커스 복귀
 * - header: 그룹 위에 두는 보조 조작(예: 이상 분석 분류 보기 전환)
 * - 넓은 화면: 페이지 스크롤에도 머리글 아래에 고정. 화면보다 길면 사이드바 안에서 스크롤. 고정은 aside에 건다(nav에 걸면 aside 높이가 같아 움직일 공간이 없다)
 * - sub(선택): 항목 아래 들여쓴 하위 목록(목차). 13px 흐린 글자, 현재 영역은 왼쪽 선·진한 글자. subFixed면 늘 펼친 채(움직임 없음)
 * - 움직임(ui.md "움직임" 토큰): 하위 목록은 높이 0↔auto로 펼치고 접는다(항목은 짧은 간격으로 위에서 아래로 나타남).
 *   메뉴를 바꾸면 이전 목록 접힘과 새 목록 펼침이 같은 길이·곡선으로 동시에 진행해 두 높이 합이 이전 높이에서 새 높이로 곧게 바뀐다.
 *   처음 그릴 때는 움직임 없이 펼친 상태(AnimatePresence initial=false).
 *   현재 표시: 상위 항목 = 제자리 피어남, 하위 목록 = 늘었다 줄기(SIDE_NAV_INDICATOR). 두 방식 공통으로 글자·아이콘 색은 표시가 켜질 때(도착) 함께 켜지고
 *   (base), 떠나는 칸은 fast로 꺼진다. 현재(이동 대상) 칸은 마우스 올림 배경이 켜지지 않는다(표시가 배경 역할).
 *   동작 줄이기면 즉시(useReducedMotion, 앱 MotionConfig reducedMotion="user"), 누름(97%)은 styles.css 미디어쿼리가 전환 길이를 없앤다.
 */
// 목록 높이가 바뀌면 아래 메뉴가 밀리는 배치 전환이라 ui.md `layout`(320ms) 토큰.
const SUB_HEIGHT = motionTransition("layout", "standard");
const subListVariants = (reduce: boolean): Variants => ({
  open: {
    height: "auto",
    opacity: 1,
    transitionEnd: { overflow: "visible" },
    transition: reduce
      ? { duration: 0 }
      : { height: SUB_HEIGHT, opacity: motionTransition("base"), delayChildren: 0.04, staggerChildren: 0.02 },
  },
  // 접힘도 높이는 펼침과 같은 길이·곡선(동시에 일어나는 펼침과 합이 곧게 바뀐다). 투명도만 더 짧게.
  collapsed: {
    height: 0,
    opacity: 0,
    overflow: "hidden",
    transition: reduce ? { duration: 0 } : { height: SUB_HEIGHT, opacity: motionTransition("fast", "in") },
  },
});
const subItemVariants = (reduce: boolean): Variants => ({
  open: { opacity: 1, y: 0, transition: reduce ? { duration: 0 } : motionTransition("base") },
  collapsed: { opacity: 0, y: -4, transition: reduce ? { duration: 0 } : motionTransition("fast", "in") },
});

/**
 * 하위 목록 펼침·접힘(AnimatePresence 직접 자식). 접히는 중(퇴장)인 이전 목록은 inert: 남은 링크의 aria-current·클릭·초점이 없게 한다.
 * 그룹 간격(gap-1.5)은 바깥 -mt-1.5 + 안쪽 pt-1.5로 높이 안에 넣어 다 접힌 뒤 사라질 때 간격만큼 튀지 않게 한다(그룹은 flex — grid 행은 음수 여백으로 줄지 않는다).
 * 움직이는 동안만 overflow hidden(끝나면 초점 테두리가 잘리지 않게 visible).
 */
const SubListMotion = ({ variants, children }: { variants: Variants; children: ReactNode }) => {
  const present = useIsPresent();
  return (
    <motion.div className="-mt-1.5" variants={variants} initial="collapsed" animate="open" exit="collapsed">
      <div className="pt-1.5" inert={!present}>
        {children}
      </div>
    </motion.div>
  );
};

/** 제자리 피어남 표시(칸마다, 이동 없음). block = 상위 항목 배경, line = 하위 항목 왼쪽 선. */
const GrowMark = ({ kind, reduce }: { kind: "block" | "line"; reduce: boolean }) => (
  <motion.span
    aria-hidden="true"
    data-side-nav-mark={kind}
    className={cn("pointer-events-none absolute -z-10", kind === "block" ? "inset-0 rounded-md bg-accent" : "inset-y-0 -left-0.5 w-0.5 bg-foreground")}
    initial={reduce ? false : kind === "block" ? { opacity: 0, scale: 0.92 } : { opacity: 0, scaleY: 0.3 }}
    animate={kind === "block" ? { opacity: 1, scale: 1 } : { opacity: 1, scaleY: 1 }}
    exit={{ opacity: 0, transition: reduce ? { duration: 0 } : motionTransition("fast", "in") }}
    transition={reduce ? { duration: 0 } : { default: springTransition("grow"), opacity: motionTransition("base") }}
  />
);

type Spring = { stiffness: number; damping: number };
const stepSpring = (x: number, v: number, goal: number, s: Spring, dt: number) => {
  const nv = v + (-s.stiffness * (x - goal) - s.damping * v) * dt;
  return [x + nv * dt, nv] as const;
};
// 하위 목록 높이 전환(layout 320ms) 동안 칸이 움직이므로 그동안은 멈추지 않고 대상 위치를 다시 잰다.
const FOLLOW_MS = 450;

/**
 * 늘었다 줄기 표시(목록에 하나): 매 프레임 대상 칸 위치를 다시 재서 위·아래 가장자리를 각각 스프링으로 따라간다.
 * 이동 방향 앞 가장자리 = stretchLead, 뒤 = stretchTrail. 표시 가운데가 대상 가운데의 칸 높이 1/4 안에 들어오면 도착(onArrive).
 * DOM을 잴 수 없는 환경(시험 렌더러)·대상이 없거나 동작 줄이기면 바로 제자리·바로 도착.
 */
const useStretchMark = ({ container, mark, targetKey, attr, reduce, onArrive }: {
  container: RefObject<HTMLElement | null>;
  mark: RefObject<HTMLElement | null>;
  targetKey: string | null;
  attr: string;
  reduce: boolean;
  onArrive: (key: string | null) => void;
}) => {
  const track = useRef({ top: 0, bottom: 0, vt: 0, vb: 0, init: false });
  useLayoutEffect(() => {
    const c = container.current;
    const el = mark.current;
    if (!c || !el || typeof c.getBoundingClientRect !== "function" || typeof requestAnimationFrame !== "function") {
      onArrive(targetKey);
      return;
    }
    const s = track.current;
    let raf = 0;
    let last = 0;
    let stable = 0;
    let arrived = false;
    const followUntil = performance.now() + FOLLOW_MS;
    const arrive = () => {
      if (arrived) return;
      arrived = true;
      onArrive(targetKey);
    };
    const step = (now: number) => {
      raf = 0;
      const target = targetKey ? c.querySelector<HTMLElement>(`[${attr}="${CSS.escape(targetKey)}"]`) : null;
      if (!target) {
        el.style.opacity = "0";
        s.init = false;
        return arrive();
      }
      const cr = c.getBoundingClientRect();
      const tr = target.getBoundingClientRect();
      const goalTop = tr.top - cr.top - c.clientTop + c.scrollTop;
      const goalBottom = goalTop + tr.height;
      const dt = last ? Math.min(0.05, (now - last) / 1000) : 1 / 60;
      last = now;
      if (!s.init || reduce) {
        Object.assign(s, { top: goalTop, bottom: goalBottom, vt: 0, vb: 0, init: true });
      } else {
        const down = goalTop + goalBottom > s.top + s.bottom;
        const topSpring = down ? MOTION_SPRING.stretchTrail : MOTION_SPRING.stretchLead;
        const bottomSpring = down ? MOTION_SPRING.stretchLead : MOTION_SPRING.stretchTrail;
        const n = Math.max(1, Math.round(dt * 240));
        for (let i = 0; i < n; i++) {
          [s.top, s.vt] = stepSpring(s.top, s.vt, goalTop, topSpring, dt / n);
          [s.bottom, s.vb] = stepSpring(s.bottom, s.vb, goalBottom, bottomSpring, dt / n);
        }
        if (s.bottom < s.top + 2) s.bottom = s.top + 2;
      }
      el.style.transform = `translate(${tr.left - cr.left - c.clientLeft}px, ${s.top}px)`;
      el.style.height = `${s.bottom - s.top}px`;
      el.style.width = `${tr.width}px`;
      el.style.opacity = "1";
      if (Math.abs((s.top + s.bottom - goalTop - goalBottom) / 2) <= Math.max(3, tr.height / 4)) arrive();
      const done = Math.abs(goalTop - s.top) < 0.3 && Math.abs(goalBottom - s.bottom) < 0.3 && Math.abs(s.vt) < 2 && Math.abs(s.vb) < 2;
      stable = done ? stable + 1 : 0;
      if (stable < 8 || now < followUntil) raf = requestAnimationFrame(step);
      else last = 0;
    };
    const kick = () => {
      if (raf) return;
      last = 0;
      raf = requestAnimationFrame(step);
    };
    // 처음 자리 잡기는 그리기 전에(첫 화면에서 현재 칸 색이 한 프레임 늦게 켜지지 않게), 이동은 다음 프레임부터.
    if (s.init) kick();
    else step(performance.now());
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(kick);
    observer?.observe(c);
    return () => {
      cancelAnimationFrame(raf);
      observer?.disconnect();
    };
  }, [container, mark, targetKey, attr, reduce, onArrive]);
};

/** 늘었다 줄기 표시 요소: block = 상위 항목 배경(칸 폭), line = 하위 항목 왼쪽 2px 선. 위치·크기는 useStretchMark가 style로 쓴다. */
const StretchMark = ({ refEl, kind }: { refEl: RefObject<HTMLSpanElement | null>; kind: "block" | "line" }) => (
  <span
    ref={refEl}
    aria-hidden="true"
    data-side-nav-mark={kind}
    style={{ opacity: 0 }}
    className={cn(
      "pointer-events-none absolute top-0 left-0 transition-opacity duration-(--motion-duration-base) ease-motion-out",
      kind === "block" ? "rounded-md bg-accent" : "w-0.5! bg-foreground",
    )}
  />
);

/** 단계 하나(상위 항목 또는 한 하위 목록)의 현재 표시 상태: lit = 글자색이 켜진 칸. grow는 바로, stretch는 표시가 도착할 때. */
const useIndicator = (mode: SideNavIndicator, targetKey: string | null, attr: string, reduce: boolean) => {
  const container = useRef<HTMLElement>(null);
  const mark = useRef<HTMLSpanElement>(null);
  const [arrived, setArrived] = useState<string | null>(null);
  const onArrive = useCallback((key: string | null) => setArrived(key), []);
  useStretchMark({ container, mark, targetKey: mode === "stretch" ? targetKey : null, attr, reduce, onArrive });
  // stretch: 대상이 바뀌면 출발지 색은 그 순간 꺼진다(arrived가 이전 값이면 켜진 칸 없음).
  const lit = mode === "grow" ? targetKey : arrived === targetKey ? arrived : null;
  return { container, mark, lit };
};

const SubList = ({ item, reduce, collapse }: { item: SideNavItem; reduce: boolean; collapse: () => void }) => {
  const mode = SIDE_NAV_INDICATOR.sub;
  const currentId = item.sub?.find((sub) => sub.current)?.id ?? null;
  const { container, mark, lit } = useIndicator(mode, currentId, "data-side-nav-sub", reduce);
  const itemVariants = subItemVariants(reduce);
  const Item = item.subFixed ? "li" : motion.li;
  return (
    <ul ref={container as RefObject<HTMLUListElement>} data-slot="side-nav-sub" className="relative ml-6 grid gap-0.5 border-l">
      {mode === "stretch" && <StretchMark refEl={mark} kind="line" />}
      {item.sub!.map((sub) => {
        const target = sub.id === currentId;
        const on = sub.id === lit;
        return (
          <Item key={sub.id} {...(item.subFixed ? {} : { variants: itemVariants })}>
            <Link
              to={sub.to}
              replace={sub.replace}
              data-side-nav-sub={sub.id}
              aria-current={sub.current ? (sub.currentType ?? "location") : undefined}
              onClick={collapse}
              className={cn(
                "relative isolate -ml-px flex min-h-8 items-center rounded-r-md border-l-2 border-transparent px-3 text-body text-muted-foreground outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                on ? motionClasses.litOn : motionClasses.litOff,
                !target && "hover:bg-accent hover:text-accent-foreground",
                on && "font-semibold text-foreground",
              )}
            >
              {mode === "grow" && <AnimatePresence initial={false}>{target && <GrowMark key="mark" kind="line" reduce={reduce} />}</AnimatePresence>}
              <span className="min-w-0 flex-1 truncate">{sub.label}</span>
              {typeof sub.count === "number" && (
                <span className={cn("text-xs font-semibold tabular-nums text-muted-foreground", on && "text-foreground")}>{sub.count}</span>
              )}
            </Link>
          </Item>
        );
      })}
    </ul>
  );
};

export const SideNav = ({
  label,
  heading,
  groups,
  activeId,
  menuId,
  className,
  header,
}: {
  label: string;
  heading?: string;
  header?: ReactNode;
  groups: SideNavGroup[];
  activeId: string | null;
  menuId?: string;
  className?: string;
}) => {
  const generatedId = useId();
  const navId = menuId ?? generatedId;
  const [expanded, setExpanded] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const GroupTitle = heading ? "h3" : "h2";
  const reduceMotion = Boolean(useReducedMotion());
  const listVariants = subListVariants(reduceMotion);
  const mode = SIDE_NAV_INDICATOR.item;
  const { container, mark, lit } = useIndicator(mode, activeId, "data-side-nav-item", reduceMotion);
  const collapse = () => {
    if (!expanded) return;
    setExpanded(false);
    toggleRef.current?.focus();
  };
  return (
    <aside
      data-slot="side-nav"
      className={cn("w-full shrink-0 md:sticky md:top-[calc(var(--top-nav-offset)+var(--page-block-padding))] md:w-(--section-sidebar-width) md:self-start", className)}
      onKeyDown={(event) => {
        if (event.key === "Escape" && expanded) {
          event.preventDefault();
          collapse();
        }
      }}
    >
      <Button
        ref={toggleRef}
        variant="outline"
        className="mb-2 w-full justify-start md:hidden"
        aria-expanded={expanded}
        aria-controls={navId}
        onClick={() => setExpanded((value) => !value)}
      >
        <IconMenu2 aria-hidden="true" />
        {heading ?? label}
      </Button>
      <nav
        ref={container as RefObject<HTMLElement>}
        id={navId}
        aria-label={label}
        className={cn("relative grid gap-4 rounded-lg border bg-card p-3 md:max-h-[calc(100dvh-var(--top-nav-offset)-2*var(--page-block-padding))] md:overflow-y-auto", !expanded && "max-md:hidden")}
      >
        {mode === "stretch" && <StretchMark refEl={mark} kind="block" />}
        {heading && <h2 className="px-3 text-group text-muted-foreground">{heading}</h2>}
        {header}
        {groups.map((group, index) => (
          <section key={group.title ?? index} className={cn("flex flex-col gap-1.5", group.separated && "border-t pt-3")}>
            {group.title && <GroupTitle className="px-3 text-group text-muted-foreground">{group.title}</GroupTitle>}
            {group.items.map((item) => {
              const current = item.id === activeId;
              const on = item.id === lit;
              const hasSub = Boolean(item.sub && item.sub.length > 0);
              return (
                <Fragment key={item.id}>
                  <Link
                    to={item.to}
                    data-side-nav-item={item.id}
                    aria-current={current ? "page" : undefined}
                    onClick={collapse}
                    className={cn(
                      "relative isolate flex min-h-(--section-menu-height) items-center gap-3 rounded-md px-3 text-menu text-muted-foreground outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                      on ? motionClasses.litOn : motionClasses.litOff,
                      !current && "hover:bg-accent hover:text-accent-foreground",
                      on && "font-semibold text-foreground",
                    )}
                  >
                    {mode === "grow" && <AnimatePresence initial={false}>{current && <GrowMark key="mark" kind="block" reduce={reduceMotion} />}</AnimatePresence>}
                    {item.icon && <span className="flex size-5 items-center justify-center [&_svg]:size-4">{item.icon}</span>}
                    <span className={cn("min-w-0 flex-1 truncate", item.muted && "text-body", item.muted && !on && "text-muted-foreground")}>{item.label}</span>
                    {typeof item.count === "number" && (
                      <span className={cn("text-xs font-semibold tabular-nums text-muted-foreground", on && "text-foreground")}>{item.count}</span>
                    )}
                  </Link>
                  {item.subFixed ? (
                    hasSub && <SubList item={item} reduce={reduceMotion} collapse={collapse} />
                  ) : (
                    <AnimatePresence initial={false}>
                      {hasSub && (
                        <SubListMotion key="sub" variants={listVariants}>
                          <SubList item={item} reduce={reduceMotion} collapse={collapse} />
                        </SubListMotion>
                      )}
                    </AnimatePresence>
                  )}
                </Fragment>
              );
            })}
          </section>
        ))}
      </nav>
    </aside>
  );
};
