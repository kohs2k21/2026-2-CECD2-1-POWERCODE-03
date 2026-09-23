import { SettingsCard } from "../components/SettingsCard";
import { SettingRow } from "../components/SettingRow";
import {
  dashboardDisplaySettings,
  homeWidgetSettings,
} from "../settingsConfig";
import { appTheme, darkThemeSupported } from "../../../lib/theme";

export const DisplaySettings = () => (
  <div className="settings-grid">
    <SettingsCard title="대시보드 표시">
      {dashboardDisplaySettings.map((setting) => (
        <SettingRow
          key={setting.label}
          {...setting}
          enabled={false}
          disabled
          disabledReason="아직 대시보드 화면에 적용되지 않는 설정입니다."
        />
      ))}
    </SettingsCard>

    <SettingsCard title="테마 선택">
      <div className="settings-theme-grid">
        <button
          type="button"
          aria-pressed={appTheme === "light"}
          aria-label="화이트 (라이트 테마), 현재 적용 중"
          title="현재 적용 중인 테마"
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: "10px",
            padding: "16px",
            borderRadius: "var(--radius-lg)",
            border: "2px solid var(--theme-color, #7c3aed)",
            background: "#ffffff",
            color: "#1a1a1a",
            cursor: "default",
          }}
          className="theme-option"
        >
          <div className="theme-preview theme-preview--light">
            <div className="theme-preview__sidebar" />
            <div className="theme-preview__accent" />
            <div className="theme-preview__line" />
          </div>
          <span style={{ fontSize: "12px", fontWeight: "bold" }}>
            화이트 (라이트 테마)
          </span>
        </button>

        <button
          type="button"
          disabled={!darkThemeSupported}
          aria-pressed={false}
          aria-describedby="dark-theme-unavailable"
          title="다크 테마는 아직 지원되지 않습니다."
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: "10px",
            padding: "16px",
            borderRadius: "var(--radius-lg)",
            border: "2px solid var(--hairline)",
            background: "#121212",
            color: "#ffffff",
            cursor: "not-allowed",
            opacity: 0.55,
          }}
          className="theme-option"
        >
          <div className="theme-preview theme-preview--dark">
            <div className="theme-preview__sidebar" />
            <div className="theme-preview__accent" />
            <div className="theme-preview__line" />
          </div>
          <span style={{ fontSize: "12px", fontWeight: "bold" }}>
            블랙 (다크 테마, 준비 중)
          </span>
        </button>
        <p
          id="dark-theme-unavailable"
          className="settings-theme-note"
          style={{ gridColumn: "1 / -1", margin: 0 }}
        >
          현재 화면은 라이트 테마입니다. 다크 테마는 아직 지원되지 않아 선택할 수 없습니다.
        </p>
      </div>
    </SettingsCard>

    <SettingsCard title="홈 위젯">
      {homeWidgetSettings.map((setting) => (
        <SettingRow
          key={setting.label}
          {...setting}
          enabled={false}
          disabled
          disabledReason="아직 홈 화면에 적용되지 않는 설정입니다."
        />
      ))}
    </SettingsCard>
  </div>
);
