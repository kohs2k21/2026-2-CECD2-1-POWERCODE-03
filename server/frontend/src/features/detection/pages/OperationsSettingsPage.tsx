import { Link, useLocation } from "react-router-dom";
import { ScreenTitle } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { paths } from "@/app/routes";
import type { DetectionReturnState } from "../DetectionLayout";

/**
 * 탐지 설정 › 운영 설정(골격: 숨긴 화면 제목 + 와이어프레임의 [모델 교체] 진입만). 운영 버전 상태·교체 이력·작업은
 * 하위 Issue(운영 설정 + 교체)에서 API와 함께.
 */
export const OperationsSettingsPage = () => {
  const location = useLocation();
  return (
    <div className="grid gap-6">
      <ScreenTitle>운영 설정</ScreenTitle>
      {/* 제목 줄 옆에 있던 명령은 본문 첫 영역(오른쪽 정렬)으로 */}
      <div className="flex flex-wrap justify-end gap-2">
        <Button asChild>
          <Link
            to={paths.detectionReplace}
            state={{ returnTo: location.pathname + location.search } satisfies DetectionReturnState}
          >
            모델 교체
          </Link>
        </Button>
      </div>
    </div>
  );
};
