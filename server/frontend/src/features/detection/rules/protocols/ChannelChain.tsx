import { Fragment } from "react";
import { IconArrowRight } from "@tabler/icons-react";
import { cn } from "@/lib/utils";

/**
 * 허용 경로 표시: 채널을 순서대로 화살표로 잇는다. 값은 원래 문자열 그대로(공백이 보이도록 whitespace-pre + 테두리 — rule-set.md v3.6 권고).
 * 같은 채널이 연달아 나와도 합치지 않는다(protocol-rules.md 3.3 (a) — 위치 = 단계).
 */
export const ChannelChain = ({ channels, className }: { channels: readonly string[]; className?: string }) => (
  <span data-channel-chain className={cn("flex flex-wrap items-center gap-1", className)} aria-label={channels.join(" → ")}>
    {channels.map((channel, index) => (
      <Fragment key={index}>
        {index > 0 && <IconArrowRight aria-hidden="true" className="size-3.5 text-muted-foreground" />}
        <span data-channel={channel} className="rounded border px-1.5 py-0.5 font-mono text-xs whitespace-pre">{channel}</span>
      </Fragment>
    ))}
  </span>
);
