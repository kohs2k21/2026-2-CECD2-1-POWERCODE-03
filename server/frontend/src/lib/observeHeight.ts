/**
 * 요소의 실제 높이를 문서 루트의 CSS 변수로 알린다(고정 영역의 위치·scroll-padding 기준). 해제하면 변수를 지워 토큰 기본값으로 돌아간다.
 * 호출한 쪽의 useEffect 정리 함수로 돌려준다.
 */
export const observeHeight = (node: HTMLElement | null, cssVariable: string) => {
  if (!node || typeof ResizeObserver === "undefined") return undefined;
  const root = node.ownerDocument.documentElement;
  const sync = () => root.style.setProperty(cssVariable, `${node.offsetHeight}px`);
  sync();
  const observer = new ResizeObserver(sync);
  observer.observe(node);
  return () => {
    observer.disconnect();
    root.style.removeProperty(cssVariable);
  };
};
