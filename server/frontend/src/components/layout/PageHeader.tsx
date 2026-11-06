import type { ReactNode } from "react";

/**
 * 화면 제목(시각적으로 숨김): 운영 현황·이상 분석·탐지 설정·설정은 상단 메뉴·사이드바의 현재 표시로 위치를 알 수 있어
 * 제목 줄을 두지 않는다(사용자 11/03). 화면 낭독기·문서 구조용 h1만 남긴다.
 */
export const ScreenTitle = ({ children }: { children: ReactNode }) => <h1 className="sr-only">{children}</h1>;
