import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
} from "recharts";
import {
  channelRiskData,
  chartPalette,
  deliveryData,
  epsTrendData,
  interfaceAnomaliesData,
  riskDistributionData,
  transactionTrendData,
} from "../../data/widgetChartData";
const MiniSystemStatus = () => (
  <div style={{ width: "100%", height: "100%", display: "flex", justifyContent: "space-around", alignItems: "center", opacity: 0.8, pointerEvents: "none" }}>
    {[42, 68, 24].map((val, idx) => {
      const color = idx === 0 ? "#4361ee" : idx === 1 ? "#ffb703" : "#10b981";
      const data = [
        { name: "used", value: val },
        { name: "free", value: 100 - val },
      ];
      return (
        <div key={idx} style={{ width: "16px", height: "16px" }}>
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={data} cx="50%" cy="50%" innerRadius={4} outerRadius={7} startAngle={90} endAngle={-270} dataKey="value">
                {data.map((entry, eIdx) => (
                  <Cell key={`mini-cell-${entry.name}`} fill={eIdx === 0 ? color : "var(--hairline)"} />
                ))}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
        </div>
      );
    })}
  </div>
);

const MiniRecentAlerts = () => (
  <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", justifyContent: "center", gap: "3px", padding: "4px", opacity: 0.8, pointerEvents: "none" }}>
    {[1, 2, 3].map((i) => (
      <div key={i} style={{ display: "flex", flexDirection: "column", gap: "1px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", height: "3px" }}>
          <div style={{ width: "10px", height: "3px", borderRadius: "1px", background: i === 2 ? "#10b981" : "var(--link)" }} />
          <div style={{ width: "15px", height: "2px", borderRadius: "1px", background: "var(--hairline-strong)" }} />
        </div>
        <div style={{ width: "100%", height: "2px", borderRadius: "1px", background: "var(--hairline)" }} />
      </div>
    ))}
  </div>
);

const MiniTransactionTrend = () => (
  <div style={{ width: "100%", height: "100%", opacity: 0.8, pointerEvents: "none" }}>
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={transactionTrendData} margin={{ top: 2, right: 2, left: 2, bottom: 2 }}>
        <Area type="monotone" dataKey="transactions" stroke="var(--link)" fill="var(--link)" fillOpacity={0.1} strokeWidth={1} />
        <Area type="monotone" dataKey="anomalies" stroke="var(--error)" fill="none" strokeWidth={1.5} />
      </AreaChart>
    </ResponsiveContainer>
  </div>
);

const MiniInterfaceAnomalies = () => (
  <div style={{ width: "100%", height: "100%", opacity: 0.8, pointerEvents: "none" }}>
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={interfaceAnomaliesData} layout="vertical" margin={{ top: 2, right: 2, left: 2, bottom: 2 }}>
        <Bar dataKey="value" fill="var(--link)" radius={[0, 2, 2, 0]} barSize={4} />
      </BarChart>
    </ResponsiveContainer>
  </div>
);

const MiniRiskDistribution = () => (
  <div style={{ width: "100%", height: "100%", opacity: 0.8, pointerEvents: "none" }}>
    <ResponsiveContainer width="100%" height="100%">
      <PieChart>
        <Pie data={riskDistributionData} cx="50%" cy="50%" innerRadius={14} outerRadius={22} paddingAngle={2} dataKey="value">
          {riskDistributionData.map((entry) => (
            <Cell key={`cell-${entry.name}`} fill={entry.color} />
          ))}
        </Pie>
      </PieChart>
    </ResponsiveContainer>
  </div>
);

const MiniChannelRisk = () => (
  <div style={{ width: "100%", height: "100%", opacity: 0.8, pointerEvents: "none" }}>
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={channelRiskData} margin={{ top: 4, right: 4, left: 4, bottom: 4 }}>
        <Bar dataKey="value" fill="var(--link)" radius={[2, 2, 0, 0]} barSize={6}>
          {channelRiskData.map((entry, index) => (
            <Cell key={`cell-${entry.name}`} fill={chartPalette[index % chartPalette.length]} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  </div>
);

const MiniAlertDelivery = () => (
  <div style={{ width: "100%", height: "100%", opacity: 0.8, pointerEvents: "none" }}>
    <ResponsiveContainer width="100%" height="100%">
      <PieChart>
        <Pie data={deliveryData} cx="50%" cy="50%" innerRadius={10} outerRadius={20} paddingAngle={1} dataKey="value">
          {deliveryData.map((entry) => (
            <Cell key={`cell-${entry.name}`} fill={entry.color} />
          ))}
        </Pie>
      </PieChart>
    </ResponsiveContainer>
  </div>
);

const MiniCollectorStatus = () => (
  <div style={{ width: "100%", height: "100%", opacity: 0.8, pointerEvents: "none" }}>
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={epsTrendData} margin={{ top: 2, right: 2, left: 2, bottom: 2 }}>
        <defs>
          <linearGradient id="miniColorEps" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="var(--warning)" stopOpacity={0.4} />
            <stop offset="95%" stopColor="var(--warning)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <Area type="monotone" dataKey="eps" stroke="var(--warning)" fill="url(#miniColorEps)" strokeWidth={1} />
      </AreaChart>
    </ResponsiveContainer>
  </div>
);

const MiniRecentAnomalyLogs = () => (
  <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", justifyContent: "center", gap: "4px", padding: "4px", opacity: 0.8, pointerEvents: "none" }}>
    {[
      { color: "var(--error)", w1: "15px", w2: "30px" },
      { color: "var(--warning)", w1: "18px", w2: "25px" },
      { color: "var(--link)", w1: "12px", w2: "35px" },
    ].map((item, idx) => (
      <div key={idx} style={{ display: "flex", alignItems: "center", gap: "4px" }}>
        <span style={{ width: "4px", height: "4px", borderRadius: "50%", background: item.color }} />
        <div style={{ display: "flex", flexDirection: "column", gap: "1px", flex: 1 }}>
          <div style={{ width: item.w1, height: "3px", borderRadius: "1px", background: "var(--hairline-strong)" }} />
          <div style={{ width: item.w2, height: "2px", borderRadius: "1px", background: "var(--hairline)" }} />
        </div>
      </div>
    ))}
  </div>
);

const MiniImpactScope = () => {
  const data = [
    { name: "CH", value: 3 },
    { name: "PR", value: 4 },
    { name: "TX", value: 7 },
  ];
  return (
    <div style={{ width: "100%", height: "100%", opacity: 0.8, pointerEvents: "none" }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 2, right: 2, left: 2, bottom: 2 }}>
          <Bar dataKey="value" fill="var(--link)" radius={[2, 2, 0, 0]} barSize={5}>
            {data.map((entry, idx) => (
              <Cell key={`cell-${entry.name}`} fill={["#4361ee", "#ffb703", "#10b981"][idx]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
};

const MiniLatencyChange = () => {
  const data = [
    { time: "1", value: 120 },
    { time: "2", value: 130 },
    { time: "3", value: 125 },
    { time: "4", value: 180 },
    { time: "5", value: 170 },
    { time: "6", value: 195 },
  ];
  return (
    <div style={{ width: "100%", height: "100%", opacity: 0.8, pointerEvents: "none" }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 2, right: 2, left: 2, bottom: 2 }}>
          <defs>
            <linearGradient id="miniLatencyColor" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="var(--warning)" stopOpacity={0.4} />
              <stop offset="95%" stopColor="var(--warning)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <Area type="monotone" dataKey="value" stroke="var(--warning)" fill="url(#miniLatencyColor)" strokeWidth={1} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
};

const MiniFailureRateChange = () => {
  const data = [
    { name: "fail", value: 1.8 },
    { name: "success", value: 98.2 },
  ];
  return (
    <div style={{ width: "100%", height: "100%", opacity: 0.8, pointerEvents: "none" }}>
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie data={data} cx="50%" cy="50%" innerRadius={6} outerRadius={10} startAngle={90} endAngle={-270} dataKey="value">
            <Cell fill="var(--error)" />
            <Cell fill="var(--hairline)" />
          </Pie>
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
};

const MiniModelStatus = () => {
  const data = [
    { x: 1, y: 10 },
    { x: 2, y: 15 },
    { x: 3, y: 8 },
    { x: 4, y: 12 },
    { x: 5, y: 18 },
    { x: 6, y: 11 },
  ];
  return (
    <div style={{ width: "100%", height: "100%", opacity: 0.8, pointerEvents: "none" }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 2, right: 2, left: 2, bottom: 2 }}>
          <Area type="monotone" dataKey="y" stroke="var(--link)" fill="var(--link)" fillOpacity={0.1} strokeWidth={1} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
};

const MiniModelDrift = () => {
  const data = [
    { x: 1, y: 0.1 },
    { x: 2, y: 0.15 },
    { x: 3, y: 0.22 },
    { x: 4, y: 0.28 },
    { x: 5, y: 0.31 },
  ];
  return (
    <div style={{ width: "100%", height: "100%", opacity: 0.8, pointerEvents: "none" }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 2, right: 2, left: 2, bottom: 2 }}>
          <Area type="monotone" dataKey="y" stroke="#7209b7" fill="#7209b7" fillOpacity={0.15} strokeWidth={1} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
};

export const renderMiniatureChart = (widgetId: string) => {
  switch (widgetId) {
    case "system-status":
      return <MiniSystemStatus />;
    case "severity-trend":
      return <MiniTransactionTrend />;
    case "response-code-change":
      return <MiniInterfaceAnomalies />;
    case "major-risk-events":
      return <MiniRiskDistribution />;
    case "recent-alerts":
      return <MiniRecentAlerts />;
    case "channel-risk-rank":
      return <MiniChannelRisk />;
    case "alert-delivery":
      return <MiniAlertDelivery />;
    case "collector-status":
      return <MiniCollectorStatus />;
    case "recent-anomaly-logs":
      return <MiniRecentAnomalyLogs />;
    case "impact-scope":
      return <MiniImpactScope />;
    case "latency-change":
      return <MiniLatencyChange />;
    case "failure-rate-change":
      return <MiniFailureRateChange />;
    case "model-status":
      return <MiniModelStatus />;
    case "model-drift":
      return <MiniModelDrift />;
    default:
      return null;
  }
};
