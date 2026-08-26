import React from "react";

/**
 * Heatmap
 * -------
 * Renders one upper-triangular S2 heatmap per `cases` value (no diagonal).
 * Input rows are expected like: { cases, group_i, group_j, S2, S2_conf }.
 */
const Heatmap = ({ data }) => {
  if (!data || data.length === 0) {
    return (
      <div className="mt-4 rounded-xl border border-gray-200 bg-gray-50 p-4 text-sm text-gray-700">
        No data available for heatmap.
      </div>
    );
  }

  // --- helpers ---
  const clamp = (val) => (typeof val === 'number' && val < 0 ? 0 : val);
  const uniq = (arr) => Array.from(new Set(arr));

  const fmt = (v) =>
    typeof v === "number" && Number.isFinite(v) ? v.toFixed(3) : "n/a";

  // Symmetric key so (i,j) == (j,i) for lookup
  const pairKey = (a, b) => (a < b ? `${a}__${b}` : `${b}__${a}`);

  // Color scale based on S2/S2_conf ratio: higher ratio (more significant) -> red, lower ratio -> white
  const makeColor = (ratio, maxRatio) => {
    if (ratio == null || !Number.isFinite(ratio) || maxRatio <= 0 || ratio <= 0) return "hsl(0, 0%, 98%)";
    const t = Math.min(1, ratio / maxRatio); // 0..1
    const light = 95 - t * 45; // 95 (white) to 50 (darker red)
    const hue = 5; // red
    return `hsl(${hue}, 70%, ${light}%)`;
  };

  // --- group by cases ---
  const casesList = uniq(data.map((d) => d.cases)).sort((a, b) => b - a); // decreasing
  const allGroups = uniq(
    data.flatMap((d) => [d.group_i, d.group_j]).filter(Boolean)
  ).sort((a, b) => a.localeCompare(b));

  // Build per-case lookup map: key -> row
  const byCasesLookup = {};
  casesList.forEach((c) => {
    const m = new Map();
    data
      .filter((d) => d.cases === c)
      .forEach((d) => {
        m.set(pairKey(d.group_i, d.group_j), d);
      });
    byCasesLookup[c] = m;
  });

  // Custom tooltip (simple absolute-positioned div)
  const [tip, setTip] = React.useState(null);

  const onCellEnter = (evt, payload) => {
    const rect = evt.currentTarget.getBoundingClientRect();
    setTip({
      x: rect.right + 12,
      y: rect.top,
      ...payload,
    });
  };
  const onCellLeave = () => setTip(null);

  return (
    <div className="mt-8 space-y-8 relative">
      <h3 className="text-sm font-semibold text-gray-900">
        Sobol Second Order Effects Heatmaps (S2)
      </h3>
      <p className="text-xs text-gray-600">
        Upper-triangular matrix per <code className="font-mono">cases</code>.
        Cell value = S2. Cell color indicates significance (S2/S2_conf ratio, darker red = higher ratio).
      </p>

      {/* tooltip */}
      {tip && (
        <div
          className="fixed z-50 rounded-lg border bg-white px-3 py-2 text-xs shadow-md"
          style={{ left: tip.x, top: tip.y, maxWidth: 280 }}
        >
          <div className="font-semibold mb-1">Cases: {tip.cases}</div>
          <div className="flex justify-between gap-4">
            <span className="text-gray-600">Pair</span>
            <span className="font-mono">{tip.i} × {tip.j}</span>
          </div>
          <div className="flex justify-between gap-4">
            <span className="text-gray-600">S2</span>
            <span className="font-mono">{fmt(tip.s2)}</span>
          </div>
          <div className="flex justify-between gap-4">
            <span className="text-gray-600">S2_conf</span>
            <span className="font-mono">{fmt(tip.s2conf)}</span>
          </div>
        </div>
      )}

      {casesList.map((casesVal) => {
        const lookup = byCasesLookup[casesVal];
        
        // Calculate S2/S2_conf ratios for this specific case (using clamped values)
        const ratios = data
          .filter((d) => d.cases === casesVal)
          .map((d) => {
            const s2 = clamp(d.S2);
            const conf = clamp(d.S2_conf);
            if (!Number.isFinite(s2) || !Number.isFinite(conf) || conf === 0) return null;
            // Include ratio even if s2 is 0 (from clamping)
            return s2 / conf;
          })
          .filter((r) => r !== null && Number.isFinite(r));
        
        const maxRatio = ratios.length > 0 ? Math.max(...ratios) : 0;
        const minRatio = ratios.length > 0 ? Math.min(...ratios) : 0;

        return (
          <div
            key={casesVal}
            className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm"
          >
            <div className="mb-3 flex items-baseline justify-between">
              <h4 className="text-sm font-semibold text-gray-900">
                Cases: {casesVal}
              </h4>
              <div className="text-[11px] text-gray-500">
                S2/S2_conf ratio — min: {minRatio.toFixed(3)}, max: {maxRatio.toFixed(3)}
              </div>
            </div>

            {/* heatmap grid */}
            <div className="overflow-auto">
              <div
                className="grid"
                style={{
                  gridTemplateColumns: `180px repeat(${allGroups.length}, minmax(42px, 1fr))`,
                  gap: "6px",
                  minWidth: 180 + allGroups.length * 48,
                }}
              >
                {/* top-left empty */}
                <div />

                {/* column headers */}
                {allGroups.map((g) => (
                  <div
                    key={`col_${casesVal}_${g}`}
                    className="text-[10px] text-gray-600 font-medium truncate text-center"
                    title={g}
                  >
                    {g}
                  </div>
                ))}

                {/* rows */}
                {allGroups.map((rowG, i) => (
                  <React.Fragment key={`row_${casesVal}_${rowG}`}>
                    {/* row header */}
                    <div
                      className="text-[10px] text-gray-600 font-medium truncate pr-2"
                      title={rowG}
                    >
                      {rowG}
                    </div>

                    {/* cells */}
                    {allGroups.map((colG, j) => {
                      // upper triangular only, and hide diagonal:
                      // show cell when j > i
                      if (j <= i) {
                        return (
                          <div
                            key={`cell_${casesVal}_${rowG}_${colG}`}
                            className="h-10 rounded-md"
                          />
                        );
                      }

                      const rec = lookup.get(pairKey(rowG, colG));
                      const s2 = clamp(rec?.S2);
                      const s2conf = clamp(rec?.S2_conf);
                      
                      // Calculate ratio for coloring (using clamped values)
                      const ratio = (rec && Number.isFinite(s2) && Number.isFinite(s2conf) && s2conf !== 0 && s2 > 0)
                        ? s2 / s2conf
                        : 0;

                      const bg = makeColor(ratio, maxRatio);

                      return (
                        <div
                          key={`cell_${casesVal}_${rowG}_${colG}`}
                          className="h-10 rounded-md border border-gray-200 flex items-center justify-center text-[10px] font-mono cursor-default"
                          style={{ backgroundColor: bg }}
                          onMouseEnter={(e) =>
                            onCellEnter(e, {
                              cases: casesVal,
                              i: rowG,
                              j: colG,
                              s2,
                              s2conf,
                            })
                          }
                          onMouseLeave={onCellLeave}
                          title={rec ? `S2=${fmt(s2)} | conf=${fmt(s2conf)}` : "no data"}
                        >
                          {rec ? fmt(s2) : ""}
                        </div>
                      );
                    })}
                  </React.Fragment>
                ))}
              </div>
            </div>

            {/* little legend */}
            <div className="mt-4 flex items-center gap-3 text-[11px] text-gray-600">
              <span className="font-medium">Color Legend (by S2/S2_conf ratio)</span>
              <div className="flex items-center gap-2">
                <span>lower significance</span>
                <span
                  className="inline-block h-3 w-10 rounded border border-gray-200"
                  style={{ background: "hsl(0, 0%, 98%)" }}
                />
                <span
                  className="inline-block h-3 w-10 rounded"
                  style={{ background: "hsl(5, 70%, 75%)" }}
                />
                <span
                  className="inline-block h-3 w-10 rounded"
                  style={{ background: "hsl(5, 70%, 50%)" }}
                />
                <span>higher significance</span>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
};

export default Heatmap;
