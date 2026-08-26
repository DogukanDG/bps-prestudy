import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";

/**
 * BumpChart
 * ---------
 * Renders a bump chart showing how parameter groups rank across different
 * cases. Supports both Sobol (ST) and Morris (μ*) effects using the fields
 * provided via props. Renders only when between group analysis is on.
 */
const BumpChart = ({
  data,
  groupField,
  valueField,
  isSobol,
  title,
  effectLabel,
}) => {
  // Helper to clamp negative values to 0 for display
  const clamp = (val) => (typeof val === 'number' && val < 0 ? 0 : val);

  if (!data || data.length === 0) {
    return (
      <div className="mt-4 rounded-xl border border-gray-200 bg-gray-50 p-4 text-sm text-gray-700">
        No data available for bump chart.
      </div>
    );
  }

  // --------------------------------------------
  // Build groups & cases
  // --------------------------------------------

  const groups = [...new Set(data.map((d) => d[groupField]))];

  const casesList = [...new Set(data.map((d) => d.cases))].sort(
    (a, b) => a - b
  );

  const nGroups = groups.length;

  // Lookup for ST / mu_star values by (cases, group)
  const effectLookup = {};
  data.forEach((d) => {
    const g = d[groupField];
    effectLookup[`${d.cases}__${g}`] = clamp(d[valueField]);
  });

  // --------------------------------------------
  // Build chartData with inverted ranks
  // --------------------------------------------

  const chartData = casesList.map((casesVal) => {
    const subset = data.filter((d) => d.cases === casesVal);

    // Sort in descending order of effect (with clamped values)
    const sorted = [...subset].sort((a, b) => clamp(b[valueField]) - clamp(a[valueField]));

    const ranks = {};
    sorted.forEach((d, idx) => {
      const g = d[groupField];
      ranks[g] = idx + 1; // rank 1 = highest effect
    });

    const row = { cases: casesVal };
    groups.forEach((g) => {
      const rank = ranks[g];
      if (rank != null) {
        // store inverted value so rank 1 is at the top visually
        row[g] = nGroups + 1 - rank;
      } else {
        row[g] = null;
      }
    });

    return row;
  });

  // Y-axis ticks: one per group
  const yTicks = Array.from({ length: nGroups }, (_, i) => i + 1);

  // Dynamic color generator: spreads hues around the color wheel
  const getColor = (index, total) => {
    const safeTotal = Math.max(total, 1);
    const hue = (index / safeTotal) * 360; // 0–360°
    return `hsl(${hue}, 65%, 50%)`;
  };

  // --------------------------------------------
  // Order groups for legend by final rank
  // --------------------------------------------

  const finalRankByGroup = {};
  chartData.forEach((row, idx) => {
    groups.forEach((g) => {
      if (row[g] != null) {
        const yStored = row[g];
        const rank = nGroups + 1 - yStored;
        finalRankByGroup[g] = { index: idx, rank };
      }
    });
  });

  const orderedGroups = [...groups].sort((a, b) => {
    const fa = finalRankByGroup[a];
    const fb = finalRankByGroup[b];

    const rankA = fa ? fa.rank : Number.POSITIVE_INFINITY;
    const rankB = fb ? fb.rank : Number.POSITIVE_INFINITY;

    if (rankA === rankB) return 0;
    return rankA < rankB ? -1 : 1;
  });

  // --------------------------------------------
  // Custom Tooltip (sorted by rank for that case)
  // --------------------------------------------

  const CustomTooltip = ({ active, payload, label }) => {
    if (!active || !payload || payload.length === 0) return null;

    // payload[i].value is the *inverted* y (nGroups + 1 - rank)
    const rows = payload
      .filter((entry) => entry && typeof entry.value === "number")
      .map((entry) => {
        const groupName = entry.dataKey;
        const rank = nGroups + 1 - entry.value;
        const effectVal = effectLookup[`${label}__${groupName}`];
        return {
          entry,
          groupName,
          rank,
          effectVal,
        };
      })
      .sort((a, b) => a.rank - b.rank); // rank 1 (highest effect) first

    return (
      <div className="rounded-lg border bg-white px-3 py-2 text-xs shadow-md">
        <p className="font-semibold mb-1">Cases: {label}</p>

        {rows.map(({ entry, groupName, rank, effectVal }) => (
          <div key={groupName} className="flex items-center gap-2">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ backgroundColor: entry.color }}
            />
            <span className="truncate max-w-[120px]" title={groupName}>
              {groupName}
            </span>
            <span className="ml-auto text-[10px] text-gray-500">
              #{rank}
            </span>
            <span className="ml-2">
              {isSobol ? "ST" : "μ*"}:{" "}
              {typeof effectVal === "number"
                ? effectVal.toFixed(3)
                : "n/a"}
            </span>
          </div>
        ))}
      </div>
    );
  };

  // --------------------------------------------
  // Chart height depending on number of groups
  // --------------------------------------------

  const legendRowHeight = 22; // px per legend row
  const legendPadding = 40; // top/bottom padding
  const legendHeight = nGroups * legendRowHeight + legendPadding;

  const perGroup = 28; // px per group
  const chartBaseHeight = 200; // min for readability
  const bumpChartHeight = Math.max(chartBaseHeight, nGroups * perGroup);

  const chartHeight = Math.max(legendHeight, bumpChartHeight, 260);

  // --------------------------------------------
  // Render
  // --------------------------------------------

  return (
    <div className="mt-4 space-y-3">
      <h3 className="text-sm font-semibold text-gray-900">{title}</h3>

      <p className="text-xs text-gray-600">
        Each line represents a parameter group ranked by {effectLabel} for each{" "}
        <code className="font-mono">cases</code> value.
      </p>

      <div
        className="w-full flex gap-4"
        style={{ height: `${chartHeight}px` }}
      >
        {/* Chart area */}
        <div className="flex-1">
          <ResponsiveContainer>
            <LineChart
              data={chartData}
              margin={{ top: 20, right: 20, left: 10, bottom: 28 }}
            >
              <CartesianGrid strokeDasharray="3 3" />

              <XAxis
                dataKey="cases"
                label={{
                  value: "# of Cases",
                  position: "insideBottom",
                  offset: -6,
                  style: { fontSize: 11 },
                }}
                tick={{ fontSize: 11 }}
              />

              <YAxis
                domain={[1, nGroups]}
                ticks={yTicks}
                tickFormatter={(val) => nGroups + 1 - val}
                tick={{
                  fontSize: 11,
                  textAnchor: "end",
                  dx: -4,
                }}
                label={{
                  value: "Rank (1 = highest effect)",
                  angle: -90,
                  position: "insideLeft",
                  style: { textAnchor: "middle", fontSize: 11 },
                }}
              />

              <Tooltip content={<CustomTooltip />} />

              {orderedGroups.map((g, idx) => (
                <Line
                  key={g}
                  type="monotone"
                  dataKey={g}
                  stroke={getColor(idx, nGroups)}
                  strokeWidth={2}
                  dot={{ r: 3 }}
                  activeDot={{ r: 5 }}
                  connectNulls
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>

        {/* Legend */}
        <div className="w-40 flex flex-col justify-center text-xs space-y-1">
          <p className="font-semibold mb-1">Groups (by final rank)</p>
          {orderedGroups.map((g, idx) => {
            const info = finalRankByGroup[g];
            const finalRank =
              info && typeof info.rank === "number" ? info.rank : "–";

            return (
              <div key={g} className="flex items-center gap-2">
                <span
                  className="inline-block h-2 w-2 rounded-full"
                  style={{ backgroundColor: getColor(idx, nGroups) }}
                />
                <span className="truncate" title={g}>
                  {g}
                </span>
                <span className="ml-auto text-[10px] text-gray-500">
                  #{finalRank}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

export default BumpChart;
