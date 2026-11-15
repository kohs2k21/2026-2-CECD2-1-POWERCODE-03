import { Fragment, useId, useRef, useState, type ReactNode } from "react";
import { Link, type To } from "react-router-dom";
import { AnimatePresence, motion, useIsPresent, useReducedMotion, type Variants } from "motion/react";
import { IconMenu2 } from "@tabler/icons-react";
import { Button } from "@/components/ui/button";
import { motionClasses, motionTransition } from "@/lib/motion";
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
};
/**
 * 하위 목록 항목: 같은 화면 안 이동이면 replace(뒤로 가기에 쌓지 않음), current는 지금 보이는 영역(aria-current="location").
 * currentType="page"는 하위 항목이 별도 목록(예: 이상 분석 완료 아래 확인 결과별)이라 현재 항목을 aria-current="page"로 표시. count는 항목 오른쪽 건수.
 */
export type SideNavSubItem = { id: string; label: ReactNode; to: To; replace?: boolean; current?: boolean; currentType?: "page" | "location"; count?: number };
/** separated: 위 그룹과 구분선으로 나눈 별도 구역(예: 이상 분석 분류함). */
export type SideNavGroup = { title?: string; items: SideNavItem[]; separated?: boolean };

/**
 * 사이드바 한 벌(탐지 설정·이상 분석 분류·설정 항목 공용).
 * - 항목은 URL Link, 현재 항목은 aria-current="page"(ui.md 공통 내부 탐색)
 * - 실폭 264px, 메뉴 최소 높이 48px. 제목(heading)·그룹 제목 13/600/18 흐린 글자(`text-group`), 메뉴 14/21(`text-menu`) 500, 현재 항목 600
 * - 제목 수준: heading이 있으면 heading h2·그룹 h3, 없으면 그룹 h2(이상 분석)
 * - 좁은 화면: 메뉴 버튼으로 펼침, Escape·항목 선택 시 접고 버튼으로 포커스 복귀
 * - header: 그룹 위에 두는 보조 조작(예: 이상 분석 분류 보기 전환)
 * - 넓은 화면: 페이지 스크롤에도 머리글 아래에 고정. 화면보다 길면 사이드바 안에서 스크롤. 고정은 aside에 건다(nav에 걸면 aside 높이가 같아 움직일 공간이 없다)
 * - sub(선택): 항목 아래 들여쓴 하위 목록(목차). 13px 흐린 글자, 현재 영역은 왼쪽 선·진한 글자
 * - 움직임(ui.md "움직임" 토큰): 하위 목록은 높이 0↔auto로 펼치고 접는다(항목은 짧은 간격으로 위에서 아래로 나타남).
 *   메뉴를 바꾸면 이전 목록 접힘과 새 목록 펼침이 같은 길이·곡선으로 동시에 진행해 두 높이 합이 이전 높이에서 새 높이로 곧게 바뀐다(사이드바 높이가 튀지 않음).
 *   처음 그릴 때는 움직임 없이 펼친 상태(AnimatePresence initial=false). 현재 표시(메뉴 배경·목차 왼쪽 선)는 layoutId로 이전 위치에서 미끄러져 온다.
 *   aside는 layoutRoot: 고정(sticky) 사이드바라 렌더 사이 페이지 스크롤(화면 이동 시 맨 위로·목차 해시 스크롤)이 위치 변화로 잡히지 않게 사이드바 기준으로 잰다.
 *   동작 줄이기면 길이 0(useReducedMotion, 앱 MotionConfig reducedMotion="user"), 메뉴 누름(motionClasses.press 97%)은 버튼과 같이 styles.css 미디어쿼리가 전환 길이를 없앤다.
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
const INDICATOR = motionTransition("base", "standard");
/**
 * 위치 측정(layoutId·layoutRoot)은 문서가 있을 때만 켠다. Motion 투영 노드는 문서 스크롤(document.documentElement)을 읽으므로
 * DOM 없는 환경(시험 렌더러·서버 렌더)에서 켜면 오류가 난다. 브라우저에서는 늘 켜진다.
 */
const canMeasureLayout = () => typeof document !== "undefined" && document.documentElement != null;

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
  const itemVariants = subItemVariants(reduceMotion);
  const measure = canMeasureLayout();
  const collapse = () => {
    if (!expanded) return;
    setExpanded(false);
    toggleRef.current?.focus();
  };
  return (
    <motion.aside
      layoutRoot={measure}
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
        id={navId}
        aria-label={label}
        className={cn("grid gap-4 rounded-lg border bg-card p-3 md:max-h-[calc(100dvh-var(--top-nav-offset)-2*var(--page-block-padding))] md:overflow-y-auto", !expanded && "max-md:hidden")}
      >
        {heading && <h2 className="px-3 text-group text-muted-foreground">{heading}</h2>}
        {header}
        {groups.map((group, index) => (
          <section key={group.title ?? index} className={cn("flex flex-col gap-1.5", group.separated && "border-t pt-3")}>
            {group.title && <GroupTitle className="px-3 text-group text-muted-foreground">{group.title}</GroupTitle>}
            {group.items.map((item) => {
              const current = item.id === activeId;
              return (
                <Fragment key={item.id}>
                  <Link
                    to={item.to}
                    aria-current={current ? "page" : undefined}
                    onClick={collapse}
                    className={cn(
                      "relative isolate flex min-h-(--section-menu-height) items-center gap-3 rounded-md px-3 text-menu text-muted-foreground outline-none hover:bg-accent hover:text-accent-foreground focus-visible:ring-3 focus-visible:ring-ring/50",
                      motionClasses.press,
                      current && "font-semibold text-foreground",
                    )}
                  >
                    {/* 현재 항목 배경: 항목마다 같은 layoutId라 메뉴를 바꾸면 이전 항목에서 새 항목으로 미끄러진다. */}
                    {current && (
                      <motion.span
                        layoutId={measure ? `${navId}-current` : undefined}
                        aria-hidden="true"
                        className="absolute inset-0 -z-10 rounded-md bg-accent"
                        transition={reduceMotion ? { duration: 0 } : INDICATOR}
                      />
                    )}
                    {item.icon && <span className="flex size-5 items-center justify-center [&_svg]:size-4">{item.icon}</span>}
                    <span className={cn("min-w-0 flex-1 truncate", item.muted && "text-body", item.muted && !current && "text-muted-foreground")}>{item.label}</span>
                    {typeof item.count === "number" && (
                      <span className={cn("text-xs font-semibold tabular-nums text-muted-foreground", current && "text-foreground")}>{item.count}</span>
                    )}
                  </Link>
                  <AnimatePresence initial={false}>
                    {item.sub && item.sub.length > 0 && (
                      <SubListMotion key="sub" variants={listVariants}>
                        <ul data-slot="side-nav-sub" className="ml-6 grid gap-0.5 border-l">
                          {item.sub.map((sub) => (
                            <motion.li key={sub.id} variants={itemVariants}>
                              <Link
                                to={sub.to}
                                replace={sub.replace}
                                aria-current={sub.current ? (sub.currentType ?? "location") : undefined}
                                onClick={collapse}
                                className={cn(
                                  "relative -ml-px flex min-h-8 items-center rounded-r-md border-l-2 border-transparent px-3 text-body text-muted-foreground transition-colors outline-none hover:bg-accent hover:text-accent-foreground focus-visible:ring-3 focus-visible:ring-ring/50",
                                  sub.current && "font-semibold text-foreground",
                                )}
                              >
                                {/* 현재 영역 왼쪽 선(테두리 자리 2px): 목록마다 layoutId 하나라 현재 영역이 바뀌면 선이 미끄러진다. */}
                                {sub.current && (
                                  <motion.span
                                    layoutId={measure ? `${navId}-${item.id}-sub-current` : undefined}
                                    aria-hidden="true"
                                    className="absolute inset-y-0 -left-0.5 w-0.5 bg-foreground"
                                    transition={reduceMotion ? { duration: 0 } : INDICATOR}
                                  />
                                )}
                                <span className="min-w-0 flex-1 truncate">{sub.label}</span>
                                {typeof sub.count === "number" && (
                                  <span className={cn("text-xs font-semibold tabular-nums text-muted-foreground", sub.current && "text-foreground")}>{sub.count}</span>
                                )}
                              </Link>
                            </motion.li>
                          ))}
                        </ul>
                      </SubListMotion>
                    )}
                  </AnimatePresence>
                </Fragment>
              );
            })}
          </section>
        ))}
      </nav>
    </motion.aside>
  );
};
