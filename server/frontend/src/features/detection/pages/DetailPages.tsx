import { useParams } from "react-router-dom";
import { ScreenTitle } from "@/components/layout/PageHeader";

/** 상세 화면 골격: 숨긴 화면 제목(대상 ID 포함)만. 상단 뒤로가기 아이콘은 DetectionLayout이 표시한다. */
const DetailPage = ({ title, idParam }: { title: string; idParam: string }) => {
  const params = useParams();
  return (
    <div className="grid gap-6">
      <ScreenTitle>
        {title} {params[idParam]}
      </ScreenTitle>
    </div>
  );
};

export const JobDetailPage = () => <DetailPage title="작업 상세" idParam="jobId" />;
