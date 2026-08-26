import BumpChart from "./VisualizationComponents/BumpChart";
import BarChart from "./VisualizationComponents/BarChart";
import Heatmap from "./VisualizationComponents/Heatmap";

const Visualization = ({
  is_sobol,
  is_groups,
  calc_second_order,
  sobol_results_first_and_total_order,
  sobol_results_second_order,
  morris_results_first_order,
}) => {
  // Pick the main data source based on analysis type
  const dataForChart = is_sobol
    ? sobol_results_first_and_total_order
    : morris_results_first_order;

  // If no data at all, show a single message and bail out
  if (!dataForChart || dataForChart.length === 0) {
    return (
      <div className="mt-4 rounded-xl border border-gray-200 bg-gray-50 p-4 text-sm text-gray-700">
        {is_sobol
          ? "No Sobol results available."
          : "No Morris results available."}
      </div>
    );
  }

  // Field names differ between Sobol (group) and Morris (name)
  const groupField = is_sobol ? "group" : "name";
  const valueField = is_sobol ? "ST" : "mu_star";

  // Bump chart labels
  const bumpTitle = is_sobol
    ? "Sobol Total Order Effects Bump Chart"
    : "Morris First Order Effecs Bump Chart";

  const effectLabel = is_sobol
    ? "total order effects (ST)"
    : "first order effects (mu_star)";

  return (
    <>
      {/* Info banner about negative value handling */}
      <div className="mt-4 rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm text-blue-900">
        <p className="font-semibold mb-1">Display Note</p>
        <p>
          Negative sensitivity values (ST, S1, S2, or μ*) are displayed as 0 in all visualizations for clarity, while preserving the original values in the data files.
        </p>
      </div>

      {/* Non-group mode info banner (bump chart not available) */}
      {!is_groups && (
        <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <p className="font-semibold mb-1">
            {is_sobol
              ? "Sobol analysis (non-group mode)"
              : "Morris analysis (non-group mode)"}
          </p>
          <p>
            The bump chart is only available for{" "}
            <code className="font-mono">group</code>-based effects. Below you
            can still inspect the parameter ranking using the bar chart.
          </p>
        </div>
      )}

      {/* Bump chart only when group-based aggregation is available */}
      {is_groups && (
        <BumpChart
          data={dataForChart}
          groupField={groupField}
          valueField={valueField}
          isSobol={is_sobol}
          title={bumpTitle}
          effectLabel={effectLabel}
        />
      )}

      {/* Bar chart is ALWAYS shown when data is available */}
      <BarChart data={dataForChart} groupField={groupField} isSobol={is_sobol} isGroups={is_groups} />

      {/* Heatmap: only Sobol + second-order enabled */}
      {is_sobol && calc_second_order && is_groups && (
        <Heatmap data={sobol_results_second_order}/>
      )}
    </>
  );
};

export default Visualization;
