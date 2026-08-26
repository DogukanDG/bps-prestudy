import { useState, useMemo } from "react";

const API_BASE = import.meta.env.VITE_API_BASE_URL;

/**
 * SamplingAndSimulation
 * ----------------------
 * Frontend panel to:
 *  - upload a BPMN model + JSON config,
 *  - choose a sensitivity analysis method (Sobol / Morris),
 *  - configure SA parameters (samples / trajectories / levels),
 *  - define analysis scope (between vs within groups),
 *  - select which parameter dimensions to include,
 *  - send everything to the backend `/simulate` endpoint,
 *  - and display the plain-text success message returned by the backend.
 *
 *   The backend returns plain text:
 *   return result, 200
 */
export default function SamplingAndSimulation({ setIsRunning }) {
  const [bpmnFile, setBpmnFile] = useState(null);
  const [jsonFile, setJsonFile] = useState(null);
  const [resultMessage, setResultMessage] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Sensitivity method: "Sobol" (global) or "Morris" (local)
  const [analysisType, setAnalysisType] = useState("Sobol"); // "Sobol" | "Morris"

  // Group scope: true = between groups, false = within a single group
  const [is_groups, setIsGroups] = useState(true);

  // Dimension flags: which parameter sets are included in SA
  const [flags, setFlags] = useState({
    is_gateway: true,
    is_arrival_distribution: true,
    is_arrival_calendar: true,
    is_tasks_resources: true,
    is_resource_calendars: true,
    is_resource_numbers: true,
  });

  // --- Sensitivity Analysis Parameters ---

  // Sobol-specific
  const [nOfSamples, setNOfSamples] = useState("256");
  const [calcSecondOrder, setCalcSecondOrder] = useState(false);

  // Morris-specific
  const [nOfTrajectories, setNOfTrajectories] = useState("30");
  const [nOfLevels, setNOfLevels] = useState("6");

  // Common parameters (used by both Sobol and Morris)
  const [caseCounts, setCaseCounts] = useState("100");
  const [nReplicationRuns, setNReplicationRuns] = useState("1");
  const [seed, setSeed] = useState("");
  const [resultsFolder, setResultsFolder] = useState("");

  // File pickers
  const pickBPMN = (e) => setBpmnFile(e.target.files?.[0] ?? null);
  const pickJSON = (e) => setJsonFile(e.target.files?.[0] ?? null);

  // Count how many dimensions are selected
  const selectedCount = useMemo(
    () => Object.values(flags).filter(Boolean).length,
    [flags]
  );

  // Scope rule:
  // - Between groups: at least 2 selected
  // - Within group: exactly 1 selected
  const selectionValid = is_groups ? selectedCount >= 2 : selectedCount === 1;

  // Utility: set all flags to one value (true/false)
  const setAllFlags = (prev, value) =>
    Object.fromEntries(Object.keys(prev).map((k) => [k, value]));

  // Toggle one dimension tile, respecting scope logic
  const toggle = (key) => {
    setFlags((prev) => {
      if (!is_groups) {
        // Within group → only one dimension can be active
        const next = setAllFlags(prev, false);
        next[key] = true;
        return next;
      }
      // Between groups → just flip this one
      return { ...prev, [key]: !prev[key] };
    });
  };

  // Switch between "Between groups" and "Within group"
  const handleScopeChange = (value) => {
    const nextIsGroups = value === "groups";
    setIsGroups(nextIsGroups);

    setFlags((prev) => {
      if (nextIsGroups) {
        // When switching to between-groups, turn everything on
        return setAllFlags(prev, true);
      } else {
        // When switching to within-group, keep exactly one active
        const firstTrue =
          Object.entries(prev).find(([_, v]) => v)?.[0] ?? "is_gateway";
        const next = setAllFlags(prev, false);
        next[firstTrue] = true;
        return next;
      }
    });
  };

  // Small helper: parse integer > 1, else throw
  const parseIntGreaterThanOne = (value, fieldLabel) => {
    const n = Number(value);
    if (!Number.isInteger(n) || n <= 1) {
      throw new Error(`${fieldLabel} must be an integer greater than 1.`);
    }
    return n;
  };

  // Main submit handler: validate, build FormData, POST to backend
  const handleSubmit = async () => {
    if (!bpmnFile || !jsonFile) {
      setError("Please upload both BPMN and JSON files.");
      return;
    }
    if (!selectionValid) {
      setError(
        is_groups
          ? "Please select at least two dimensions for ‘Between groups’."
          : "Please select exactly one dimension for ‘Within group’."
      );
      return;
    }

    let nSamples = null;
    let nTraj = null;
    let nLevels = null;
    let seedInt = null;
    let caseList = null;
    let nReplicationRunsInt = null;

    try {
      // Analysis-type specific numeric validation
      if (analysisType === "Sobol") {
        nSamples = parseIntGreaterThanOne(
          nOfSamples,
          "Number of Samples (Sobol)"
        );
      } else {
        nTraj = parseIntGreaterThanOne(
          nOfTrajectories,
          "Number of Trajectories (Morris)"
        );
        nLevels = parseIntGreaterThanOne(
          nOfLevels,
          "Number of Levels (Morris)"
        );
      }

      // Optional seed
      if (seed.trim() !== "") {
        seedInt = parseIntGreaterThanOne(seed, "Seed");
      }

      // Parse comma-separated case counts
      const rawCases = caseCounts.trim();
      if (!rawCases) {
        throw new Error("Number of Cases cannot be empty.");
      }
      const parts = rawCases.split(",").map((s) => s.trim()).filter(Boolean);
      if (parts.length === 0) {
        throw new Error(
          "Number of Cases must contain at least one positive integer."
        );
      }
      const parsedCases = parts.map((p) => {
        const v = Number(p);
        if (!Number.isInteger(v) || v <= 0) {
          throw new Error(
            "Number of Cases must be a comma-separated list of positive integers (e.g., 100,200,3000)."
          );
        }
        return v;
      });
      caseList = parsedCases;

      // Replication runs
      const rn = Number(nReplicationRuns);
      if (!Number.isInteger(rn) || rn <= 0) {
        throw new Error("Number of Replication Runs must be a positive integer.");
      }
      nReplicationRunsInt = rn;
    } catch (e) {
      setError(e.message || "Invalid sensitivity analysis parameters.");
      return;
    }

    // Build request payload
    setLoading(true);
    setIsRunning(true);
    setError(null);
    setResultMessage(null);

    const formData = new FormData();
    formData.append("bpmn", bpmnFile);
    formData.append("json", jsonFile);

    formData.append("is_sobol", analysisType === "Sobol" ? "true" : "false");
    formData.append("is_groups", is_groups ? "true" : "false");
    Object.entries(flags).forEach(([key, value]) => {
      formData.append(key, value ? "true" : "false");
    });

    if (analysisType === "Sobol") {
      formData.append("n_samples", String(nSamples));
      formData.append("calc_second_order", calcSecondOrder ? "true" : "false");
    } else {
      formData.append("n_trajectories", String(nTraj));
      formData.append("num_levels", String(nLevels));
    }

    formData.append("cases_list", JSON.stringify(caseList));
    formData.append("replication_runs", String(nReplicationRunsInt));

    if (seedInt !== null) {
      formData.append("seed", String(seedInt));
    }

    if (resultsFolder.trim() !== "") {
      formData.append("simulation_results_folder", resultsFolder.trim());
    }

    // Call backend /simulate endpoint
    try {
      const resp = await fetch(`${API_BASE}/simulate`, {
        method: "POST",
        body: formData,
      });

      const text = await resp.text();

      if (!resp.ok) {
        throw new Error(text || "Simulation failed");
      }

      // success path: show message
      setResultMessage(text);
    } catch (e) {
      setError(e.message || "Something went wrong");
    } finally {
      setLoading(false);
      setIsRunning(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* BPMN file input */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          BPMN File
        </label>
        <label className="flex items-center justify-between gap-4 px-4 py-3 border-2 border-dashed border-gray-300 rounded-xl cursor-pointer hover:border-indigo-500 transition">
          <span className="text-sm text-gray-600 truncate">
            {bpmnFile ? bpmnFile.name : "Choose BPMN (.bpmn )"}
          </span>
          <span className="text-xs px-2 py-1 rounded bg-gray-100 text-gray-700">
            Browse
          </span>
          <input
            type="file"
            accept=".bpmn"
            onChange={pickBPMN}
            className="hidden"
          />
        </label>
      </div>

      {/* JSON config input */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          JSON Configuration
        </label>
        <label className="flex items-center justify-between gap-4 px-4 py-3 border-2 border-dashed border-gray-300 rounded-xl cursor-pointer hover:border-indigo-500 transition">
          <span className="text-sm text-gray-600 truncate">
            {jsonFile ? jsonFile.name : "Choose JSON file"}
          </span>
          <span className="text-xs px-2 py-1 rounded bg-gray-100 text-gray-700">
            Browse
          </span>
          <input
            type="file"
            accept=".json"
            onChange={pickJSON}
            className="hidden"
          />
        </label>
      </div>

      {/* Sensitivity method selector */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Sensitivity Analysis Method
        </label>
        <select
          value={analysisType}
          onChange={(e) => setAnalysisType(e.target.value)}
          className="w-full rounded-xl border border-gray-300 bg-white px-4 py-3 text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-indigo-500"
        >
          <option value="Sobol">Sobol (Global Sensitivity Analysis)</option>
          <option value="Morris">Morris (Local Sensitivity Analysis)</option>
        </select>
      </div>

      {/* Sensitivity parameters block (Sobol vs Morris) */}
      <div>
        <h3 className="block text-sm font-medium text-gray-700 mb-2">
          Sensitivity Analysis Parameters
        </h3>

        {analysisType === "Sobol" ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-end">
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1">
                Number of Samples
              </label>
              <input
                type="number"
                min={2}
                step={1}
                value={nOfSamples}
                onChange={(e) => setNOfSamples(e.target.value)}
                className="w-full rounded-xl border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
              <p className="mt-1 text-xs text-gray-500">
                Number of samples must be a power of two.
            </p>
            </div>

            {/* Toggle for Sobol second-order indices */}
            <button
              type="button"
              onClick={() => setCalcSecondOrder((v) => !v)}
              className={[
                "w-full text-left rounded-xl border px-4 py-3 text-sm transition flex items-center justify-between",
                calcSecondOrder
                  ? "border-emerald-300 bg-emerald-50"
                  : "border-gray-300 bg-white hover:border-gray-400",
              ].join(" ")}
              aria-pressed={calcSecondOrder}
            >
              <div>
                <p className="font-medium text-gray-900">
                  Second Order Calculation
                </p>
                <p className="text-xs text-gray-500">
                  Calculate second order interaction
                </p>
              </div>
              <span
                className={[
                  "inline-flex h-6 w-10 items-center rounded-full transition",
                  calcSecondOrder ? "bg-emerald-500" : "bg-gray-300",
                ].join(" ")}
              >
                <span
                  className={[
                    "h-5 w-5 rounded-full bg-white shadow transform transition",
                    calcSecondOrder ? "translate-x-5" : "translate-x-1",
                  ].join(" ")}
                />
              </span>
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1">
                Number of Trajectories (default 30)
              </label>
              <input
                type="number"
                min={2}
                step={1}
                value={nOfTrajectories}
                onChange={(e) => setNOfTrajectories(e.target.value)}
                className="w-full rounded-xl border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1">
                Number of Levels (default 6)
              </label>
              <input
                type="number"
                min={4}
                step={1}
                value={nOfLevels}
                onChange={(e) => setNOfLevels(e.target.value)}
                className="w-full rounded-xl border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>
          </div>
        )}

        {/* Common numeric parameters (cases, replication runs) */}
        <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Number of Cases (comma-separated)
            </label>
            <input
              type="text"
              value={caseCounts}
              onChange={(e) => setCaseCounts(e.target.value)}
              placeholder="e.g. 100 or 100,500,3000"
              className="w-full rounded-xl border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
            <p className="mt-1 text-xs text-gray-500">
              Enter one or more positive integers, separated by commas.
            </p>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Number of Replication Runs
            </label>
            <input
              type="number"
              min={1}
              step={1}
              value={nReplicationRuns}
              onChange={(e) => setNReplicationRuns(e.target.value)}
              className="w-full rounded-xl border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
            <p className="mt-1 text-xs text-gray-500">
              Must be a positive integer.
            </p>
          </div>
        </div>
      </div>

      {/* Optional RNG seed */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Seed for reproducible results
        </label>
        <input
          type="number"
          min={2}
          step={1}
          value={seed}
          onChange={(e) => setSeed(e.target.value)}
          placeholder="Optional. Leave empty for no fixed seed."
          className="w-full rounded-xl border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
        />
        <p className="mt-1 text-xs text-gray-500">
          If provided, the seed must be an integer greater than 1.
        </p>
      </div>

      {/* Analysis scope selector */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Analysis Scope
        </label>
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            onClick={() => handleScopeChange("groups")}
            className={[
              "rounded-xl border px-4 py-3 text-sm transition",
              is_groups
                ? "border-indigo-400 bg-indigo-50 text-indigo-800"
                : "border-gray-300 bg-white hover:border-gray-400",
            ].join(" ")}
            aria-pressed={is_groups}
          >
            Between groups
          </button>
          <button
            type="button"
            onClick={() => handleScopeChange("within")}
            className={[
              "rounded-xl border px-4 py-3 text-sm transition",
              !is_groups
                ? "border-indigo-400 bg-indigo-50 text-indigo-800"
                : "border-gray-300 bg-white hover:border-gray-400",
            ].join(" ")}
            aria-pressed={!is_groups}
          >
            Within group
          </button>
        </div>
        <p className="mt-2 text-xs text-gray-500">
          {is_groups
            ? "Select at least two dimensions to compare parameter groups."
            : "Select exactly one dimension to analyze individual parameters within a parameter group."}
        </p>
      </div>

      {/* Dimension selection tiles */}
      <div>
        <p className="block text-sm font-medium text-gray-700 mb-3">
          Include in Sensitivity
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <ToggleTile
            label="Gateways"
            sub="Analyze how branching probability distributions affect process outcomes"
            active={flags.is_gateway}
            onClick={() => toggle("is_gateway")}
          />
          <ToggleTile
            label="Arrival Distribution"
            sub="Examine sensitivity to inter-arrival time distribution parameters"
            active={flags.is_arrival_distribution}
            onClick={() => toggle("is_arrival_distribution")}
          />
          <ToggleTile
            label="Arrival Calendar"
            sub="Study impact of temporal arrival patterns (weekday/weekend, business hours)"
            active={flags.is_arrival_calendar}
            onClick={() => toggle("is_arrival_calendar")}
          />
          <ToggleTile
            label="Tasks ↔ Resources"
            sub="Test sensitivity of task-resource assignment and processing time distributions"
            active={flags.is_tasks_resources}
            onClick={() => toggle("is_tasks_resources")}
          />
          <ToggleTile
            label="Resource Calendars"
            sub="Evaluate how resource availability schedules influence performance"
            active={flags.is_resource_calendars}
            onClick={() => toggle("is_resource_calendars")}
          />
          <ToggleTile
            label="Resource Numbers"
            sub="Assess impact of varying resource pool sizes on process metrics"
            active={flags.is_resource_numbers}
            onClick={() => toggle("is_resource_numbers")}
          />
        </div>
        {!selectionValid && (
          <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
            {is_groups
              ? "Pick at least two dimensions for ‘Between groups’."
              : "Pick exactly one dimension for ‘Within group’."}
          </div>
        )}
      </div>

      {/* Optional backend folder name for this experiment */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Results Folder Name
        </label>
        <input
          type="text"
          value={resultsFolder}
          onChange={(e) => setResultsFolder(e.target.value)}
          placeholder="Optional. e.g. experiment_01"
          className="w-full rounded-xl border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
        />
        <p className="mt-1 text-xs text-gray-500">
          Leave empty to let the backend choose a default folder name.
        </p>
      </div>

      {/* Run button */}
      <button
        onClick={handleSubmit}
        disabled={loading || !bpmnFile || !jsonFile || !selectionValid}
        className="w-full inline-flex items-center justify-center gap-2 bg-indigo-600 text-white py-3 rounded-xl font-medium hover:bg-indigo-700 disabled:bg-gray-400 transition"
      >
        {loading ? (
          <>
            <span className="w-5 h-5 border-2 border-white/70 border-t-transparent rounded-full animate-spin" />
            Running Simulation…
          </>
        ) : (
          "Run Simulation"
        )}
      </button>

      {/* Error banner */}
      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          <p className="font-semibold mb-1">Error</p>
          <p>{error}</p>
        </div>
      )}

      {/* Result summary banner (PLAIN TEXT) */}
      {resultMessage && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-5">
          <p className="text-emerald-900 font-semibold">✓ Simulation Complete</p>
          <p className="text-gray-800 mt-2 whitespace-pre-line">
            {resultMessage}
          </p>
        </div>
      )}
    </div>
  );
}

/**
 * ToggleTile
 * ----------
 * Small reusable button component representing one SA dimension
 * (e.g., Gateways, Arrival Calendar). Visually behaves like a tile
 * with a label, subtitle, and slider-style on/off indicator.
 */
function ToggleTile({ label, sub, active, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={[
        "w-full text-left rounded-xl border p-4 transition",
        active
          ? "border-emerald-300 bg-emerald-50"
          : "border-gray-300 bg-white hover:border-gray-400",
      ].join(" ")}
      aria-pressed={active}
    >
      <div className="flex items-center justify-between">
        <div>
          <p className="font-medium text-gray-900">{label}</p>
          {sub && <p className="text-xs text-gray-500">{sub}</p>}
        </div>
        <span
          className={[
            "inline-flex h-6 w-10 items-center rounded-full transition",
            active ? "bg-emerald-500" : "bg-gray-300",
          ].join(" ")}
        >
          <span
            className={[
              "h-5 w-5 rounded-full bg-white shadow transform transition",
              active ? "translate-x-5" : "translate-x-1",
            ].join(" ")}
          />
        </span>
      </div>
    </button>
  );
}
