import { jobStateLabels } from "../components/DetectionQueryBoundary";
import { performanceAssessment } from "../data/performanceCriteria";
import type { DetectionData, VersionBundle } from "../data/types";
import { configurationChangeCount } from "./VersionDetails";

export const VersionReviewSummary = ({
  active,
  target,
  data,
  approved,
}: {
  active: VersionBundle | undefined;
  target: VersionBundle;
  data: DetectionData;
  approved: boolean;
}) => {
  const assessment = performanceAssessment(target, data);
  const artifact = data.modelArtifacts.find(
    (item) => item.id === target.modelArtifactId,
  );
  const availability =
    target.artifactAvailable === false || artifact?.available === false
      ? false
      : target.artifactAvailable === true && artifact?.available === true
        ? true
        : null;
  const compatibility =
    target.compatible === false || artifact?.compatible === false
      ? false
      : target.compatible === true && artifact?.compatible === true
        ? true
        : null;
  const count = configurationChangeCount(active, target, data);
  return (
    <section
      className="version-review-summary"
      aria-label="운영 적용 검토 요약"
    >
      <dl className="detection-summary">
        <div>
          <dt>변경 항목</dt>
          <dd>{count == null ? "미확인" : `${count}개`}</dd>
        </div>
        <div>
          <dt>평가 처리 상태</dt>
          <dd>
            {assessment.result
              ? jobStateLabels[assessment.result.state]
              : "미확인"}
          </dd>
        </div>
        <div>
          <dt>성능기준 확정</dt>
          <dd>
            {assessment.confirmed
              ? `${assessment.spec!.name} · ${assessment.spec!.revision}`
              : "미확정"}
          </dd>
        </div>
        <div>
          <dt>성능기준 검토</dt>
          <dd>
            {assessment.passed
              ? "확정 기준 충족 · test 최종 확인"
              : "적용 근거 확인 필요"}
          </dd>
        </div>
        <div>
          <dt>산출물 가용성</dt>
          <dd>
            {availability === true
              ? "가용 확인"
              : availability === false
                ? "없음"
                : "미확인"}
          </dd>
        </div>
        <div>
          <dt>입력 호환성</dt>
          <dd>
            {compatibility === true
              ? "호환 확인"
              : compatibility === false
                ? "호환 불가"
                : "미확인"}
          </dd>
        </div>
        <div>
          <dt>관리자 승인</dt>
          <dd>{approved ? "검토 확인됨" : "검토 대기"}</dd>
        </div>
      </dl>
      <p className="detection-note">
        평가 처리 성공과 성능기준 충족은 별개입니다. 최종 적용은 서버에서 근거와
        현재 운영 버전을 재확인합니다.
      </p>
    </section>
  );
};
