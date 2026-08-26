import { useState } from "react";
import Visualization from "./Visualization";
const API_BASE = import.meta.env.VITE_API_BASE_URL;

/**
 * SensitivityAnalysis
 * -------------------
 * Frontend panel to:
 *  - select which simulation run folder to use (or let the backend pick the latest),
 *  - choose a target process KPI and statistic (min/max/avg/total),
 *  - optionally define an output folder name for SA results,
 *  - send a JSON request to the backend `/sensitivity-analysis` endpoint,
 *  - and show a summary of what was done plus the raw JSON response.
 *  - builds some visualizations based on the analysis configuration
 */
export default function SensitivityAnalysis({ setIsRunning }) {
  // User-configurable inputs
  const [runFolderName, setRunFolderName] = useState(""); // simulation results folder (optional)
  const [outputFolderName, setOutputFolderName] = useState(""); // SA output folder (optional)
  const [kpi, setKpi] = useState("cycle_time"); // process KPI
  const [statType, setStatType] = useState("avg"); // statistic type

  // Request / UI state
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  // Trigger backend sensitivity analysis
  const handleRun = async () => {
    setError(null);
    setResult(null);

    const payload = {
      // Optional, backend uses latest Simulation Results if empty
      run_folder: runFolderName.trim(),
      // Optional, backend can use default output folder if empty
      output_folder: outputFolderName.trim(),
      kpi,
      stat_type: statType,
    };

    setLoading(true);
    setIsRunning(true);
    try {
      const resp = await fetch(`${API_BASE}/sensitivity-analysis`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      let data = null;
      try {
        data = await resp.json();
      } catch {
        data = null;
      }

      if (!resp.ok) {
        let msg = "Sensitivity analysis failed.";
        if (data?.error_type && data?.error) {
          msg = `${data.error_type}: ${data.error}`;
        } else if (data?.error) {
          msg = data.error;
        }
        if (data?.trace) {
          console.error("Traceback:\n", data.trace);
        }
        throw new Error(msg);
      }

      // success
      setResult(data);
    } catch (e) {
      setError(e.message || "Something went wrong while running analysis.");
    } finally {
      setLoading(false);
      setIsRunning(false);
    }
  };

  // Helpers for the success message
  const renderSuccessHeader = () => {
    if (!result) return null;

    if (result.is_sobol) {
      return (
        <>
          <p className="font-semibold">
            Sobol sensitivity analysis completed.
          </p>
          <p className="mt-1">
            {result.calc_second_order
              ? "Second order effects calculation is on."
              : "Second order effect calculation is off."}
          </p>
        </>
      );
    }

    return (
      <p className="font-semibold">
        Morris sensitivity analysis completed.
      </p>
    );
  };

  return (
    <div className="space-y-6 text-sm text-gray-700">
      {/* Simulation Results folder name (optional) */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Simulation Results Folder Name
        </label>
        <input
          type="text"
          value={runFolderName}
          onChange={(e) => setRunFolderName(e.target.value)}
          placeholder='Optional. e.g. "Simulation Results 20251123_170203"'
          className="w-full rounded-xl border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
        />
        <p className="mt-1 text-xs text-gray-500">
          Enter only the folder name inside{" "}
          <code className="font-mono">output/</code> — for example:{" "}
          <code className="font-mono">Simulation Results 20251123_170203</code>.
          <br />
          If left empty, the backend automatically selects the{" "}
          <strong>most recent</strong> simulation results folder to run sensitivity analysis.
        </p>
      </div>

      {/* Sensitivity Analysis Outputs folder name (optional) */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Sensitivity Analysis Results Folder Name
        </label>
        <input
          type="text"
          value={outputFolderName}
          onChange={(e) => setOutputFolderName(e.target.value)}
          placeholder='Optional. e.g. "Sensitivity Analysis 20251123_171500"'
          className="w-full rounded-xl border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
        />
        <p className="mt-1 text-xs text-gray-500">
          Leave empty to let the backend choose a default folder name.
        </p>
      </div>

      {/* KPI selection */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Process KPI
        </label>
        <select
          value={kpi}
          onChange={(e) => setKpi(e.target.value)}
          className="w-full rounded-xl border border-gray-300 bg-white px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
        >
          <option value="cycle_time">Cycle Time</option>
          <option value="waiting_time">Waiting Time</option>
          <option value="processing_time">Processing Time</option>
          <option value="idle_time">Idle Time</option>
          <option value="idle_cycle_time">Idle-Cycle Time</option>
          <option value="idle_processing_time">Idle-Processing Time</option>
        </select>
      </div>

      {/* Stat type */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Statistic Type
        </label>
        <select
          value={statType}
          onChange={(e) => setStatType(e.target.value)}
          className="w-full rounded-xl border border-gray-300 bg-white px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
        >
          <option value="min">Min</option>
          <option value="max">Max</option>
          <option value="avg">Avg</option>
          <option value="total">Total</option>
        </select>
      </div>

      {/* Run button */}
      <button
        onClick={handleRun}
        disabled={loading}
        className="w-full inline-flex items-center justify-center gap-2 bg-indigo-600 text-white py-3 rounded-xl font-medium hover:bg-indigo-700 disabled:bg-gray-400 transition"
      >
        {loading ? (
          <>
            <span className="w-5 h-5 border-2 border-white/70 border-t-transparent rounded-full animate-spin" />
            Running Sensitivity Analysis…
          </>
        ) : (
          "Run Sensitivity Analysis"
        )}
      </button>

      {/* Error message */}
      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          <p className="font-semibold mb-1">Error</p>
          <p>{error}</p>
        </div>
      )}

      {result && (
        <>
          {/* Summary + raw JSON (green-ish) */}
          <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900 space-y-3">
            {renderSuccessHeader()}

            <p className="mt-2">
              KPI is {result.stat_type} {result.kpi}.
            </p>

            <p>
              The results are saved to{" "}
              <code className="font-mono">{result.output_folder}</code> folder.
            </p>

            <details className="mt-3">
              <summary className="cursor-pointer text-xs underline">
                Show raw JSON response
              </summary>
              <pre className="mt-2 text-xs whitespace-pre-wrap break-all">
                {JSON.stringify(result, null, 2)}
              </pre>
            </details>
          </div>

          {/* Visualization card (separate, white background) */}
          <div className="rounded-xl border border-gray-200 bg-white p-4 text-sm text-gray-900">
            <Visualization
              is_sobol={result.is_sobol}
              is_groups={result.is_groups}
              calc_second_order={result.calc_second_order}
              sobol_results_first_and_total_order={
                result.sobol_results_first_and_total_order
              }
              sobol_results_second_order={result.sobol_results_second_order}
              morris_results_first_order={result.morris_results_first_order}
            />
          </div>
        </>
      )}
    </div>
  );
}
