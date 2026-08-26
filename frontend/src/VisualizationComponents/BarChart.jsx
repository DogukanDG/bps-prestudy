import {
  ResponsiveContainer,
  BarChart as ReBarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ErrorBar,
} from "recharts";

/**
 * BarChart
 * --------
 * Renders horizontal grouped bar charts with confidence intervals.
 * - Bar *groups* (categories) are `cases`
 * - Bar *series* are parameter groups/names, ranked per effect.
 */
const BarChart = ({ data, isSobol, groupField, isGroups }) => {
  // Helper to clamp negative values to 0 for display
  const clamp = (val) => (typeof val === 'number' && val < 0 ? 0 : val);

  if (!data || data.length === 0) {
    return (
      <div className="mt-4 rounded-xl border border-gray-200 bg-gray-50 p-4 text-sm text-gray-700">
        No data available for bar chart.
      </div>
    );
  }

  const casesList = [...new Set(data.map((d) => d.cases))].sort(
    (a, b) => b - a
  );
  const groupNames = [...new Set(data.map((d) => d[groupField]))];

  const pivotAndSortByCases = (effectKey, confKey, singleCase = null) => {
    const grouped = new Map();

    data.forEach((d) => {
      const c = d.cases;
      // If singleCase is specified, only include that case
      if (singleCase !== null && c !== singleCase) return;
      
      const g = d[groupField];

      if (!grouped.has(c)) grouped.set(c, { cases: c });

      const row = grouped.get(c);
      const baseKey = `${g}_${effectKey}`;

      row[baseKey] = clamp(d[effectKey]);
      row[`${baseKey}_conf`] = d[confKey];
    });

    const rows = Array.from(grouped.values());

    // ⬇️ always sort cases descending on the Y-axis (e.g. 200, 100, 50)
    return rows.sort((a, b) => b.cases - a.cases);
  };

  const getColor = (index, total) => {
    const hue = ((index / total) * 360) | 0;
    return `hsl(${hue}, 60%, 55%)`;
  };

  const HorizontalEffectChart = ({
    title,
    description,
    effectKey,
    rows,
    xLabel,
    singleCase = null,
  }) => {
    const numCases = rows.length;
    const numGroups = groupNames.length;
    
    // Each case needs enough height for all groups to stack vertically
    // barSize is 22px per bar, plus some spacing
    const BAR_SIZE = 10;
    const SPACING_BETWEEN_BARS = 8;
    const MARGIN_TOP_BOTTOM = 10; // for labels and axes
    
    const heightPerCase = numGroups * (BAR_SIZE + SPACING_BETWEEN_BARS);
    const chartHeight = Math.max(400, (numCases * heightPerCase) + MARGIN_TOP_BOTTOM);

    // Determine which case to use for sorting: if singleCase provided, use that; otherwise use max
    const sortingCase = singleCase !== null ? singleCase : Math.max(...casesList);

    // Sort groups by effect at the sorting case value (ranking)
    const sortedGroupNames = [...groupNames].sort((a, b) => {
      const da = data.find(
        (d) => d.cases === sortingCase && d[groupField] === a
      );
      const db = data.find(
        (d) => d.cases === sortingCase && d[groupField] === b
      );
      const va = da ? clamp(da[effectKey]) : -Infinity;
      const vb = db ? clamp(db[effectKey]) : -Infinity;
      return vb - va; // descending
    });

    // Custom tooltip: sort entries by value (per hovered case)
    const CustomTooltip = ({ active, payload, label }) => {
      if (!active || !payload || payload.length === 0) return null;

      const sortedPayload = [...payload]
        .filter((p) => typeof p.value === "number")
        .sort((a, b) => b.value - a.value);

      return (
        <div className="rounded-lg border bg-white px-3 py-2 text-xs shadow-md max-w-sm">
          <p className="font-semibold mb-1">Cases: {label}</p>
          {sortedPayload.map((entry, idx) => (
            <div key={entry.dataKey} className="flex items-center gap-2 mb-0.5">
              <span
                className="inline-block h-2 w-2 rounded-sm flex-shrink-0"
                style={{ backgroundColor: entry.color }}
              />
              <span className="flex-1 truncate min-w-0" title={entry.name}>
                {entry.name}
              </span>
              <span className="text-[10px] text-gray-500 flex-shrink-0">
                #{idx + 1}
              </span>
              <span className="flex-shrink-0 font-mono">
                {entry.value.toFixed(3)}
              </span>
            </div>
          ))}
        </div>
      );
    };

    return (
      <div className="mt-4 space-y-2">
        <h3 className="text-sm font-semibold text-gray-900">{title}</h3>
        <p className="text-xs text-gray-600">{description}</p>

        <div
          className="w-full flex gap-6 overflow-auto border rounded-lg bg-gray-50 p-4"
          style={{ maxHeight: '80vh' }}
        >
          {/* Chart area */}
          <div className="flex-1" style={{ height: `${chartHeight}px`, minHeight: `${chartHeight}px` }}>
            <ResponsiveContainer width="100%" height={chartHeight}>
              <ReBarChart
                data={rows}
                layout="vertical"
                // bars start close to left edge
                margin={{ top: 40, right: 10, left: 0, bottom: 40 }}
              >
                <CartesianGrid strokeDasharray="3 3" />

                <XAxis
                  type="number"
                  label={{
                    value: xLabel,
                    position: "insideBottom",
                    offset: -5,
                    style: { textAnchor: "middle", fontSize: 11 },
                  }}
                />

                <YAxis
                  type="category"
                  dataKey="cases"
                  tick={{ fontSize: 14 }}
                  width={55}
                  label={{
                    value: "# of Cases",
                    angle: -90,
                    position: "insideLeft",
                    style: { textAnchor: "middle", fontSize: 11 },
                  }}
                />

                <Tooltip content={<CustomTooltip />} />

                {sortedGroupNames.map((g, idx) => {
                  const baseKey = `${g}_${effectKey}`;
                  return (
                    <Bar
                      key={baseKey}
                      dataKey={baseKey}
                      name={g}
                      fill={getColor(idx, sortedGroupNames.length)}
                      barSize={22}
                    >
                      <ErrorBar
                        dataKey={`${baseKey}_conf`}
                        width={5}
                        strokeWidth={2}
                        direction="x"
                      />
                    </Bar>
                  );
                })}
              </ReBarChart>
            </ResponsiveContainer>
          </div>

          {/* Custom legend on the right, ordered by rank at max cases */}
          <div className="w-60 flex flex-col text-xs space-y-1 flex-shrink-0" style={{ paddingTop: '40px' }}>
            <p className="font-semibold mb-1">Groups (by final rank)</p>
            {sortedGroupNames.map((g, idx) => (
              <div key={g} className="flex items-center gap-2">
                <span
                  className="inline-block h-2 w-2 rounded-sm flex-shrink-0"
                  style={{
                    backgroundColor: getColor(idx, sortedGroupNames.length),
                  }}
                />
                <span className="truncate" title={g}>
                  {g}
                </span>
                <span className="ml-auto text-[10px] text-gray-500 flex-shrink-0">
                  #{idx + 1}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  };

  if (isSobol) {
    if (!isGroups) {
      // Render separate charts for each case
      return (
        <div className="mt-6 space-y-10">
          {casesList.map((caseNum) => (
            <div key={caseNum} className="space-y-6">
              <HorizontalEffectChart
                title={`Sobol Total Order Effects Bar Chart (Cases: ${caseNum})`}
                description="Each bar represents a parameter ranked by ST value."
                effectKey="ST"
                rows={pivotAndSortByCases("ST", "ST_conf", caseNum)}
                xLabel="ST (magnitude of effect)"
                singleCase={caseNum}
              />

              <HorizontalEffectChart
                title={`Sobol First Order Effect Bar Chart (Cases: ${caseNum})`}
                description="Each bar represents a parameter ranked by S1 value."
                effectKey="S1"
                rows={pivotAndSortByCases("S1", "S1_conf", caseNum)}
                xLabel="S1 (magnitude of effect)"
                singleCase={caseNum}
              />
            </div>
          ))}
        </div>
      );
    }

    return (
      <div className="mt-6 space-y-10">
        <HorizontalEffectChart
          title="Sobol Total Order Effects Bar Chart"
          description="Bars grouped by cases; each color is a parameter group ranked by ST at the highest cases value."
          effectKey="ST"
          rows={pivotAndSortByCases("ST", "ST_conf")}
          xLabel="ST (magnitude of effect)"
        />

        <HorizontalEffectChart
          title="Sobol First Order Effect Bar Chart"
          description="Bars grouped by cases; each color is a parameter group ranked by S1 at the highest cases value."
          effectKey="S1"
          rows={pivotAndSortByCases("S1", "S1_conf")}
          xLabel="S1 (magnitude of effect)"
        />
      </div>
    );
  }

  if (!isGroups) {
    // Render separate charts for each case
    return (
      <div className="mt-6 space-y-10">
        {casesList.map((caseNum) => (
          <HorizontalEffectChart
            key={caseNum}
            title={`Morris First Order Effects Bar Chart (Cases: ${caseNum})`}
            description="Each bar represents a parameter ranked by μ* value."
            effectKey="mu_star"
            rows={pivotAndSortByCases("mu_star", "mu_star_conf", caseNum)}
            xLabel="μ* (magnitude of effect)"
            singleCase={caseNum}
          />
        ))}
      </div>
    );
  }

  return (
    <div className="mt-6 space-y-10">
      <HorizontalEffectChart
        title="Morris First Order Effecs Bar Chart"
        description="Bars grouped by cases; each color is a parameter group ranked by μ* at the highest cases value."
        effectKey="mu_star"
        rows={pivotAndSortByCases("mu_star", "mu_star_conf")}
        xLabel="μ* (magnitude of effect)"
      />
    </div>
  );
};

export default BarChart;
