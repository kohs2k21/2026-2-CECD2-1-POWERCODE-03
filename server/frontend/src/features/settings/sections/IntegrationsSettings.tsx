import { Button } from "../../../components/ui/button";
import { IntegrationCard } from "../components/IntegrationCard";
import { SettingsTextInput } from "../components/SettingsTextInput";
import { integrationMeta } from "../settingsConfig";

export const IntegrationsSettings = () => {
  const SlackIcon = integrationMeta.slack.icon;
  const EmailIcon = integrationMeta.email.icon;
  const WebhookIcon = integrationMeta.webhook.icon;
  const disabledReason =
    "서버 연동을 지원하지 않아 URL 입력, 연결 설정, 테스트를 사용할 수 없습니다.";

  return (
    <div className="settings-grid">
      <IntegrationCard
        title={integrationMeta.slack.title}
        titleLabel={integrationMeta.slack.titleLabel}
        description={integrationMeta.slack.description}
        enabled={false}
        disabled
        disabledReason={disabledReason}
        icon={<SlackIcon size={24} style={{ color: integrationMeta.slack.iconColor }} />}
        iconBackground={integrationMeta.slack.iconBackground}
      >
        <SettingsTextInput
          label="WEBHOOK URL"
          type="password"
          value=""
          placeholder="서버 지원 후 설정할 수 있습니다."
          disabled
          action={<Button size="sm" variant="outline" disabled>테스트 미지원</Button>}
        />
      </IntegrationCard>

      <IntegrationCard
        title={integrationMeta.email.title}
        titleLabel={integrationMeta.email.titleLabel}
        description={integrationMeta.email.description}
        enabled={false}
        disabled
        disabledReason={disabledReason}
        icon={<EmailIcon size={24} style={{ color: integrationMeta.email.iconColor }} />}
        iconBackground={integrationMeta.email.iconBackground}
      >
        <div className="settings-smtp-grid">
          <SettingsTextInput
            label="SMTP 호스트"
            value=""
            placeholder="서버 지원 후 설정할 수 있습니다."
            disabled
          />
          <SettingsTextInput
            label="포트"
            value=""
            placeholder="서버 지원 후 설정할 수 있습니다."
            disabled
          />
          <SettingsTextInput
            gridColumn="span 2"
            label="발신 계정"
            value=""
            placeholder="서버 지원 후 설정할 수 있습니다."
            disabled
            action={<Button size="sm" variant="outline" disabled>테스트 미지원</Button>}
          />
        </div>
      </IntegrationCard>

      <IntegrationCard
        title={integrationMeta.webhook.title}
        titleLabel={integrationMeta.webhook.titleLabel}
        description={integrationMeta.webhook.description}
        enabled={false}
        disabled
        disabledReason={disabledReason}
        icon={<WebhookIcon size={24} style={{ color: integrationMeta.webhook.iconColor }} />}
        iconBackground={integrationMeta.webhook.iconBackground}
      >
        <SettingsTextInput
          label="엔드포인트 URL"
          value=""
          placeholder="서버 지원 후 설정할 수 있습니다."
          disabled
          action={<Button size="sm" variant="outline" disabled>테스트 미지원</Button>}
        />
      </IntegrationCard>
    </div>
  );
};
