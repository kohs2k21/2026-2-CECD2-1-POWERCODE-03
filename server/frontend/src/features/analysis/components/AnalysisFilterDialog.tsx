import { IconChevronDown, IconFilter } from "@tabler/icons-react";
import { Button } from "../../../components/ui/button";
import { Modal } from "../../../components/ui/Modal";
import { categoryThemeMap } from "../constants";
import type { AnalysisCategory } from "../types";

const severityCategories = ["All", "Critical", "Warning", "Info"] as const;

export const AnalysisFilterDialog = ({
  isOpen,
  onOpenChange,
  activeCategory,
  onCategoryChange,
}: {
  isOpen: boolean;
  onOpenChange: (isOpen: boolean) => void;
  activeCategory: AnalysisCategory;
  onCategoryChange: (category: AnalysisCategory) => void;
}) => {
  return (
    <Modal
      isOpen={isOpen}
      onOpenChange={onOpenChange}
      size="sm"
      trigger={
        <Button className="analysis-chip-button" size="sm" variant="outline">
          <IconFilter size={16} aria-hidden="true" />
          검색 필터
          <IconChevronDown size={16} aria-hidden="true" />
        </Button>
      }
      title="검색 필터 설정"
      description="목록에 표시할 이상 로그의 조건을 설정합니다."
    >
      <div className="analysis-filter-dialog">
        <div className="analysis-filter-group">
          <strong>위험도</strong>
          <p>현재 분류: {categoryThemeMap[activeCategory].label}</p>
          <div className="analysis-filter-group__options">
            {severityCategories.map((category) => (
              <Button
                key={category}
                type="button"
                variant={category === activeCategory ? "default" : "outline"}
                size="sm"
                className="analysis-chip-button"
                aria-pressed={category === activeCategory}
                onClick={() => {
                  onCategoryChange(category);
                  onOpenChange(false);
                }}
              >
                {categoryThemeMap[category].label}
              </Button>
            ))}
          </div>
        </div>
        <p>
          로그 상태와 업무 분류는 별도입니다. 보류·완료는 서버가 업무 분류
          상태를 제공한 이벤트만 표시합니다.
        </p>
      </div>
    </Modal>
  );
};
