import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ReactNode } from "react";
import type { ViewId } from "../../../../types/app";
import {
  channelRiskData,
  chartPalette,
  deliveryData,
  epsTrendData,
  interfaceAnomaliesData,
  liveAnomaliesLogs,
  recentAlertsLogs,
  riskDistributionData,
  systemMetricsData,
  transactionTrendData,
} from "../../data/widgetChartData";
const tooltipStyle = {
  background: "var(--canvas-soft-2)",
  borderColor: "var(--hairline-strong)",
  borderRadius: "8px",
  color: "var(--ink)",
  fontSize: "12px",
};

type LiveFeedWidgetProps = {
  onSelectView?: (view: ViewId) => void;
};

const TransactionTrendWidget = () => (
  <div className="chart-container" style={{ width: "100%", height: "100%", position: "relative" }}>
    <ResponsiveContainer width="100%" height="80%">
      <AreaChart data={transactionTrendData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
        <defs>
          <linearGradient id="colorTx" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="var(--link)" stopOpacity={0.2} />
            <stop offset="95%" stopColor="var(--link)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--hairline)" vertical={false} />
        <XAxis dataKey="time" stroke="var(--mute)" fontSize={11} tickLine={false} />
        <YAxis stroke="var(--mute)" fontSize={11} tickLine={false} axisLine={false} />
        <RechartsTooltip contentStyle={tooltipStyle} />
        <Area type="monotone" dataKey="transactions" name="전체 트랜잭션" stroke="var(--link)" strokeWidth={2} fillOpacity={1} fill="url(#colorTx)" />
        <Area type="monotone" dataKey="anomalies" name="이상 징후" stroke="var(--error)" strokeWidth={2} fill="none" dot={{ r: 3, stroke: "var(--error)", strokeWidth: 1, fill: "var(--canvas)" }} />
      </AreaChart>
    </ResponsiveContainer>
    <div className="chart-legend" style={{ display: "flex", justifyContent: "flex-end", gap: "12px", fontSize: "11px", color: "var(--mute)", marginTop: "4px", paddingRight: "8px" }}>
      <span style={{ display: "flex", alignItems: "center", gap: "4px" }}><span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "var(--link)" }} />전체 트랜잭션</span>
      <span style={{ display: "flex", alignItems: "center", gap: "4px" }}><span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "var(--error)" }} />이상 징후</span>
    </div>
  </div>
);

const InterfaceAnomaliesWidget = () => (
  <div className="chart-container" style={{ width: "100%", height: "100%" }}>
    <ResponsiveContainer width="100%" height="95%">
      <BarChart data={interfaceAnomaliesData} layout="vertical" margin={{ top: 5, right: 15, left: 15, bottom: 5 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--hairline)" horizontal={false} />
        <XAxis type="number" stroke="var(--mute)" fontSize={11} tickLine={false} axisLine={false} />
        <YAxis dataKey="name" type="category" stroke="var(--ink)" fontSize={11} tickLine={false} axisLine={false} width={80} />
        <RechartsTooltip contentStyle={tooltipStyle} />
        <Bar dataKey="value" name="이상 건수" radius={[0, 4, 4, 0]} barSize={12}>
          {interfaceAnomaliesData.map((entry, index) => (
            <Cell key={`cell-${entry.name}`} fill={chartPalette[index % chartPalette.length]} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  </div>
);

const RiskDistributionWidget = () => {
  const total = riskDistributionData.reduce((sum, item) => sum + item.value, 0);

  return (
    <div className="chart-container" style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", justifyContent: "center", position: "relative" }}>
      <span className="widget-c-subtitle" style={{ fontSize: "11px", color: "var(--mute)", display: "block", marginTop: "-16px", marginBottom: "8px" }}>
        금일 (최근 24시간 기준)
      </span>
      <div style={{ position: "relative", width: "100%", height: "120px" }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie data={riskDistributionData} cx="50%" cy="50%" innerRadius={38} outerRadius={52} paddingAngle={4} dataKey="value">
              {riskDistributionData.map((entry) => (
                <Cell key={entry.name} fill={entry.color} />
              ))}
            </Pie>
            <RechartsTooltip contentStyle={{ ...tooltipStyle, fontSize: "11px" }} />
          </PieChart>
        </ResponsiveContainer>
        <div style={{ position: "absolute", top: "50%", left: "50%", transform: "translate(-50%, -50%)", textAlign: "center", pointerEvents: "none" }}>
          <span style={{ fontSize: "16px", fontWeight: "700", color: "var(--ink)", display: "block", lineHeight: 1.1 }}>{total}건</span>
          <span style={{ fontSize: "9px", color: "var(--mute)", display: "block" }}>총 이상탐지</span>
        </div>
      </div>
      <div className="chart-legend" style={{ display: "flex", justifyContent: "center", gap: "8px", fontSize: "10px", color: "var(--mute)", marginTop: "8px" }}>
        {riskDistributionData.map((item) => (
          <span key={item.name} style={{ display: "flex", alignItems: "center", gap: "3px" }}>
            <span style={{ width: "6px", height: "6px", borderRadius: "50%", background: item.color }} />
            {item.name} ({item.value})
          </span>
        ))}
      </div>
    </div>
  );
};

const LiveFeedWidget = ({ onSelectView }: LiveFeedWidgetProps) => (
  <div className="live-feed-widget" style={{ display: "flex", flexDirection: "column", gap: "8px", height: "100%", overflow: "hidden" }}>
    <div style={{ display: "flex", alignItems: "center", gap: "6px", marginBottom: "2px" }}>
      <span className="live-pulse" />
      <span style={{ fontSize: "12px", color: "var(--mute)" }}>실시간 모니터링 활성화됨</span>
    </div>
    <div style={{ display: "flex", flexDirection: "column", gap: "6px", overflowY: "auto", flex: 1, paddingRight: "4px" }}>
      {liveAnomaliesLogs.map((log) => (
        <div
          key={log.id}
          onClick={() => onSelectView?.("analysis")}
          style={{ padding: "8px var(--space-sm)", border: "1px solid var(--hairline)", borderRadius: "var(--radius-md)", background: "var(--canvas-soft)", cursor: "pointer", transition: "all 0.15s ease", display: "flex", flexDirection: "column", gap: "2px" }}
          className="live-feed-item"
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", width: "100%" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
              <span className={`status-dot status-dot--${log.type.toLowerCase()}`} style={{ width: "6px", height: "6px" }} />
              <span style={{ fontSize: "11px", fontWeight: "700", color: "var(--ink)" }}>{log.interfaceId}</span>
            </div>
            <span style={{ fontSize: "10px", color: "var(--mute)" }}>{log.time}</span>
          </div>
          <p style={{ fontSize: "11px", color: "var(--body)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", margin: 0 }}>
            {log.msg}
          </p>
        </div>
      ))}
    </div>
  </div>
);

const ChannelRiskWidget = () => (
  <div className="chart-container" style={{ width: "100%", height: "100%" }}>
    <ResponsiveContainer width="100%" height="95%">
      <BarChart data={channelRiskData} margin={{ top: 10, right: 10, left: -25, bottom: 5 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--hairline)" vertical={false} />
        <XAxis dataKey="name" stroke="var(--mute)" fontSize={11} tickLine={false} />
        <YAxis stroke="var(--mute)" fontSize={11} tickLine={false} axisLine={false} />
        <RechartsTooltip contentStyle={tooltipStyle} />
        <Bar dataKey="value" name="위험 강도" radius={[4, 4, 0, 0]} barSize={16}>
          {channelRiskData.map((entry, index) => (
            <Cell key={`cell-${entry.name}`} fill={chartPalette[index % chartPalette.length]} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  </div>
);

const AlertDeliveryWidget = () => (
  <div className="chart-container" style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", justifyContent: "center", position: "relative" }}>
    <span className="widget-c-subtitle" style={{ fontSize: "11px", color: "var(--mute)", display: "block", marginTop: "-16px", marginBottom: "8px", textAlign: "center" }}>
      금일 누적 발송 요약
    </span>
    <div style={{ position: "relative", width: "100%", height: "110px" }}>
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie data={deliveryData} cx="50%" cy="50%" innerRadius={36} outerRadius={48} paddingAngle={3} dataKey="value">
            {deliveryData.map((entry) => (
              <Cell key={entry.name} fill={entry.color} />
            ))}
          </Pie>
          <RechartsTooltip contentStyle={{ ...tooltipStyle, fontSize: "11px" }} />
        </PieChart>
      </ResponsiveContainer>
      <div style={{ position: "absolute", top: "50%", left: "50%", transform: "translate(-50%, -50%)", textAlign: "center", pointerEvents: "none" }}>
        <span style={{ fontSize: "15px", fontWeight: "700", color: "var(--ink)", display: "block", lineHeight: 1.1 }}>98%</span>
        <span style={{ fontSize: "8px", color: "var(--mute)", display: "block" }}>성공률</span>
      </div>
    </div>
    <div className="chart-legend" style={{ display: "flex", justifyContent: "center", gap: "10px", fontSize: "10px", color: "var(--mute)", marginTop: "6px" }}>
      {deliveryData.map((item) => (
        <span key={item.name} style={{ display: "flex", alignItems: "center", gap: "3px" }}>
          <span style={{ width: "6px", height: "6px", borderRadius: "50%", background: item.color }} />
          {item.name} ({item.value}건)
        </span>
      ))}
    </div>
  </div>
);

const CollectorStatusWidget = () => (
  <div className="chart-container" style={{ width: "100%", height: "100%" }}>
    <ResponsiveContainer width="100%" height="80%">
      <AreaChart data={epsTrendData} margin={{ top: 10, right: 10, left: -15, bottom: 0 }}>
        <defs>
          <linearGradient id="colorEps" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="var(--warning)" stopOpacity={0.2} />
            <stop offset="95%" stopColor="var(--warning)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--hairline)" vertical={false} />
        <XAxis dataKey="time" stroke="var(--mute)" fontSize={11} tickLine={false} />
        <YAxis stroke="var(--mute)" fontSize={11} tickLine={false} axisLine={false} />
        <RechartsTooltip contentStyle={tooltipStyle} />
        <Area type="monotone" dataKey="eps" name="수집 속도 (EPS)" stroke="var(--warning)" strokeWidth={2} fillOpacity={1} fill="url(#colorEps)" />
      </AreaChart>
    </ResponsiveContainer>
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "0 6px", marginTop: "2px" }}>
      <span style={{ fontSize: "10px", color: "var(--mute)" }}>실시간 수집 속도</span>
      <span style={{ fontSize: "11px", fontWeight: "700", color: "var(--warning)" }}>평균 1,260 EPS</span>
    </div>
  </div>
);

const SystemStatusWidget = () => (
  <div className="system-status-widget" style={{ width: "100%", height: "100%", display: "flex", justifyContent: "space-around", alignItems: "center", padding: "4px 0" }}>
    {systemMetricsData.map((metric) => {
      const data = [
        { name: "used", value: metric.value },
        { name: "free", value: 100 - metric.value },
      ];
      return (
        <div key={metric.name} style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
          <div style={{ width: "56px", height: "56px", position: "relative" }}>
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie data={data} cx="50%" cy="50%" innerRadius={18} outerRadius={25} startAngle={90} endAngle={-270} dataKey="value">
                  {data.map((entry, idx) => (
                    <Cell key={`cell-${entry.name}`} fill={idx === 0 ? metric.color : "var(--hairline)"} />
                  ))}
                </Pie>
              </PieChart>
            </ResponsiveContainer>
            <div style={{ position: "absolute", top: "50%", left: "50%", transform: "translate(-50%, -50%)", textAlign: "center", pointerEvents: "none" }}>
              <span style={{ fontSize: "10px", fontWeight: "700", color: "var(--ink)", display: "block" }}>{metric.value}%</span>
            </div>
          </div>
          <span style={{ fontSize: "10px", color: "var(--mute)", marginTop: "4px", fontWeight: "500" }}>{metric.name}</span>
        </div>
      );
    })}
  </div>
);

const RecentAlertsWidget = () => (
  <div className="recent-alerts-widget" style={{ display: "flex", flexDirection: "column", gap: "6px", height: "100%", overflow: "hidden" }}>
    <div style={{ display: "flex", flexDirection: "column", gap: "5px", overflowY: "auto", flex: 1, paddingRight: "2px" }}>
      {recentAlertsLogs.map((log) => (
        <div key={log.id} style={{ padding: "6px 8px", border: "1px solid var(--hairline)", borderRadius: "var(--radius-md)", background: "var(--canvas-soft)", display: "flex", flexDirection: "column", gap: "2px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", width: "100%" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "5px" }}>
              <span style={{ fontSize: "9px", fontWeight: "700", padding: "1px 4px", borderRadius: "4px", background: log.channel === "Email" ? "rgba(67, 97, 238, 0.1)" : "rgba(16, 185, 129, 0.1)", color: log.channel === "Email" ? "var(--link)" : "#10b981", border: `1px solid ${log.channel === "Email" ? "rgba(67, 97, 238, 0.2)" : "rgba(16, 185, 129, 0.2)"}` }}>
                {log.channel}
              </span>
              <span style={{ fontSize: "10px", color: "var(--mute)", maxWidth: "80px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{log.target}</span>
            </div>
            <span style={{ fontSize: "9px", color: "var(--mute)" }}>{log.time}</span>
          </div>
          <p style={{ fontSize: "10px", color: "var(--body)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", margin: 0 }}>{log.msg}</p>
        </div>
      ))}
    </div>
  </div>
);

export const renderFullWidget = (
  widgetId: string,
  onSelectView?: (view: ViewId) => void,
): ReactNode | null => {
  const chartById: Record<string, ReactNode> = {
    "system-status": <SystemStatusWidget />,
    "severity-trend": <TransactionTrendWidget />,
    "response-code-change": <InterfaceAnomaliesWidget />,
    "major-risk-events": <RiskDistributionWidget />,
    "recent-anomaly-logs": <LiveFeedWidget onSelectView={onSelectView} />,
    "recent-alerts": <RecentAlertsWidget />,
    "channel-risk-rank": <ChannelRiskWidget />,
    "alert-delivery": <AlertDeliveryWidget />,
    "collector-status": <CollectorStatusWidget />,
  };

  return chartById[widgetId] ?? null;
};
