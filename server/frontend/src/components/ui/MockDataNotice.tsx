type MockDataNoticeProps = {
  feature: string;
};

export const MockDataNotice = ({ feature }: MockDataNoticeProps) => (
  <div
    role="status"
    aria-label={feature + " 목업 데이터 안내"}
    style={{
      display: "flex",
      alignItems: "center",
      gap: 10,
      padding: "9px 12px",
      border: "1px solid #e4e7ec",
      borderRadius: 10,
      color: "#5f6368",
      background: "#f8f9fb",
      fontSize: 12,
      lineHeight: 1.45,
    }}
  >
    <span
      style={{
        padding: "2px 7px",
        borderRadius: 999,
        color: "#475467",
        background: "#e4e7ec",
        fontSize: 10,
        fontWeight: 700,
        letterSpacing: "0.06em",
      }}
    >
      MOCK
    </span>
    <span>{feature}는 실제 API 계약 전까지 목업 데이터를 표시합니다.</span>
  </div>
);
