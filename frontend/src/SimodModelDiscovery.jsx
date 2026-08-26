import { useState } from "react";
const API_BASE = import.meta.env.VITE_API_BASE_URL;

/**
 * SimodModelDiscovery
 * -------------------
 * UI panel for running a full SIMOD model discovery job from the browser.
 *
 * What it does:
 *  - Lets the user upload:
 *      • an event log (CSV)
 *  - Lets the user optionally provide:
 *      • a results folder name
 *  - Sends the file (and optional folder name) in a FormData POST request
 *    to the backend `/simod` endpoint.
 *  - Shows loading state, error messages, and the raw JSON response from
 *    the backend (e.g., output folder paths and metadata).
 *
 * This component **does not** visualize the discovered model; it only
 * triggers the SIMOD run and reports what the backend did.
 */
export default function SimodModelDiscovery({ setIsRunning }) {
  const [eventLogFile, setEventLogFile] = useState(null);

  // Optional user-defined results folder name
  const [resultsFolderName, setResultsFolderName] = useState("");

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [resultMessage, setResultMessage] = useState(null);

  const pickEventLog = (e) => setEventLogFile(e.target.files?.[0] ?? null);

  const handleSubmit = async () => {
    if (!eventLogFile) {
      setError("Please upload event log CSV.");
      return;
    }

    setLoading(true);
    setIsRunning(true);
    setError(null);
    setResultMessage(null);

    const formData = new FormData();
    // 📝 These field names must match what you expect in the backend:
    // e.g. request.files["eventlog_csv"]
    formData.append("train_csv", eventLogFile);

    // 📝 Optional field:
    // Only send results_folder_name if the user actually provided one.
    // Backend should handle the case where it is missing.
    const trimmedFolder = resultsFolderName.trim();
    if (trimmedFolder) {
      formData.append("results_folder_name", trimmedFolder);
    }

    try {
      const resp = await fetch(`${API_BASE}/simod`, {
        method: "POST",
        body: formData,
      });

      const text = await resp.text();

      if (!resp.ok) {
        // backend should return a helpful error string; fall back if empty
        throw new Error(text || "SIMOD model discovery failed.");
      }

      // success
      setResultMessage(text);
    } catch (e) {
      setError(e.message || "Something went wrong while running SIMOD.");
    } finally {
      setLoading(false);
      setIsRunning(false);
    }
  };

  const disabled = loading || !eventLogFile;

  return (
    <div className="space-y-6">
      {/* Title */}
      <div>
        <p className="text-sm text-gray-600 mt-1">
          Upload your event log. Optionally specify a results folder name for the model discovery outputs.
        </p>
      </div>

      {/* event log CSV */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Event Log (CSV)
        </label>
        <label className="flex items-center justify-between gap-4 px-4 py-3 border-2 border-dashed border-gray-300 rounded-xl cursor-pointer hover:border-indigo-500 transition">
          <span className="text-sm text-gray-600 truncate">
            {eventLogFile ? eventLogFile.name : "Choose event log CSV file"}
          </span>
          <span className="text-xs px-2 py-1 rounded bg-gray-100 text-gray-700">
            Browse
          </span>
          <input
            type="file"
            accept=".csv,text/csv"
            onChange={pickEventLog}
            className="hidden"
          />
        </label>
      </div>

      {/* Results folder name (optional) */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Results Folder Name
        </label>
        <input
          type="text"
          value={resultsFolderName}
          onChange={(e) => setResultsFolderName(e.target.value)}
          placeholder="Optional. e.g. simod_run_2025_12_12"
          className="w-full px-4 py-3 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500"
        />
        <p className="mt-1 text-xs text-gray-500">
          Leave empty to let the backend choose a default folder name.
        </p>
      </div>

      {/* Run button */}
      <button
        type="button"
        onClick={handleSubmit}
        disabled={disabled}
        className="w-full inline-flex items-center justify-center gap-2 bg-indigo-600 text-white py-3 rounded-xl font-medium hover:bg-indigo-700 disabled:bg-gray-400 transition"
      >
        {loading ? (
          <>
            <span className="w-5 h-5 border-2 border-white/70 border-t-transparent rounded-full animate-spin" />
            Running SIMOD Model Discovery…
          </>
        ) : (
          "Run SIMOD"
        )}
      </button>

      {/* Error message */}
      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          <p className="font-semibold mb-1">Error</p>
          <p>{error}</p>
        </div>
      )}

      {/* Result block (PLAIN TEXT) */}
      {resultMessage && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900 space-y-1">
          <p className="font-semibold">✓ SIMOD run completed</p>
          <p className="mt-2 whitespace-pre-line">{resultMessage}</p>
        </div>
      )}
    </div>
  );
}
