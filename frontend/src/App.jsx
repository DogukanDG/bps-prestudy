import { useState } from "react";
import SamplingAndSimulation from "./SamplingAndSimulation";
import InstructionsPanel from "./InstructionsPanel";
import SensitivityAnalysis from "./SensitivityAnalysis";
import SimodModelDiscovery from "./SimodModelDiscovery";

/**
 * App Component (Main Entry Point)
 * --------------------------------
 * This component serves as the top-level layout and tab controller for the entire
 * BPS Sensitivity Analysis Tool. It provides:
 *
 * 1. A clean UI wrapper and centered content area.
 * 2. A navigation bar with four main tabs:
 *      - Instructions: General guidance for the user.
 *      - SIMOD – Model Discovery: Upload logs & config files to run SIMOD.
 *      - Sampling & Simulation: Upload BPMN/JSON, choose SA parameters, run simulations.
 *      - Sensitivity Analysis: Load outputs and perform Sobol or Morris analysis.
 *
 * 3. A simple state machine using `activeTab` that switches which panel is rendered.
 *
 * No business logic is placed here—this component only handles layout and routing
 * between the major functional panels.
 */

export default function App() {
  // activeTab controls which component is rendered.
  const [activeTab, setActiveTab] = useState("instructions");
  
  // Track loading state across all tabs to prevent tab switching during operations
  const [isRunning, setIsRunning] = useState(false);

  return (
    <div className="min-h-screen grid place-items-center bg-gradient-to-br from-indigo-50 to-blue-100">
      <div className="w-full max-w-6xl px-6">
        <div className="bg-white/90 backdrop-blur rounded-2xl shadow-xl p-10">
          
          {/* ---------- Application Header ---------- */}
          <h1 className="text-3xl font-bold text-gray-900">BPS Sensitivity Analysis Tool</h1>
          <p className="text-gray-600 mt-1 mb-6">
            Discover models with SIMOD from event logs, upload your BPMN and JSON files,
            perform sampling, run simulations and sensitivity analysis.
          </p>

          {/* ---------- Navigation Tabs ---------- */}
          {/* These buttons only modify state; actual content switches below. */}
          <div className="border-b border-gray-200 mb-6">
            <nav className="-mb-px flex flex-wrap gap-2 text-lg font-bold">

              {/* Instructions Tab */}
              <button
                type="button"
                onClick={() => !isRunning && setActiveTab("instructions")}
                disabled={isRunning}
                className={[
                  "pb-2 border-b-2 px-1 transition-opacity",
                  activeTab === "instructions"
                    ? "border-indigo-500 text-indigo-700"
                    : "border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300",
                  isRunning ? "opacity-50 cursor-not-allowed" : "",
                ].join(" ")}
              >
                Instructions
              </button>

              {/* SIMOD Model Discovery Tab */}
              <button
                type="button"
                onClick={() => !isRunning && setActiveTab("simod")}
                disabled={isRunning}
                className={[
                  "pb-2 border-b-2 px-1 transition-opacity",
                  activeTab === "simod"
                    ? "border-indigo-500 text-indigo-700"
                    : "border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300",
                  isRunning ? "opacity-50 cursor-not-allowed" : "",
                ].join(" ")}
              >
                SIMOD – Model Discovery
              </button>

              {/* Sampling & Simulation Tab */}
              <button
                type="button"
                onClick={() => !isRunning && setActiveTab("sampling")}
                disabled={isRunning}
                className={[
                  "pb-2 border-b-2 px-1 transition-opacity",
                  activeTab === "sampling"
                    ? "border-indigo-500 text-indigo-700"
                    : "border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300",
                  isRunning ? "opacity-50 cursor-not-allowed" : "",
                ].join(" ")}
              >
                Sampling &amp; Simulation
              </button>

              {/* Sensitivity Analysis Tab */}
              <button
                type="button"
                onClick={() => !isRunning && setActiveTab("sensitivity")}
                disabled={isRunning}
                className={[
                  "pb-2 border-b-2 px-1 transition-opacity",
                  activeTab === "sensitivity"
                    ? "border-indigo-500 text-indigo-700"
                    : "border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300",
                  isRunning ? "opacity-50 cursor-not-allowed" : "",
                ].join(" ")}
              >
                Sensitivity Analysis
              </button>

            </nav>
          </div>

          {/* ---------- Render Active Panel ---------- */}
          {/* Controlled by activeTab state; no logic here except conditional rendering */}
          <div>
            {activeTab === "instructions" && <InstructionsPanel />}
            {activeTab === "simod" && <SimodModelDiscovery setIsRunning={setIsRunning} />}
            {activeTab === "sampling" && <SamplingAndSimulation setIsRunning={setIsRunning} />}
            {activeTab === "sensitivity" && <SensitivityAnalysis setIsRunning={setIsRunning} />}
          </div>

        </div>
      </div>
    </div>
  );
}
