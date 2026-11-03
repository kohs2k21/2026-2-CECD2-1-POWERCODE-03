import type { ComponentType } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { IconBell, IconLayoutDashboard, IconPlug, IconUserCog } from "@tabler/icons-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { SideNav } from "@/components/layout/SideNav";
import { Button } from "@/components/ui/button";
import { settingsReturnPath, urlOptions, withSearchParam } from "@/app/routes";
import { useUrlOption } from "@/hooks/useUrlState";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import type { AuthUser } from "@/types/auth";
import { DisplaySettings } from "./sections/DisplaySettings";
import { IntegrationsSettings } from "./sections/IntegrationsSettings";
import { NotificationSettings } from "./sections/NotificationSettings";
import { ProfileSettings } from "./sections/ProfileSettings";

type SectionId = (typeof urlOptions.settingsSection)[number];
const sections: Record<SectionId, { label: string; icon: ComponentType<{ "aria-hidden"?: boolean }> }> = {
  notifications: { label: "알림 설정", icon: IconBell },
  integrations: { label: "연동 관리", icon: IconPlug },
  display: { label: "화면/위젯", icon: IconLayoutDashboard },
  profile: { label: "사용자 환경", icon: IconUserCog },
};
const sectionComponents: Record<SectionId, ComponentType<{ currentUser: AuthUser }>> = {
  notifications: NotificationSettings,
  integrations: IntegrationsSettings,
  display: DisplaySettings,
  profile: ProfileSettings,
};

/** 설정: 독립 페이지(section URL), 종료 시 진입 전 경로·탭·필터로 복귀(returnTo). */
export const SettingsPage = () => {
  const { user } = useAuthSession();
  const [params] = useSearchParams();
  const section = useUrlOption("section", urlOptions.settingsSection);
  const Active = sectionComponents[section.value];
  return (
    <div className="grid gap-6">
      <div>
        <Button asChild variant="outline">
          <Link to={settingsReturnPath(params.get("returnTo"))}>이전 화면으로 돌아가기</Link>
        </Button>
      </div>
      <div className="flex flex-col gap-6 md:flex-row md:items-start">
        <SideNav
          label="설정 항목"
          heading="설정"
          menuId="settings-sections"
          activeId={section.value}
          groups={[
            {
              items: urlOptions.settingsSection.map((id) => {
                const Icon = sections[id].icon;
                return {
                  id,
                  label: sections[id].label,
                  icon: <Icon aria-hidden />,
                  to: "/settings" + withSearchParam(params, "section", id),
                };
              }),
            },
          ]}
        />
        <div className="grid min-w-0 flex-1 gap-6">
          <PageHeader
            title={sections[section.value].label}
            actions={
              <Button variant="outline" disabled title="설정 저장 기능은 현재 서버에서 지원하지 않습니다.">
                저장 미지원
              </Button>
            }
          />
          <Active currentUser={user!} />
        </div>
      </div>
    </div>
  );
};
